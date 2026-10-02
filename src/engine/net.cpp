#include "net.hpp"

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#else
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>
#include <filesystem>
#endif

namespace beam::net {

#ifdef _WIN32
const Socket kInvalid = (Socket) INVALID_SOCKET;

namespace {

std::string utf8(const std::wstring & w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int) w.size(), nullptr, 0, nullptr, nullptr);
    std::string out(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int) w.size(), out.data(), n, nullptr, nullptr);
    return out;
}

std::wstring endpoint_dir() {
    const wchar_t * base = _wgetenv(L"LOCALAPPDATA");
    std::wstring dir = std::wstring(base && *base ? base : L".") + L"\\beam-ime";
    CreateDirectoryW(dir.c_str(), nullptr);
    return dir;
}

std::wstring endpoint_file() { return endpoint_dir() + L"\\beamd.endpoint"; }

bool read_endpoint(int & port, std::string & token) {
    FILE * f = _wfopen(endpoint_file().c_str(), L"rb");
    if (!f) return false;
    char buf[512];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    auto j = nlohmann::json::parse(std::string(buf, n), nullptr, false);
    if (!j.is_object() || !j.contains("port") || !j["port"].is_number_unsigned() ||
        !j.contains("token") || !j["token"].is_string()) return false;
    if (j["port"].get<double>() > 65535) return false;
    port = j.value("port", 0);
    token = j.value("token", "");
    return port > 0 && !token.empty();
}

Socket connect_port(int port, int timeout_ms) {
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return kInvalid;
    BOOL nodelay = TRUE;
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *) &nodelay, sizeof(nodelay));
    set_nonblocking((Socket) s);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons((u_short) port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(s, (sockaddr *) &addr, sizeof(addr)) != 0) {
        if (WSAGetLastError() != WSAEWOULDBLOCK || wait((Socket) s, 2, timeout_ms) != 1) {
            closesocket(s);
            return kInvalid;
        }
        int err = 0, len = sizeof(err);
        if (getsockopt(s, SOL_SOCKET, SO_ERROR, (char *) &err, &len) != 0 || err != 0) {
            closesocket(s);
            return kInvalid;
        }
    }
    return (Socket) s;
}

}  // namespace

bool startup() {
    static bool ok = [] {
        WSADATA data;
        return WSAStartup(MAKEWORD(2, 2), &data) == 0;
    }();
    return ok;
}

void close_socket(Socket s) { if (s != kInvalid) closesocket((SOCKET) s); }

bool set_nonblocking(Socket s) {
    u_long on = 1;
    return ioctlsocket((SOCKET) s, FIONBIO, &on) == 0;
}

int wait(Socket s, int events, int timeout_ms) {
    WSAPOLLFD p{(SOCKET) s, (SHORT) (events == 1 ? POLLRDNORM : POLLWRNORM), 0};
    int r = WSAPoll(&p, 1, timeout_ms);
    if (r < 0) return -1;
    if (r == 0) return 0;
    return 1;  // readable/writable, or an error/hangup the next call will report
}

int wait_readable(const std::vector<Socket> & sockets, std::vector<char> & ready, int timeout_ms) {
    std::vector<WSAPOLLFD> fds;
    for (auto s : sockets) fds.push_back({(SOCKET) s, POLLRDNORM, 0});
    int r = WSAPoll(fds.data(), (ULONG) fds.size(), timeout_ms);
    ready.assign(sockets.size(), 0);
    if (r < 0) return -1;
    for (size_t i = 0; i < fds.size(); ++i) ready[i] = fds[i].revents != 0;
    return r;
}

long send_some(Socket s, const char * data, std::size_t size) {
    int n = send((SOCKET) s, data, (int) size, 0);
    if (n >= 0) return n;
    return WSAGetLastError() == WSAEWOULDBLOCK ? -2 : -1;
}

long recv_some(Socket s, char * data, std::size_t size) {
    int n = recv((SOCKET) s, data, (int) size, 0);
    if (n >= 0) return n;
    return WSAGetLastError() == WSAEWOULDBLOCK ? -2 : -1;
}

