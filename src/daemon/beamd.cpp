// beamd: local Beam IME engine service.
//
// Listens on a user-private local endpoint (engine/net.hpp) and answers JSON-lines requests:
//   {"id":1,"op":"health"}
//     -> {"id":1,"ok":true,"model":"...","protocol":1}
//   {"id":2,"op":"query","keys":"nhsj","context":"上文","max":5,"beam_ms":150}
//     -> {"id":2,"ok":true,"candidates":["你好世界",...],"greedy_ms":21.3,"beam_ms":48.0,"beam_complete":true}
// On Windows every request also carries "token" from the endpoint file.
// Requests are served one at a time; if a client queued several queries, only its newest one is
// answered (the others are stale keystrokes). Typed keys and text are logged only with
// --debug-text (for troubleshooting; the log then contains what you typed).
// Exit codes: 0 stopped, 1 model failed to load, 2 usage, 4 another beamd is running.
// On Windows `beamd --supervise ARGS` runs `beamd ARGS` as a child and restarts it when it
// crashes; after a crash the child is started CPU-only (--ngl 0), since a broken GPU driver is
// the likely cause and would crash it again.

#include "engine.hpp"
#include "net.hpp"
#include "protocol.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <chrono>
#include <filesystem>

#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

using json = nlohmann::json;

namespace {

constexpr int kProtocol = 1;
constexpr size_t kMaxLine = 64 * 1024;

struct Client {
    beam::net::Socket socket;
    std::string buffer;
};

bool send_line(beam::net::Socket s, const json & reply) {
    std::string line = reply.dump(-1, ' ', false, json::error_handler_t::replace) + "\n";
    size_t off = 0;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
    while (off < line.size()) {
        if (std::chrono::steady_clock::now() >= deadline) return false;
        long n = beam::net::send_some(s, line.data() + off, line.size() - off);
        if (n > 0) { off += (size_t) n; continue; }
        if (n == -2 && beam::net::wait(s, 2, 1000) == 1) continue;
        return false;
    }
    return true;
}

volatile std::sig_atomic_t stopping = 0;

void usage() {
    fprintf(stderr,
            "usage: beamd --model MODEL.gguf [--socket PATH] [--ngl 99] [--threads N] [--beam-ms 150]\n"
            "             [--log FILE] [--verbose] [--debug-text]%s\n",
#ifdef _WIN32
            " [--supervise]"
#else
            ""
#endif
            );
}

bool redirect_log(const std::string & path) {
#ifdef _WIN32
    int n = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, w.data(), n);
    return _wfreopen(w.c_str(), L"a", stderr) != nullptr;
#else
    return freopen(path.c_str(), "a", stderr) != nullptr;
#endif
}

}  // namespace

int beamd_main(int argc, char ** argv) {
    beam::EngineOptions options;
    std::string socket_path, log_path;
    int beam_ms = 150;
    bool verbose = false, debug_text = false;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto value = [&]() -> std::string {
            if (i + 1 >= argc) { usage(); exit(2); }
            return argv[++i];
        };
        if (a == "--model") options.model_path = value();
        else if (a == "--version") { printf("%s\n", BEAM_VERSION); return 0; }
        else if (a == "--backend-dir") options.backend_dir = value();
        else if (a == "--socket") socket_path = value();
        else if (a == "--ngl") options.gpu_layers = std::atoi(value().c_str());
        else if (a == "--threads") options.threads = std::atoi(value().c_str());
        else if (a == "--beam-ms") beam_ms = std::atoi(value().c_str());
        else if (a == "--log") log_path = value();
        else if (a == "--verbose") verbose = true;
        else if (a == "--debug-text") verbose = debug_text = true;
        else { usage(); return 2; }
    }
    if (!log_path.empty() && !redirect_log(log_path)) fprintf(stderr, "cannot open log %s\n", log_path.c_str());
    setvbuf(stderr, nullptr, _IONBF, 0);
    if (options.model_path.empty()) { usage(); return 2; }
    if (options.backend_dir.empty()) {
        std::filesystem::path executable;
#ifdef _WIN32
        wchar_t module[32768];
        DWORD length = GetModuleFileNameW(nullptr, module, 32768);
        executable = std::filesystem::path(std::wstring(module, length));
#else
        std::error_code ec;
        executable = std::filesystem::read_symlink("/proc/self/exe", ec);
        if (ec) executable = std::filesystem::absolute(argv[0]);
#endif
        auto dir = executable.parent_path();
        auto installed = dir / "../lib/beam-ime";
        options.backend_dir = std::filesystem::exists(installed) ? installed.u8string() : dir.u8string();
    }

