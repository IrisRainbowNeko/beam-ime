// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "keys.hpp"
#include <optional>
#include <stdexcept>

namespace beam {
inline constexpr const char * kPromptV1 = "keys_llm_v1";
inline constexpr const char * kPromptV2 = "keys_llm_v2";
enum class InputMode { Ime, TranslateEn };
inline const char * mode_name(InputMode mode) { return mode == InputMode::Ime ? "ime" : "translate_en"; }
inline InputMode input_mode(const std::string & name) {
    if (name == "ime") return InputMode::Ime;
    if (name == "translate_en") return InputMode::TranslateEn;
    throw std::invalid_argument("invalid_mode");
}
inline std::string task_prompt(const std::string & keys, const std::string & context,
                               InputMode mode, const std::string & version) {
    std::string prefix;
    if (version == kPromptV2) prefix = mode == InputMode::Ime ? "任务：输入\n" : "任务：译英\n";
    else if (version != kPromptV1 || mode != InputMode::Ime) throw std::invalid_argument("unsupported_mode");
    auto tail = utf8_tail(context, 64);
    return prefix + (tail.empty() ? "" : "上文：" + tail + "\n") + "按键：" + spaced(normalize_keys(keys)) + "\n结果：";
}
inline int output_budget(InputMode mode, int letters) {
    return mode == InputMode::Ime ? 2 * letters + 8 : std::min(192, 4 * letters + 32);
}
struct TaskCandidate { std::string text, comment; };
inline std::optional<TaskCandidate> parse_candidate(const std::string & output, const std::string & keys, InputMode mode) {
    if (mode == InputMode::Ime) {
        if (aligns(output, keys)) return TaskCandidate{output, ""};
        return std::nullopt;
    }
    auto tab = output.find('\t');
    if (tab == std::string::npos || tab == 0 || tab + 1 == output.size() ||
        output.find('\t', tab + 1) != std::string::npos || output.find_first_of("\r\n") != std::string::npos)
        return std::nullopt;
    auto text = output.substr(0, tab), comment = output.substr(tab + 1);
    if (utf8_chars(text).empty() || !aligns(comment, keys)) return std::nullopt;
    bool content = false;
    for (unsigned char ch : text) {
        if (ch < 32 || ch == 127) return std::nullopt;
        content |= (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9');
    }
    if (!content) return std::nullopt;
    return TaskCandidate{text, comment};
}
}
