// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "keys.hpp"
#include <nlohmann/json.hpp>

namespace beam {
inline std::string validate_request(const nlohmann::json & r, const std::string & token = "") {
    if (!r.is_object()) return "bad_request";
    if (!token.empty() && (!r.contains("token") || !r["token"].is_string() || r["token"] != token))
        return "bad_token";
    if (!r.contains("id") || !r["id"].is_number_unsigned() || !r.contains("op") || !r["op"].is_string())
        return "bad_request";
    if (r["op"] == "health") return {};
    auto text = [&](const char * field, size_t size, bool required = true) {
        if (!r.contains(field)) return !required;
        return r[field].is_string() && (!required || !r[field].get_ref<const std::string &>().empty()) &&
               r[field].get_ref<const std::string &>().size() <= size &&
               r[field].get_ref<const std::string &>().find('\0') == std::string::npos;
    };
    if (r["op"] == "learning") {
        if (!text("action", 32)) return "bad_request";
        if (r.contains("confirm") && !r["confirm"].is_boolean()) return "bad_request";
        if (!text("manifest", 4096, false)) return "bad_request";
        return {};
    }
    if (r["op"] == "composition") {
        if (!text("session", 128) || !text("composition", 128) || !text("state", 16)) return "bad_request";
        if (r["state"] != "begin" && r["state"] != "activity" && r["state"] != "end" && r["state"] != "cancel") return "bad_request";
        if (r.contains("client_errors") && !r["client_errors"].is_number_unsigned()) return "bad_request";
        return {};
    }
    if (r["op"] == "feedback") {
        for (auto field : {"session", "composition", "event_id"}) if (!text(field, 192)) return "bad_request";
        if (!text("keys", 4096) || !text("text", 4096) || !text("context", 4096, false) || !text("first", 4096, false)) return "bad_request";
        if (r.contains("parts")) {
            if (!r["parts"].is_array() || r["parts"].size() > 40) return "bad_request";
            for (const auto & part : r["parts"])
                if (!part.is_object() || !part.contains("keys") || !part["keys"].is_string() || part["keys"].get_ref<const std::string &>().size() > 4096 ||
                    !part.contains("text") || !part["text"].is_string() || part["text"].get_ref<const std::string &>().size() > 4096 ||
                    (part.contains("first") && (!part["first"].is_string() || part["first"].get_ref<const std::string &>().size() > 4096))) return "bad_request";
        }
        return {};
    }
    if (r["op"] != "query") return "unknown_op";
    if (!r.contains("keys") || !r["keys"].is_string()) return "bad_request";
    auto keys = r["keys"].get<std::string>();
    if (keys.size() > 128 || keys.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ' ") != std::string::npos)
        return "invalid_keys";
    auto normalized = normalize_keys(keys);
    if (normalized.empty() || letter_count(normalized) > 40) return "invalid_keys";
    if (r.contains("context") && (!r["context"].is_string() || r["context"].get_ref<const std::string &>().size() > 4096))
        return "invalid_context";
    for (const auto * field : {"max", "beam_ms"}) {
        if (r.contains(field) && (!r[field].is_number_integer() || r[field].get<double>() < 0 || r[field].get<double>() > 2000))
            return "bad_request";
    }
    return {};
}
}
