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
    auto now = Clock::now();
    if (now < quiet_until_) return std::nullopt;
    auto deadline = now + std::chrono::milliseconds(timeout_ms);
    if (socket_ == net::kInvalid) {
        socket_ = net::connect_local(timeout_ms, token_, path_);
        if (socket_ == net::kInvalid) { fail(); return std::nullopt; }
    }

    unsigned long long id = next_id_++;
    nlohmann::json req = {{"id", id}, {"op", "query"}, {"keys", keys}, {"context", context},
                          {"max", max_candidates}, {"beam_ms", beam_ms}};
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
            if (!reply.contains("ok") || !reply["ok"].is_boolean() || !reply["ok"].get<bool>() ||
                !reply.contains("candidates") || !reply["candidates"].is_array()) {
                if (reply.contains("error") && reply["error"] == "bad_token") fail();
                return std::vector<std::string>{};
            }
            std::vector<std::string> out;
            for (auto & c : reply["candidates"])
                if (c.is_string() && !c.get<std::string>().empty()) out.push_back(c.get<std::string>());
            return out;
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

}  // namespace beam
