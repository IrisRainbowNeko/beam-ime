// Minimal synchronous client for beamd's JSON-lines local socket (see engine/net.hpp).
#pragma once

#include "net.hpp"

#include <chrono>
#include <optional>
#include <string>
#include <vector>

namespace beam {

class Client {
 public:
    // `socket_path` overrides the Unix socket location; ignored on Windows.
    explicit Client(std::string socket_path = "") : path_(std::move(socket_path)) {}
    ~Client() { disconnect(); }
    Client(const Client &) = delete;
    Client & operator=(const Client &) = delete;

    // nullopt when beamd is unreachable, too slow, or answered garbage. After a failure the client
    // stays quiet for `retry_after` so a dead daemon does not cost every keystroke a connect().
    std::optional<std::vector<std::string>> query(const std::string & keys, const std::string & context,
                                                  int max_candidates, int beam_ms, int timeout_ms);

 private:
    void disconnect();
    void fail();

    std::string path_;
    net::Socket socket_ = net::kInvalid;
    std::string token_;
    unsigned long long next_id_ = 1;
    std::chrono::steady_clock::time_point quiet_until_{};
    std::chrono::milliseconds retry_after_{2000};
};

}  // namespace beam
