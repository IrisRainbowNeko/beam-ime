// SPDX-License-Identifier: Apache-2.0
#include "runtime.hpp"
#include "engine.hpp"
#include <array>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#include <spawn.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>
extern char ** environ;
#endif

namespace beam {
namespace {
using Clock = std::chrono::steady_clock;
uint64_t now_seconds() {
    return (uint64_t)std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}
Json read_json(const std::filesystem::path & path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("cannot read " + path.u8string());
    return Json::parse(stream);
}
void write_json(const std::filesystem::path & path, const Json & value) {
    auto temporary = path; temporary += ".tmp";
    { std::ofstream stream(temporary); stream << value.dump(2) << '\n'; stream.close();
      if (!stream) throw std::runtime_error("cannot write " + temporary.u8string()); }
#ifdef _WIN32
    if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("cannot replace learning state");
#else
    std::filesystem::rename(temporary, path);
#endif
}

std::string sha256(const std::filesystem::path & path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("cannot fingerprint model");
    std::array<unsigned char, 32> digest{};
    std::array<char, 1024 * 64> buffer{};
#ifdef _WIN32
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0) throw std::runtime_error("SHA256 unavailable");
    if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) != 0) { BCryptCloseAlgorithmProvider(algorithm, 0); throw std::runtime_error("SHA256 init failed"); }
    bool ok = true;
    while (stream.read(buffer.data(), buffer.size()) || stream.gcount())
        if (BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), (ULONG)stream.gcount(), 0) != 0) { ok = false; break; }
    ok = ok && BCryptFinishHash(hash, digest.data(), (ULONG)digest.size(), 0) == 0;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
#else
    EVP_MD_CTX * hash = EVP_MD_CTX_new();
    if (!hash) throw std::runtime_error("SHA256 init failed");
    bool ok = EVP_DigestInit_ex(hash, EVP_sha256(), nullptr) == 1;
    while (stream.read(buffer.data(), buffer.size()) || stream.gcount())
        if (EVP_DigestUpdate(hash, buffer.data(), (size_t)stream.gcount()) != 1) { ok = false; break; }
    ok = ok && EVP_DigestFinal_ex(hash, digest.data(), nullptr) == 1;
    EVP_MD_CTX_free(hash);
#endif
    if (!ok || stream.bad()) throw std::runtime_error("model fingerprint failed");
    std::ostringstream result;
    for (auto byte : digest) result << std::hex << std::setw(2) << std::setfill('0') << (unsigned)byte;
    return result.str();
}

bool on_power() {
#ifdef _WIN32
    SYSTEM_POWER_STATUS status{};
    if (!GetSystemPowerStatus(&status)) throw std::runtime_error("cannot read power status");
    return status.BatteryFlag == 128 || status.ACLineStatus == 1;
#else
    const std::filesystem::path root("/sys/class/power_supply");
    if (!std::filesystem::exists(root)) return true;
    for (const auto & item : std::filesystem::directory_iterator(root)) {
        std::string type, status;
        std::ifstream(item.path() / "type") >> type;
        if (type != "Battery") continue;
        std::ifstream(item.path() / "status") >> status;
        if (status != "Charging" && status != "Full" && status != "Not") return false;
    }
    return true;
#endif
}