#ifndef _WIN32
    std::signal(SIGPIPE, SIG_IGN);
#endif
    std::signal(SIGINT, [](int) { stopping = 1; });
    std::signal(SIGTERM, [](int) { stopping = 1; });

    beam::net::startup();
    beam::net::Listener listener;
    std::string error;
    if (!beam::net::listen_local(listener, error, socket_path)) { fprintf(stderr, "beamd: %s\n", error.c_str()); return 4; }
    beam::Engine engine(options);
    if (!engine.ok()) {
        fprintf(stderr, "beamd: failed to load %s\n", options.model_path.c_str());
        beam::net::remove_endpoint(listener);
        return 1;
    }
    fprintf(stderr, "beamd: %s on %s\n", engine.model_name().c_str(), listener.location.c_str());

    std::vector<Client> clients;
    std::vector<beam::net::Socket> sockets;
    std::vector<char> ready;
    while (!stopping) {
        sockets = {listener.socket};
        for (auto & c : clients) sockets.push_back(c.socket);
        // A finite timeout so SIGTERM/Ctrl-C is noticed even where signals do not interrupt poll.
        if (beam::net::wait_readable(sockets, ready, 1000) < 0) { fprintf(stderr, "beamd: poll failed\n"); break; }
        if (ready[0]) {
            beam::net::Socket s = beam::net::accept_local(listener);
            if (s != beam::net::kInvalid) {
                if (clients.size() >= 64) beam::net::close_socket(s);
                else clients.push_back({s, {}});
            }
        }
        for (size_t k = 1; k < sockets.size(); ++k) {
            if (!ready[k]) continue;
            Client & c = clients[k - 1];
            char buf[8192];
            long n = beam::net::recv_some(c.socket, buf, sizeof(buf));
            if (n == -2) continue;
            if (n <= 0) { beam::net::close_socket(c.socket); c.socket = beam::net::kInvalid; continue; }
            c.buffer.append(buf, (size_t) n);
            if (c.buffer.size() > kMaxLine) {
                beam::net::close_socket(c.socket);
                c.socket = beam::net::kInvalid;
                continue;
            }

            // Parse all complete lines; answer health checks in order and only the newest query.
            std::vector<json> requests;
            size_t nl;
            while ((nl = c.buffer.find('\n')) != std::string::npos) {
                std::string line = c.buffer.substr(0, nl);
                c.buffer.erase(0, nl + 1);
                json req = json::parse(line, nullptr, false);
                std::string invalid = req.is_discarded() ? "bad_request" : beam::validate_request(req, listener.token);
                if (!invalid.empty()) {
                    json id = req.is_object() && req.contains("id") && req["id"].is_number_unsigned() ? req["id"] : json(nullptr);
                    send_line(c.socket, {{"id", id}, {"ok", false}, {"error", invalid}});
                    continue;
                }
                requests.push_back(std::move(req));
            }
            int newest_query = -1;
            for (int r = 0; r < (int) requests.size(); ++r)
                if (requests[r].value("op", "") == "query") newest_query = r;
            for (int r = 0; r < (int) requests.size(); ++r) {
                const json & req = requests[r];
                std::string op = req.value("op", "");
                json reply = {{"id", req.contains("id") ? req["id"] : json(nullptr)}};
                if (!listener.token.empty() && req.value("token", "") != listener.token) {
                    reply["ok"] = false;
                    reply["error"] = "bad_token";
                } else if (op == "health") {
                    reply["ok"] = true;
                    reply["model"] = engine.model_name();
                    reply["protocol"] = kProtocol;
                    reply["version"] = BEAM_VERSION;
                    reply["backend"] = engine.backend_name();
                } else if (op == "query") {
                    if (r != newest_query) continue;
                    beam::QueryOptions q;
                    q.max_candidates = std::clamp(req.value("max", 5), 1, 10);
                    q.beam_budget_ms = std::clamp(req.value("beam_ms", beam_ms), 0, 2000);
                    std::string keys = req.value("keys", "");
                    std::string context = req.value("context", "");
                    beam::QueryResult res;
                    try { res = engine.query(keys, context, q); }
                    catch (const std::exception &) {
                        reply["ok"] = false;
                        reply["error"] = "model_error";
                        send_line(c.socket, reply);
                        continue;
                    }
                    reply["ok"] = true;
                    reply["candidates"] = res.candidates;
                    reply["greedy_ms"] = res.greedy_ms;
                    reply["beam_ms"] = res.beam_ms;
                    reply["beam_complete"] = res.beam_complete;
                    if (verbose)
                        fprintf(stderr, "query letters=%zu context=%s greedy=%.1fms beam=%.1fms%s\n", keys.size(),
                                context.empty() ? "no" : "yes", res.greedy_ms, res.beam_ms,
                                res.beam_complete ? "" : " (cut)");
                    if (debug_text)
                        fprintf(stderr, "  keys=%s context=%s -> %s\n", keys.c_str(), context.c_str(),
                                res.candidates.empty() ? "" : res.candidates[0].c_str());
                } else {
                    reply["ok"] = false;
                    reply["error"] = "unknown_op";
                }
                if (!send_line(c.socket, reply)) {
                    beam::net::close_socket(c.socket);
                    c.socket = beam::net::kInvalid;
                    break;
                }
            }
        }
        clients.erase(std::remove_if(clients.begin(), clients.end(),
                                     [](const Client & c) { return c.socket == beam::net::kInvalid; }),
                      clients.end());
    }
    for (auto & c : clients) beam::net::close_socket(c.socket);
    beam::net::close_socket(listener.socket);
    beam::net::remove_endpoint(listener);
    return 0;
}

