#include "beam_client.hpp"

#include <nlohmann/json.hpp>

namespace beam {

using Clock = std::chrono::steady_clock;

namespace {

int remaining_ms(Clock::time_point deadline) {
    auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
    return left > 0 ? (int) left : 0;
}

}  // namespace

void Client::disconnect() {
    net::close_socket(socket_);
    socket_ = net::kInvalid;
}

void Client::fail() {
    disconnect();
    quiet_until_ = Clock::now() + retry_after_;
}

std::optional<std::vector<std::string>> Client::query(const std::string & keys, const std::string & context,
                                                      int max_candidates, int beam_ms, int timeout_ms) {
    auto reply = request({{"op", "query"}, {"keys", keys}, {"context", context},
                          {"max", max_candidates}, {"beam_ms", beam_ms}}, timeout_ms);
    if (!reply) return std::nullopt;
    std::vector<std::string> out;
    if (!reply->value("ok", false) || !reply->contains("candidates") || !(*reply)["candidates"].is_array()) return out;
    for (const auto & value : (*reply)["candidates"]) if (value.is_string()) out.push_back(value);
    return out;
}

std::optional<nlohmann::json> Client::request(nlohmann::json req, int timeout_ms) {
    auto now = Clock::now();
    if (now < quiet_until_) return std::nullopt;
    auto deadline = now + std::chrono::milliseconds(timeout_ms);
    if (socket_ == net::kInvalid) {
        socket_ = net::connect_local(timeout_ms, token_, path_);
        if (socket_ == net::kInvalid) { fail(); return std::nullopt; }
    }

    unsigned long long id = next_id_++;
    req["id"] = id;
    if (!token_.empty()) req["token"] = token_;
    std::string line = req.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace) + "\n";
    for (size_t off = 0; off < line.size();) {
        long n = net::send_some(socket_, line.data() + off, line.size() - off);
        if (n > 0) { off += (size_t) n; continue; }
        if (n == -2 && net::wait(socket_, 2, remaining_ms(deadline)) == 1) continue;
        fail();
        return std::nullopt;
    }

    std::string buffer;
    while (true) {
        size_t nl;
        while ((nl = buffer.find('\n')) != std::string::npos) {
            std::string reply_line = buffer.substr(0, nl);
            buffer.erase(0, nl + 1);
            auto reply = nlohmann::json::parse(reply_line, nullptr, false);
            if (reply.is_discarded() || !reply.is_object()) { fail(); return std::nullopt; }
            // A reply to an older, timed-out request: skip it and keep waiting for ours.
            if (!reply.contains("id") || !reply["id"].is_number_unsigned() || reply["id"].get<unsigned long long>() != id)
                continue;
            if (!reply.contains("ok") || !reply["ok"].is_boolean()) { fail(); return std::nullopt; }
            if (reply.value("error", "") == "bad_token") fail();
            return reply;
        }
        int wait = remaining_ms(deadline);
        if (wait <= 0 || net::wait(socket_, 1, wait) != 1) {
            // Too slow: drop the connection so the late reply cannot be mistaken for a newer one.
            disconnect();
            return std::nullopt;
        }
        char buf[8192];
        long n = net::recv_some(socket_, buf, sizeof(buf));
        if (n == -2) continue;
        if (n <= 0) { fail(); return std::nullopt; }
        buffer.append(buf, (size_t) n);
        if (buffer.size() > 64 * 1024) { fail(); return std::nullopt; }
    }
}

AsyncClient::AsyncClient(std::string path) : client_(std::move(path)), thread_([this] { run(); }) {}
AsyncClient::~AsyncClient() {
    { std::lock_guard<std::mutex> lock(mutex_); stopping_ = true;
      for (auto & work : queue_) if (work.reply) work.reply->set_value(std::nullopt);
      queue_.clear(); }
    available_.notify_one(); thread_.join();
}
bool AsyncClient::enqueue(Work work) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_ || queue_.size() >= 128) { ++errors_; return false; }
    queue_.push_back(std::move(work)); available_.notify_one(); return true;
}
void AsyncClient::post(nlohmann::json request) { enqueue({std::move(request), 400, {}}); }
std::optional<nlohmann::json> AsyncClient::query(nlohmann::json request, int timeout_ms) {
    auto reply = std::make_shared<std::promise<std::optional<nlohmann::json>>>();
    auto future = reply->get_future();
    if (!enqueue({std::move(request), timeout_ms, reply})) return std::nullopt;
    if (future.wait_for(std::chrono::milliseconds(timeout_ms)) != std::future_status::ready) return std::nullopt;
    return future.get();
}
void AsyncClient::run() {
    while (true) {
        Work work;
        { std::unique_lock<std::mutex> lock(mutex_);
          available_.wait(lock, [&] { return stopping_ || !queue_.empty(); });
          if (queue_.empty()) return;
          work = std::move(queue_.front()); queue_.pop_front(); }
        std::optional<nlohmann::json> reply;
        try { reply = client_.request(work.request, work.timeout); }
        catch (const std::exception &) { ++errors_; }
        if (!reply || !reply->value("ok", false)) ++errors_;
        if (work.reply) work.reply->set_value(std::move(reply));
    }
}

}  // namespace beam