class Child {
 public:
    ~Child() {
        if (!running()) return;
#ifdef _WIN32
        TerminateProcess(handle_, 1); WaitForSingleObject(handle_, INFINITE); CloseHandle(handle_);
#else
        kill(pid_, SIGTERM); while (waitpid(pid_, nullptr, 0) < 0 && errno == EINTR) {}
#endif
    }
    bool running() const {
#ifdef _WIN32
        return handle_ != nullptr;
#else
        return pid_ > 0;
#endif
    }
    void start(const std::vector<std::string> & args, const std::filesystem::path & log) {
#ifdef _WIN32
        std::wstring command;
        for (const auto & arg : args) {
            std::wstring value = std::filesystem::u8path(arg).wstring();
            command += L"\"";
            size_t slashes = 0;
            for (wchar_t ch : value) {
                if (ch == L'\\') { ++slashes; continue; }
                command.append(slashes * (ch == L'"' ? 2 : 1), L'\\'); slashes = 0;
                if (ch == L'"') command += L'\\';
                command += ch;
            }
            command.append(slashes * 2, L'\\'); command += L"\" ";
        }
        SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
        HANDLE output = CreateFileW(log.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &security, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (output == INVALID_HANDLE_VALUE) throw std::runtime_error("cannot create trainer log");
        STARTUPINFOW startup{}; startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdOutput = startup.hStdError = output;
        startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        PROCESS_INFORMATION process{};
        BOOL created = CreateProcessW(nullptr, command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS,
                                      nullptr, nullptr, &startup, &process);
        CloseHandle(output);
        if (!created) throw std::runtime_error("cannot start trainer");
        handle_ = process.hProcess; CloseHandle(process.hThread);
#else
        std::vector<char *> argv;
        for (const auto & arg : args) argv.push_back(const_cast<char *>(arg.c_str()));
        argv.push_back(nullptr);
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
        posix_spawn_file_actions_adddup2(&actions, STDOUT_FILENO, STDERR_FILENO);
        int error = posix_spawn(&pid_, argv[0], &actions, nullptr, argv.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        if (error) { pid_ = -1; throw std::runtime_error("cannot start trainer: " + std::to_string(error)); }
#endif
    }
    int poll() {
#ifdef _WIN32
        if (!handle_ || WaitForSingleObject(handle_, 0) != WAIT_OBJECT_0) return -1;
        DWORD code = 1; GetExitCodeProcess(handle_, &code); CloseHandle(handle_); handle_ = nullptr;
        return (int)code;
#else
        if (pid_ <= 0) return -1;
        int status;
        auto done = waitpid(pid_, &status, WNOHANG);
        if (!done || (done < 0 && errno == EINTR)) return -1;
        if (done < 0) throw std::runtime_error("cannot wait for trainer");
        pid_ = -1;
        return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
#endif
    }
 private:
#ifdef _WIN32
    HANDLE handle_ = nullptr;
#else
    pid_t pid_ = -1;
#endif
};
}

struct LearningRuntime::Impl {
    LearningStore & store;
    Engine & engine;
    std::string fingerprint, model_path, applied, failed_adapter;
    std::map<std::string, uint64_t> compositions;
    Clock::time_point last_activity = Clock::now(), last_poll = Clock::now();
    Child child;
    bool force = false, reset_pending = false;
    Json job;