#ifdef _WIN32
namespace {

std::wstring quote_arg(const std::wstring & a) {
    if (!a.empty() && a.find_first_of(L" \t\"") == std::wstring::npos) return a;
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t c : a) {
        if (c == L'\\') { ++slashes; continue; }
        out.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        slashes = 0;
        out.push_back(c);
    }
    out.append(slashes * 2, L'\\');
    return out + L"\"";
}

// Keep a child beamd running; see the header comment.
int supervise(int argc, wchar_t ** wargv) {
    std::vector<std::wstring> args;
    std::wstring log;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = wargv[i];
        if (a == L"--supervise") continue;
        if (a == L"--log" && i + 1 < argc) log = wargv[i + 1];
        args.push_back(a);
    }
    if (!log.empty()) { _wfreopen(log.c_str(), L"a", stderr); setvbuf(stderr, nullptr, _IONBF, 0); }
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    // The child dies with the supervisor (logoff, uninstall killing the supervisor).
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limit = {};
    limit.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limit, sizeof(limit));
    bool cpu_only = false;
    int crashes = 0;
    ULONGLONG window_start = GetTickCount64();
    for (;;) {
        std::wstring cmd = quote_arg(exe);
        for (auto & a : args) cmd += L" " + quote_arg(a);
        if (cpu_only) cmd += L" --ngl 0";
        STARTUPINFOW si = {sizeof(si)};
        PROCESS_INFORMATION pi = {};
        if (!CreateProcessW(exe, cmd.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW | CREATE_SUSPENDED,
                            nullptr, nullptr, &si, &pi)) {
            fprintf(stderr, "beamd: cannot start engine process (%lu)\n", GetLastError());
            return 1;
        }
        AssignProcessToJobObject(job, pi.hProcess);
        ResumeThread(pi.hThread);
        WaitForSingleObject(pi.hProcess, INFINITE);
        DWORD code = 1;
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        if (code == 0 || code == 2 || code == 4) return (int) code;
        if (GetTickCount64() - window_start > 10 * 60 * 1000) { window_start = GetTickCount64(); crashes = 0; }
        if (++crashes > 5) { fprintf(stderr, "beamd: engine keeps failing (exit 0x%lx); giving up\n", code); return 1; }
        if (!cpu_only) fprintf(stderr, "beamd: engine exited with 0x%lx; restarting on the CPU\n", code);
        else fprintf(stderr, "beamd: engine exited with 0x%lx; restarting\n", code);
        cpu_only = true;
        Sleep(1000);
    }
}

}  // namespace

// Wide entry point: arguments (a model path under a non-ASCII user profile) arrive as UTF-16 and
// are passed on as UTF-8, which llama.cpp's file loader expects.
int wmain(int argc, wchar_t ** wargv) {
    for (int i = 1; i < argc; ++i)
        if (std::wstring(wargv[i]) == L"--supervise") return supervise(argc, wargv);
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, nullptr, 0, nullptr, nullptr);
        std::string s(n > 0 ? n - 1 : 0, '\0');
        WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, s.data(), n, nullptr, nullptr);
        args.push_back(s);
    }
    std::vector<char *> argv;
    for (auto & s : args) argv.push_back(s.data());
    return beamd_main(argc, argv.data());
}
#else
int main(int argc, char ** argv) { return beamd_main(argc, argv); }
#endif