std::string default_location() { return utf8(endpoint_file()); }

bool listen_local(Listener & out, std::string & error, const std::string &) {
    startup();
    int port = 0;
    std::string token;
    if (read_endpoint(port, token)) {
        Socket probe = connect_port(port, 200);
        if (probe != kInvalid) {
            close_socket(probe);
            error = "beamd already running (" + default_location() + ")";
            return false;
        }
    }
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) { error = "socket() failed"; return false; }
    BOOL exclusive = TRUE;
    setsockopt(s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char *) &exclusive, sizeof(exclusive));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = 0;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int len = sizeof(addr);
    if (bind(s, (sockaddr *) &addr, sizeof(addr)) != 0 || listen(s, 8) != 0 ||
        getsockname(s, (sockaddr *) &addr, &len) != 0) {
        closesocket(s);
        error = "bind/listen on 127.0.0.1 failed";
        return false;
    }
    unsigned char random[32];
    if (BCryptGenRandom(nullptr, random, sizeof(random), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        closesocket(s); error = "random token generation failed"; return false;
    }
    char hex[65];
    for (int i = 0; i < 32; ++i) std::snprintf(hex + 2 * i, 3, "%02x", (unsigned) random[i]);
    out.token = hex;
    nlohmann::json j = {{"port", ntohs(addr.sin_port)}, {"token", out.token}};
    std::wstring file = endpoint_file(), tmp = file + L".tmp";
    FILE * f = _wfopen(tmp.c_str(), L"wb");
    if (!f) { closesocket(s); error = "cannot write " + default_location(); return false; }
    std::string text = j.dump();
    fwrite(text.data(), 1, text.size(), f);
    fclose(f);
    if (!MoveFileExW(tmp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        closesocket(s); error = "cannot replace endpoint file"; return false;
    }
    set_nonblocking((Socket)s);
    out.socket = (Socket) s;
    out.location = default_location();
    return true;
}

Socket accept_local(const Listener & listener) {
    SOCKET s = accept((SOCKET) listener.socket, nullptr, nullptr);
    if (s == INVALID_SOCKET) return kInvalid;
    BOOL nodelay = TRUE;
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char *) &nodelay, sizeof(nodelay));
    set_nonblocking((Socket)s);
    return (Socket) s;
}

void remove_endpoint(const Listener &) { DeleteFileW(endpoint_file().c_str()); }

Socket connect_local(int timeout_ms, std::string & token, const std::string &) {
    startup();
    int port = 0;
    if (!read_endpoint(port, token)) return kInvalid;
    return connect_port(port, timeout_ms);
}

#else  // POSIX

const Socket kInvalid = -1;

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

bool startup() { return true; }
void close_socket(Socket s) { if (s >= 0) close(s); }
bool set_nonblocking(Socket s) { return fcntl(s, F_SETFL, fcntl(s, F_GETFL) | O_NONBLOCK) == 0; }

int wait(Socket s, int events, int timeout_ms) {
    pollfd p{s, (short) (events == 1 ? POLLIN : POLLOUT), 0};
    int r;
    do r = poll(&p, 1, timeout_ms); while (r < 0 && errno == EINTR);
    return r < 0 ? -1 : r == 0 ? 0 : 1;
}

int wait_readable(const std::vector<Socket> & sockets, std::vector<char> & ready, int timeout_ms) {
    std::vector<pollfd> fds;
    for (auto s : sockets) fds.push_back({s, POLLIN, 0});
    int r = poll(fds.data(), fds.size(), timeout_ms);
    ready.assign(sockets.size(), 0);
    if (r < 0) return errno == EINTR ? 0 : -1;
    for (size_t i = 0; i < fds.size(); ++i) ready[i] = fds[i].revents != 0;
    return r;
}

long send_some(Socket s, const char * data, std::size_t size) {
    ssize_t n = send(s, data, size, MSG_NOSIGNAL);
    if (n >= 0) return (long) n;
    return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ? -2 : -1;
}

