// Key and result helpers shared by the Beam engine, daemon and Rime plugin.
#pragma once

#include <cctype>
#include <string>
#include <vector>

namespace beam {

inline std::vector<std::string> utf8_chars(const std::string & s) {
    std::vector<std::string> out;
    for (size_t i = 0; i < s.size();) {
        unsigned char c = s[i];
        size_t n = c < 0x80 ? 1 : c >= 0xC2 && c <= 0xDF ? 2 : c >= 0xE0 && c <= 0xEF ? 3 : c >= 0xF0 && c <= 0xF4 ? 4 : 0;
        if (!n || i + n > s.size()) return {};
        for (size_t j = 1; j < n; ++j) if (((unsigned char) s[i + j] & 0xC0) != 0x80) return {};
        if ((n == 3 && ((c == 0xE0 && (unsigned char)s[i+1] < 0xA0) || (c == 0xED && (unsigned char)s[i+1] >= 0xA0))) ||
            (n == 4 && ((c == 0xF0 && (unsigned char)s[i+1] < 0x90) || (c == 0xF4 && (unsigned char)s[i+1] >= 0x90)))) return {};
        out.push_back(s.substr(i, n));
        i += n;
    }
    return out;
}

// Last `n` UTF-8 characters of `s`.
inline std::string utf8_tail(const std::string & s, size_t n) {
    auto ch = utf8_chars(s);
    std::string out;
    for (size_t i = ch.size() > n ? ch.size() - n : 0; i < ch.size(); ++i) out += ch[i];
    return out;
}

// Lowercase letters plus single "'" separators (no leading/trailing ones); everything else dropped.
inline std::string normalize_keys(const std::string & raw) {
    std::string keys;
    for (char c : raw) {
        if (c >= 'A' && c <= 'Z') c = (char) (c - 'A' + 'a');
        if (c >= 'a' && c <= 'z') keys.push_back(c);
        else if ((c == '\'' || c == ' ') && !keys.empty() && keys.back() != '\'') keys.push_back('\'');
    }
    while (!keys.empty() && keys.back() == '\'') keys.pop_back();
    return keys;
}

inline int letter_count(const std::string & keys) {
    int n = 0;
    for (char c : keys) n += c != '\'';
    return n;
}

// The prompt's key line: one key per space-separated position ("n h ' s j").
inline std::string spaced(const std::string & keys) {
    std::string out;
    for (size_t i = 0; i < keys.size(); ++i) {
        if (i) out.push_back(' ');
        out.push_back(keys[i]);
    }
    return out;
}

// Mirrors keys_llm_format.structural_valid without segments: the result is Hanzi characters
// and English words (single spaces only between English words); each Hanzi consumes >= 1
// key letter and each English word exactly its lowercase letters, covering all letters.
inline bool aligns(const std::string & result, const std::string & keys) {
    std::string letters;
    for (char c : keys) if (c != '\'') letters.push_back(c);
    std::vector<std::string> pieces;
    std::string word;
    bool prev_space = false;
    for (auto & ch : utf8_chars(result)) {
        bool alpha = ch.size() == 1 && std::isalpha((unsigned char) ch[0]);
        if (alpha) { word += (char) std::tolower((unsigned char) ch[0]); prev_space = false; continue; }
        if (ch == " ") {
            if (word.empty()) return false;
            pieces.push_back(word); word.clear(); prev_space = true;
            continue;
        }
        if (prev_space) return false;
        if (!word.empty()) { pieces.push_back(word); word.clear(); }
        if (ch.size() != 3) return false;
        unsigned cp = ((ch[0] & 0x0F) << 12) | ((ch[1] & 0x3F) << 6) | (ch[2] & 0x3F);
        if (cp < 0x4E00 || cp > 0x9FFF) return false;
        pieces.push_back("");  // a Hanzi
    }
    if (prev_space) return false;
    if (!word.empty()) pieces.push_back(word);
    if (pieces.empty()) return false;
    std::vector<char> reach(letters.size() + 1, 0), next;
    reach[0] = 1;
    for (auto & piece : pieces) {
        next.assign(letters.size() + 1, 0);
        bool any = false;
        for (size_t p = 0; p <= letters.size(); ++p) {
            if (!reach[p]) continue;
            if (piece.empty()) {
                for (size_t q = p + 1; q <= letters.size(); ++q) next[q] = any = 1;
            } else if (letters.compare(p, piece.size(), piece) == 0) {
                next[p + piece.size()] = any = 1;
            }
        }
        if (!any) return false;
        reach.swap(next);
    }
    return reach[letters.size()];
}

inline std::string fold_case(std::string t) {
    for (auto & c : t) c = (char) std::tolower((unsigned char) c);
    return t;
}

}  // namespace beam
