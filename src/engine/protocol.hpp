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