    Impl(LearningStore & s, Engine & e, const std::string & model) : store(s), engine(e), fingerprint(sha256(std::filesystem::u8path(model))),
        model_path(std::filesystem::absolute(std::filesystem::u8path(model)).u8string()) {
        auto current = store.setting("active_adapter", Json::object());
        if (current.value("base_sha256", "") != fingerprint) store.set("active_adapter", store.setting("adapter_" + fingerprint, Json::object()));
        auto pending = store.setting("pending_adapter", Json::object());
        if (!pending.empty() && pending.value("base_sha256", "") != fingerprint) store.set("pending_adapter", Json::object());
        store.set("previous_adapter", store.setting("previous_" + fingerprint, Json::object()));
        auto saved = store.setting("job", Json::object());
        if (!saved.empty() && saved.value("base_sha256", "") == fingerprint) job = saved;
        auto training = store.setting("training", Json::object());
        if (training.value("state", "") == "running" || training.value("state", "") == "pausing")
            store.set("training", {{"state", "paused"}, {"reason", "service_restarted"}});
    }
    void pause() {
        if (child.running() && !job.empty()) {
            write_json(std::filesystem::u8path(job.at("directory").get<std::string>()) / "pause.json", true);
            store.set("training", {{"state", "pausing"}});
        }
    }
    void clear() {
        std::string error;
        if (!engine.set_adapter("", error)) throw std::runtime_error(error);
        applied.clear(); failed_adapter.clear();
        store.reset(); job = Json(); reset_pending = false;
        for (auto name : {"jobs", "generations", "teacher-cache"}) std::filesystem::remove_all(store.directory() / name);
    }
    void publish(const Json & manifest) {
        auto component = store.setting("trainer", Json::object());
        if (manifest.value("base_sha256", "") != fingerprint || manifest.value("prompt_version", "") != "keys_llm_v1" ||
            manifest.value("tokenizer", "") != "gguf-embedded" || manifest.value("rank", 0) != 8 ||
            manifest.value("alpha", 0) != 16 || manifest.value("recipe", "") != component.value("recipe", ""))
            throw std::runtime_error("adapter metadata does not match this model and trainer");
        store.set("pending_adapter", manifest);
    }
    void tick() {
        if (child.running()) {
            int code = child.poll();
            auto directory = std::filesystem::u8path(job.at("directory").get<std::string>());
            if (code >= 0) {
                if (code == 0 && !reset_pending) {
                    auto manifest = read_json(directory / "result.json");
                    publish(manifest);
                    store.set("training", {{"state", "awaiting_activation"}, {"step", 24}});
                    store.set("last_success_" + fingerprint, now_seconds());
                    store.set("trained_clock_" + fingerprint, job.at("clock"));
                    store.set("job", Json::object()); job = Json();
                    std::filesystem::remove_all(directory);
                } else if (code == 75) store.set("training", {{"state", "paused"}, {"reason", "input_activity"}});
                else {
                    store.set("training", {{"state", "error"}, {"exit_code", code}});
                    const auto error = directory / "error.json";
                    store.set("last_error", std::filesystem::exists(error) ? read_json(error).at("error") : Json("trainer failed; see the local trainer.log"));
                }
            } else {
                if (!store.collecting() || !on_power() || !compositions.empty()) pause();
                auto progress = directory / "progress.jsonl";
                if (std::filesystem::exists(progress) && store.setting("training", Json::object()).value("state", "") != "pausing") {
                    std::ifstream stream(progress);
                    if (!stream) throw std::runtime_error("cannot read training progress");
                    std::string line, complete;
                    // Only consume complete records while the trainer appends its next update.
                    while (std::getline(stream, line)) if (!stream.eof()) complete = line;
                    if (stream.bad()) throw std::runtime_error("cannot read training progress");
                    if (!complete.empty()) store.set("training", Json::parse(complete));
                }
            }
        }
        if (reset_pending && !child.running() && compositions.empty()) { clear(); return; }
        if (compositions.empty()) {
            auto pending = store.setting("pending_adapter", Json::object());
            auto current = store.setting("active_adapter", Json::object());
            auto wanted = pending.empty() ? current : pending;
            std::string path = store.enabled() ? wanted.value("path", "") : "";
            if (path != applied && (failed_adapter.empty() || path != failed_adapter)) {
                std::string error;
                if (!engine.set_adapter(path, error)) { failed_adapter = path; store.set("last_error", error); }
                else {
                    applied = path; failed_adapter.clear(); store.changed();
                    if (store.enabled() && !pending.empty()) {
                        store.set("previous_adapter", current); store.set("active_adapter", pending);
                        store.set("previous_" + fingerprint, current);
                        store.set("adapter_" + fingerprint, pending); store.set("pending_adapter", Json::object());
                        store.set("training", {{"state", "idle"}});
                        auto generations = store.directory() / "generations";
                        if (std::filesystem::exists(generations)) for (const auto & item : std::filesystem::directory_iterator(generations)) {
                            if (!item.is_directory() || !std::filesystem::exists(item.path() / "manifest.json")) continue;
                            auto saved = read_json(item.path() / "manifest.json");
                            if (saved.value("base_sha256", "") == fingerprint && saved.value("id", "") != pending.value("id", "") &&
                                saved.value("id", "") != current.value("id", "")) std::filesystem::remove_all(item.path());
                        }
                    }
                }
            }
        }
        if (child.running() || reset_pending || !store.collecting() || !compositions.empty()) return;
        auto component = store.setting("trainer", Json::object());
        if (component.empty()) return;
        if (component.value("schemaVersion", 0) != 2) {
            store.set("training", {{"state", "component_update_required"}}); return;
        }
        if (component.value("base_sha256", "") != fingerprint) { store.set("training", {{"state", "model_mismatch"}}); return; }
        if (store.setting("training", Json::object()).value("state", "") == "error") return;
        if (!store.setting("pending_adapter", Json::object()).empty()) return;
        uint64_t clock = store.setting("clock", uint64_t(0));
        uint64_t learned = store.setting("trained_clock_" + fingerprint, uint64_t(0));
        if (!force && (Clock::now() - last_activity < std::chrono::minutes(5) ||
                       (job.empty() && (clock < learned + 64 || now_seconds() < store.setting("last_success_" + fingerprint, uint64_t(0)).get<uint64_t>() + 14400)))) return;
        if (!on_power() || clock == 0) return;
        if (job.empty()) {
            std::string id = std::to_string(now_seconds()) + "-" + std::to_string(store.revision());
            auto directory = store.directory() / "jobs" / id;
            std::filesystem::create_directories(directory);
            job = store.snapshot(); job["id"] = id; job["directory"] = directory.u8string();
            job["base_sha256"] = fingerprint; job["model"] = model_path; job["component"] = component;
            job["previous"] = store.setting("active_adapter", Json::object());
            write_json(directory / "job.json", job); store.set("job", job);
        }
        auto directory = std::filesystem::u8path(job.at("directory").get<std::string>());
        std::filesystem::remove(directory / "pause.json");
        std::filesystem::remove(directory / "progress.jsonl");
        std::filesystem::remove(directory / "error.json");
        child.start({component.at("executable"), "--job", (directory / "job.json").u8string()}, directory / "trainer.log");
        force = false; store.set("training", {{"state", "running"}, {"step", 0}, {"total", 24}}); store.set("last_error", "");
    }
};

LearningRuntime::LearningRuntime(LearningStore & store, Engine & engine, const std::string & model)
    : impl_(std::make_unique<Impl>(store, engine, model)) {}
LearningRuntime::~LearningRuntime() {
    try {
        impl_->pause();
        for (int i = 0; i < 100 && impl_->child.running(); ++i) {
            if (impl_->child.poll() >= 0) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    } catch (const std::exception & error) { fprintf(stderr, "learning shutdown: %s\n", error.what()); }
}
void LearningRuntime::activity() { impl_->last_activity = Clock::now(); impl_->pause(); }
void LearningRuntime::composition(const Json & request, uint64_t owner) {
    auto & m = *impl_;
    std::string key = request.at("session").get<std::string>() + ":" + request.at("composition").get<std::string>();
    std::string state = request.at("state");
    if (state == "begin" || state == "activity") { m.compositions[key] = owner; activity(); }
    else m.compositions.erase(key);
    if (request.contains("client_errors")) m.store.set("client_errors", m.store.setting("client_errors", uint64_t(0)).get<uint64_t>() + request["client_errors"].get<uint64_t>());
}
void LearningRuntime::disconnect(uint64_t owner) {
    for (auto it = impl_->compositions.begin(); it != impl_->compositions.end();)
        if (it->second == owner) it = impl_->compositions.erase(it); else ++it;
}
Json LearningRuntime::status() const {
    auto result = impl_->store.status(); result["base_sha256"] = impl_->fingerprint;
    result["active_compositions"] = impl_->compositions.size(); result["adapter_loaded"] = !impl_->applied.empty();
    return result;
}
void LearningRuntime::tick() {
    if (Clock::now() - impl_->last_poll < std::chrono::milliseconds(200)) return;
    impl_->last_poll = Clock::now();
    try { impl_->tick(); }
    catch (const std::exception & error) {
        impl_->store.set("last_error", error.what()); impl_->store.set("training", {{"state", "error"}});
    }
}
Json LearningRuntime::command(const Json & request) {
    auto & m = *impl_; auto & store = m.store;
    std::string action = request.at("action");
    if (action == "status") return status();
    if (action == "enable" || action == "resume") { store.set("enabled", true); store.set("paused", false); m.failed_adapter.clear(); }
    else if (action == "disable") { store.set("enabled", false); m.pause(); }
    else if (action == "pause") { store.set("paused", true); m.pause(); }
    else if (action == "train") {
        if (!store.collecting()) throw std::runtime_error("enable or resume learning first");
        if (store.setting("trainer", Json::object()).empty()) throw std::runtime_error("install the learning component first");
        m.force = true;
    } else if (action == "reset") {
        if (!request.value("confirm", false)) throw std::runtime_error("reset requires confirm=true");
        store.set("enabled", false); m.reset_pending = true; m.pause();
    } else if (action == "rollback") {
        if (m.child.running()) throw std::runtime_error("pause training before rollback");
        auto previous = store.setting("previous_adapter", Json::object());
        if (previous.empty() || previous.value("base_sha256", "") != m.fingerprint) throw std::runtime_error("no previous adapter for this model");
        store.set("pending_adapter", previous); m.failed_adapter.clear();
        store.set("trained_clock_" + m.fingerprint, previous.value("clock", uint64_t(0)));
        store.set("job", Json::object()); m.job = Json();
    } else if (action == "install") {
        if (m.child.running()) throw std::runtime_error("pause training before installing a component");
        auto path = std::filesystem::absolute(std::filesystem::u8path(request.at("manifest").get<std::string>()));
        auto component = read_json(path);
        if (component.value("schemaVersion", 0) != 2 || component.value("base_sha256", "") != m.fingerprint ||
            component.value("prompt_version", "") != "keys_llm_v1" || component.value("tokenizer", "") != "gguf-embedded" ||
            component.value("recipe", "") != "beam-personal-r8-qvac-v1" ||
            (component.value("backend", "") != "cpu" && component.value("backend", "") != "vulkan"))
            throw std::runtime_error("learning component is incompatible with this model");
        for (auto field : {"executable", "replay", "pinyin"}) {
            auto target = std::filesystem::weakly_canonical(path.parent_path() / std::filesystem::u8path(component.at(field).get<std::string>()));
            if (!std::filesystem::exists(target)) throw std::runtime_error(std::string("missing component file: ") + field);
            component[field] = target.u8string();
        }
        // A replacement runtime starts a new snapshot with the active adapter's state.
        if (!m.job.empty()) {
            store.set("job", Json::object());
            std::filesystem::remove_all(std::filesystem::u8path(m.job.at("directory").get<std::string>()));
            m.job = Json();
        }
        store.set("trainer", component);
    } else throw std::runtime_error("unknown learning action");
    if (action == "train" || action == "resume" || action == "enable" || action == "install") {
        store.set("last_error", ""); store.set("training", {{"state", store.setting("trainer", Json::object()).empty() ? "not_installed" : "idle"}});
    }
    store.changed(); m.tick(); return status();
}
}
