// Local transport between beamd and its clients.
//
// Linux/macOS: a user-private AF_UNIX stream socket ($BEAM_SOCKET, else
//   $XDG_RUNTIME_DIR/beam-ime/beamd.sock); the server checks the peer's uid.
// Windows: loopback TCP on a random port. beamd writes {"port":N,"token":"..."} to
//   %LOCALAPPDATA%\beam-ime\beamd.endpoint (user-private by the profile ACL); every request
//   carries the token, so other local users cannot query the model.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace beam::net {

#ifdef _WIN32
using Socket = std::uintptr_t;
#else
using Socket = int;
#endif
extern const Socket kInvalid;

bool startup();                       // WSAStartup on Windows; no-op elsewhere
void close_socket(Socket s);
bool set_nonblocking(Socket s);
// poll one socket for reading (events = 1) or writing (events = 2); 1 ready, 0 timeout, -1 error
int wait(Socket s, int events, int timeout_ms);
// Wait until any socket is readable (or closed); ready[i] is set for those. Returns -1 on error.
int wait_readable(const std::vector<Socket> & sockets, std::vector<char> & ready, int timeout_ms);
long send_some(Socket s, const char * data, std::size_t size);  // -1 error, -2 would block
long recv_some(Socket s, char * data, std::size_t size);        // 0 closed, -1 error, -2 would block

// Server side.
struct Listener {
    Socket socket = kInvalid;
    std::string token;     // empty on Unix (peer uid is checked instead)
    std::string location;  // socket path or endpoint file, for messages
};
// Fails (and explains in `error`) when another beamd already answers on the endpoint.
bool listen_local(Listener & out, std::string & error, const std::string & unix_path_override = "");
Socket accept_local(const Listener & listener);  // kInvalid when the peer is not this user
void remove_endpoint(const Listener & listener);

// Client side: connect within timeout_ms; `token` receives the token requests must carry.
Socket connect_local(int timeout_ms, std::string & token, const std::string & unix_path_override = "");

// Linux/macOS socket path, Windows endpoint file (UTF-8).
std::string default_location();

}  // namespace beam::net
