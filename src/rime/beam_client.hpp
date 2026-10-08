// Minimal synchronous client for beamd's JSON-lines local socket (see engine/net.hpp).
#pragma once

#include "net.hpp"

#include <chrono>
#include <optional>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
#include <condition_variable>
#include <deque>
#include <future>
#include <mutex>
#include <thread>
#include <atomic>

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
    std::optional<nlohmann::json> request(nlohmann::json request, int timeout_ms);

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

// One ordered connection for feedback and queries; only immutable snapshots cross threads.
class AsyncClient {
 public:
    explicit AsyncClient(std::string path = "");
    ~AsyncClient();
    void post(nlohmann::json request);
    std::optional<nlohmann::json> query(nlohmann::json request, int timeout_ms);
    nlohmann::json learning_status();
    uint64_t errors() const { return errors_.load(); }
 private:
    struct Work {
        nlohmann::json request;
        int timeout;
        std::shared_ptr<std::promise<std::optional<nlohmann::json>>> reply;
    };
    bool enqueue(Work work);
    void run();
    Client client_;
    std::mutex mutex_;
    std::condition_variable available_;
    std::deque<Work> queue_;
    nlohmann::json learning_status_;
    size_t learning_pending_ = 0;
    std::atomic<uint64_t> errors_{0};
    bool stopping_ = false;
    std::thread thread_;
};

}  // namespace beam
