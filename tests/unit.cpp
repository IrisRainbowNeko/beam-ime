// SPDX-License-Identifier: Apache-2.0
#include "keys.hpp"
#include "protocol.hpp"
#include "beam_client.hpp"
#include <iostream>
#include <stdexcept>

void check(bool value, const char * message) { if (!value) throw std::runtime_error(message); }
int main() {
    using namespace beam;
    check(normalize_keys("  NI  hao''") == "ni'hao", "key normalization");
    check(utf8_tail("你好世界", 2) == "世界", "UTF-8 tail");
    check(utf8_chars(std::string("\xe4\xbd", 2)).empty(), "truncated UTF-8");
    check(utf8_chars(std::string("\xc0\x80", 2)).empty(), "overlong UTF-8");
    check(aligns("使用Linux", "sylinux"), "mixed result");
    check(!aligns("使用Linux", "sylinu"), "English must match");
    check(!aligns("hello  world", "helloworld"), "repeated spaces");
    check(fold_case("你好GPT") == "你好gpt", "case folding");
    auto request = nlohmann::json::parse(R"({"id":1,"op":"query","keys":"nh"})");
    check(validate_request(request).empty(), "valid request");
    request["keys"] = std::string(41, 'a');
    check(validate_request(request) == "invalid_keys", "long input");
    request["keys"] = "nh";
    request["max"] = "five";
    check(validate_request(request) == "bad_request", "wrong JSON type");
    check(validate_request(request, "secret") == "bad_token", "authentication before operation");
    Client client("/nonexistent/beam-test.sock");
    check(!client.query("nh", "", 5, 100, 5).has_value(), "unavailable daemon fallback");
    std::cout << "core tests passed\n";
}
