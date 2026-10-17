// SPDX-License-Identifier: Apache-2.0
#include "keys.hpp"
#include "protocol.hpp"
#include "task.hpp"
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
    check(task_prompt("mtj", "", InputMode::TranslateEn, kPromptV2) == "任务：译英\n按键：m t j\n结果：", "translation prompt");
    check(task_prompt("nh", "", InputMode::Ime, kPromptV1) == "按键：n h\n结果：", "legacy prompt");
    auto translated = parse_candidate("See you tomorrow.\t明天见", "mtj", InputMode::TranslateEn);
    check(translated && translated->text == "See you tomorrow." && translated->comment == "明天见", "paired translation");
    check(bool(parse_candidate("3:30\t三点半", "sdb", InputMode::TranslateEn)), "numeric translation");
    auto waiting = parse_candidate("的\t<WAIT>", "d", InputMode::TranslateEn, kPromptV4);
    check(waiting && waiting->need_more_input && waiting->text.empty() && waiting->comment == "的", "waiting candidate");
    auto fragment = parse_candidate("看看\tTake a look", "kk", InputMode::TranslateEn, kPromptV4);
    check(fragment && !fragment->need_more_input && fragment->text == "Take a look", "Chinese-first fragment");
    check(!parse_candidate("做其他\tDo其他.", "zuoqt", InputMode::TranslateEn, kPromptV4), "reject Hanzi in English");
    check(task_prompt("d", "你好", InputMode::TranslateEn, kPromptV4) == "任务：译英\n上文：你好\n按键：d\n结果：", "v4 prompt");
    check(zh_en_prefix("") == "任务：中译英\n中文：", "zh_en prompt");
    check(zh_en_prefix("我们明天") == "任务：中译英\n上文：我们明天\n中文：", "zh_en prompt with context");
    float score = bilingual_score({-1.f, -.1f, -2.f}, {"你好", "\t", "Hello"}, kPromptV4);
    check(std::abs(score + 1.25f) < 1e-6f, "bilingual field score ignores tab");
    check(!std::isfinite(bilingual_score({-1.f}, {"你好"}, kPromptV4)), "missing English has no score");
    for (auto text : {"Hello", "\t你好", "Hi\t你好\textra", "Hi\t你好\n", "Hi\t使用Linux"})
        check(!parse_candidate(text, "nh", InputMode::TranslateEn), "reject incomplete translation");
    check(output_budget(InputMode::TranslateEn, 40) == 192 && output_budget(InputMode::Ime, 10) == 28, "task budgets");
    auto request = nlohmann::json::parse(R"({"id":1,"op":"query","keys":"nh"})");
    check(validate_request(request).empty(), "valid request");
    request["mode"] = "translate_en";
    check(validate_request(request).empty(), "translation request");
    request["mode"] = "bad";
    check(validate_request(request) == "invalid_mode", "unknown mode");
    request.erase("mode");
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