long recv_some(Socket s, char * data, std::size_t size) {
    ssize_t n = recv(s, data, size, 0);
    if (n >= 0) return (long) n;
    return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ? -2 : -1;
}

std::string default_location() {
    if (const char * p = std::getenv("BEAM_SOCKET"); p && *p) return p;
    if (const char * r = std::getenv("XDG_RUNTIME_DIR"); r && *r) return std::string(r) + "/beam-ime/beamd.sock";
    return "/tmp/beam-ime-" + std::to_string(getuid()) + "/beamd.sock";
}

namespace {

bool fill_addr(const std::string & path, sockaddr_un & addr) {
    addr = {};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof(addr.sun_path)) return false;
    std::strcpy(addr.sun_path, path.c_str());
    return true;
}

bool peer_is_self(int fd) {
#if defined(__linux__)
    struct ucred cred;
    socklen_t len = sizeof(cred);
    return getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) == 0 && cred.uid == getuid();
#else
    uid_t uid;
    gid_t gid;
    return getpeereid(fd, &uid, &gid) == 0 && uid == getuid();
#endif
}

}  // namespace

bool listen_local(Listener & out, std::string & error, const std::string & override_path) {
    std::string path = override_path.empty() ? default_location() : override_path;
    namespace fs = std::filesystem;
    fs::path dir = fs::path(path).parent_path();
    std::error_code ec;
    fs::create_directories(dir, ec);
    struct stat info{};
    if (ec || lstat(dir.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) || info.st_uid != getuid() ||
        chmod(dir.c_str(), 0700) != 0) { error = "socket directory must be owned by this user"; return false; }
    sockaddr_un addr;
    if (!fill_addr(path, addr)) { error = "socket path too long"; return false; }
    // A live daemon already owns the socket: refuse rather than steal it.
    int probe = socket(AF_UNIX, SOCK_STREAM, 0);
    if (connect(probe, (sockaddr *) &addr, sizeof(addr)) == 0) {
        close(probe);
        error = "beamd already running on " + path;
        return false;
    }
    close(probe);
    if (lstat(path.c_str(), &info) == 0 && (!S_ISSOCK(info.st_mode) || info.st_uid != getuid())) {
        error = "refusing to replace a non-socket endpoint"; return false;
    }
    unlink(path.c_str());
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    mode_t old = umask(0077);
    int rc = bind(fd, (sockaddr *) &addr, sizeof(addr));
    umask(old);
    if (rc != 0 || listen(fd, 8) != 0) {
        error = std::string("bind/listen: ") + std::strerror(errno);
        close(fd);
        return false;
    }
    chmod(path.c_str(), 0600);
    set_nonblocking(fd);
    out.socket = fd;
    out.location = path;
    return true;
}

Socket accept_local(const Listener & listener) {
    int fd = accept(listener.socket, nullptr, nullptr);
    if (fd < 0) return kInvalid;
    if (!peer_is_self(fd)) { close(fd); return kInvalid; }
    set_nonblocking(fd);
#ifdef SO_NOSIGPIPE
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#endif
    return fd;
}

void remove_endpoint(const Listener & listener) { unlink(listener.location.c_str()); }

Socket connect_local(int timeout_ms, std::string & token, const std::string & override_path) {
    token.clear();
    sockaddr_un addr;
    if (!fill_addr(override_path.empty() ? default_location() : override_path, addr)) return kInvalid;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return kInvalid;
#ifdef SO_NOSIGPIPE
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#endif
    set_nonblocking(fd);
    if (connect(fd, (sockaddr *) &addr, sizeof(addr)) != 0) {
        if ((errno != EINPROGRESS && errno != EAGAIN) || wait(fd, 2, timeout_ms) != 1) { close(fd); return kInvalid; }
        int err = 0;
        socklen_t len = sizeof(err);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) != 0 || err != 0) { close(fd); return kInvalid; }
    }
    return fd;
}

#endif

}  // namespace beam::net
