// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "keys.hpp"
#include <optional>
#include <stdexcept>
#include <cmath>

namespace beam {
inline constexpr const char * kPromptV1 = "keys_llm_v1";
inline constexpr const char * kPromptV2 = "keys_llm_v2";
inline constexpr const char * kPromptV3 = "keys_llm_v3";
inline constexpr const char * kPromptV4 = "keys_llm_v4";
inline bool dual_prompt(const std::string & version) { return version == kPromptV2 || version == kPromptV3 || version == kPromptV4; }
inline bool chinese_first(const std::string & version) { return version == kPromptV3 || version == kPromptV4; }
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
    if (dual_prompt(version)) prefix = mode == InputMode::Ime ? "任务：输入\n" : "任务：译英\n";
    else if (version != kPromptV1 || mode != InputMode::Ime) throw std::invalid_argument("unsupported_mode");
    auto tail = utf8_tail(context, 64);
    return prefix + (tail.empty() ? "" : "上文：" + tail + "\n") + "按键：" + spaced(normalize_keys(keys)) + "\n结果：";
}
inline int output_budget(InputMode mode, int letters) {
    return mode == InputMode::Ime ? 2 * letters + 8 : std::min(192, 4 * letters + 32);
}
inline float bilingual_score(const std::vector<float> & probabilities, const std::vector<std::string> & pieces,
                              const std::string & version) {
    float cn = 0, en = 0;
    int nc = 0, ne = 0;
    bool after = false;
    auto add = [&](float value, bool chinese) { if (chinese) { cn += value; ++nc; } else { en += value; ++ne; } };
    for (size_t i = 0; i < probabilities.size(); ++i) {
        auto tab = pieces[i].find('\t');
        if (tab != std::string::npos) {
            if (after || pieces[i].find('\t', tab + 1) != std::string::npos) return -INFINITY;
            size_t following = pieces[i].size() - tab - 1, total = tab + following;
            if (tab) add(probabilities[i] * tab / total, chinese_first(version));
            if (following) add(probabilities[i] * following / total, !chinese_first(version));
            after = true;
        } else if (!pieces[i].empty()) add(probabilities[i], !after == chinese_first(version));
    }
    return after && nc && ne ? .75f * cn / nc + .25f * en / std::pow((float)ne, .6f) : -INFINITY;
}
struct TaskCandidate { std::string text, comment; bool need_more_input = false; };
inline std::optional<TaskCandidate> parse_candidate(const std::string & output, const std::string & keys, InputMode mode,
                                                    const std::string & version = kPromptV2) {
    if (mode == InputMode::Ime) {
        if (aligns(output, keys)) return TaskCandidate{output, ""};
        return std::nullopt;
    }
    auto tab = output.find('\t');
    if (tab == std::string::npos || tab == 0 || tab + 1 == output.size() ||
        output.find('\t', tab + 1) != std::string::npos || output.find_first_of("\r\n") != std::string::npos)
        return std::nullopt;
    auto text = output.substr(0, tab), comment = output.substr(tab + 1);
    if (chinese_first(version)) std::swap(text, comment);
    if (version == kPromptV4 && text == "<WAIT>" && aligns(comment, keys)) return TaskCandidate{"", comment, true};
    if (text.find("<WAIT>") != std::string::npos) return std::nullopt;
    if (utf8_chars(text).empty() || !aligns(comment, keys)) return std::nullopt;
    bool content = false;
    for (unsigned char ch : text) {
        if (ch < 32 || ch == 127) return std::nullopt;
        content |= (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9');
    }
    if (!content) return std::nullopt;
    if (version == kPromptV4) for (const auto & ch : utf8_chars(text)) {
        if (ch.size() != 3) continue;
        unsigned cp = ((ch[0] & 0x0F) << 12) | ((ch[1] & 0x3F) << 6) | (ch[2] & 0x3F);
        if (cp >= 0x4E00 && cp <= 0x9FFF) return std::nullopt;
    }
    return TaskCandidate{text, comment};
}
}
