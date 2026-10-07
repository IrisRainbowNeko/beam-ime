// SPDX-License-Identifier: Apache-2.0
#include "store.hpp"
#include "keys.hpp"
#include <sqlite3.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/stat.h>
#endif

namespace beam {
namespace {
class Statement {
 public:
    Statement(sqlite3 * db, const char * sql) : db_(db) {
        if (sqlite3_prepare_v2(db, sql, -1, &value_, nullptr) != SQLITE_OK) fail();
    }
    ~Statement() { sqlite3_finalize(value_); }
    void bind(int i, const std::string & value) {
        if (sqlite3_bind_text(value_, i, value.c_str(), (int)value.size(), SQLITE_TRANSIENT) != SQLITE_OK) fail();
    }
    void bind(int i, uint64_t value) {
        if (sqlite3_bind_int64(value_, i, (sqlite3_int64)value) != SQLITE_OK) fail();
    }
    bool next() {
        int rc = sqlite3_step(value_);
        if (rc == SQLITE_ROW) return true;
        if (rc != SQLITE_DONE) fail();
        return false;
    }
    std::string text(int i) const {
        const auto * p = sqlite3_column_text(value_, i);
        return p ? reinterpret_cast<const char *>(p) : "";
    }
    uint64_t integer(int i) const { return (uint64_t)sqlite3_column_int64(value_, i); }
 private:
    [[noreturn]] void fail() const { throw std::runtime_error(std::string("learning database: ") + sqlite3_errmsg(db_)); }
    sqlite3 * db_;
    sqlite3_stmt * value_ = nullptr;
};

std::string key_letters(const std::string & input) {
    std::string result;
    for (char ch : normalize_keys(input)) if (ch != '\'') result += ch;
    return result;
}

Json sample_from(const Json & event, const std::string & keys, const std::string & text) {
    return {{"keys", normalize_keys(keys)}, {"target", text}, {"context", utf8_tail(event.value("context", ""), 64)},
            {"event_id", event.at("event_id")}};
}
}

std::filesystem::path learning_data_directory() {
#ifdef _WIN32
    wchar_t value[32768];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", value, 32768);
    if (!n || n >= 32768) throw std::runtime_error("LOCALAPPDATA is unavailable");
    return std::filesystem::path(value) / "beam-ime" / "learning";
#else
    if (const char * value = std::getenv("XDG_DATA_HOME")) return std::filesystem::path(value) / "beam-ime/learning";
    const char * home = std::getenv("HOME");
    if (!home) throw std::runtime_error("HOME is unavailable");
    return std::filesystem::path(home) / ".local/share/beam-ime/learning";
#endif
}

Pronunciations::Pronunciations(const std::filesystem::path & path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("cannot open Beam pronunciation table: " + path.u8string());
    std::string line, ch, reading;
    while (std::getline(stream, line)) {
        std::istringstream values(line);
        values >> ch;
        while (values >> reading) table_[ch].push_back(reading);
    }
}

std::vector<size_t> Pronunciations::align(const std::string & text, const std::string & input) const {
    auto chars = utf8_chars(text);
    const auto keys = key_letters(input);
    if (chars.empty() || keys.empty() || keys.size() > 40) return {};
    std::vector<std::vector<int>> previous(chars.size() + 1, std::vector<int>(keys.size() + 1, -1));
    previous[0][0] = 0;
    for (size_t i = 0; i < chars.size(); ++i) {
        auto found = table_.find(chars[i]);
        for (size_t p = 0; p <= keys.size(); ++p) if (previous[i][p] >= 0) {
            if (chars[i] == " ") { previous[i + 1][p] = (int)p; continue; }
            if (chars[i].size() == 1) {
                if (p < keys.size() && fold_case(chars[i])[0] == keys[p]) previous[i + 1][p + 1] = (int)p;
            } else if (found != table_.end()) {
                for (const auto & reading : found->second)
                    for (size_t n = 1; n <= reading.size() && p + n <= keys.size(); ++n)
                        if (keys.compare(p, n, reading, 0, n) == 0) previous[i + 1][p + n] = (int)p;
            }
        }
    }
    if (previous.back().back() < 0) return {};
    std::vector<size_t> boundaries(chars.size() + 1);
    boundaries.back() = keys.size();
    for (size_t i = chars.size(); i > 0; --i) boundaries[i - 1] = (size_t)previous[i][boundaries[i]];
    return boundaries;
}

std::pair<std::string, std::string> Pronunciations::codes(const std::string & text, const std::string & keys) const {
    auto readings = syllables(text, keys);
    auto chars = utf8_chars(text);
    std::string full, initial;
    size_t i = 0;
    for (auto & ch : chars) if (ch != " ") {
        if (i >= readings.size()) return {};
        full += readings[i];
        initial += ch.size() == 1 ? readings[i] : readings[i].substr(0, 1);
        ++i;
    }
    return {full, initial};
}

std::vector<std::string> Pronunciations::syllables(const std::string & text, const std::string & keys) const {
    auto boundaries = align(text, keys);
    if (boundaries.empty()) return {};
    auto chars = utf8_chars(text);
    std::string letters = key_letters(keys);
    std::vector<std::string> result;
    for (size_t i = 0; i < chars.size(); ++i) {
        auto found = table_.find(chars[i]);
        if (chars[i] == " ") continue;
        if (found == table_.end()) { result.push_back(fold_case(chars[i])); continue; }
        auto prefix = letters.substr(boundaries[i], boundaries[i + 1] - boundaries[i]);
        for (auto & reading : found->second) if (reading.compare(0, prefix.size(), prefix) == 0) {
            result.push_back(reading); break;
        }
    }
    return result;
}

double context_similarity(const std::string & a, const std::string & b) {
    auto grams = [](const std::string & text) {
        std::set<std::string> result;
        auto chars = utf8_chars(text);
        for (size_t i = 0; i < chars.size(); ++i) if (i + 1 < chars.size() || chars.size() == 1)
            result.insert(chars[i] + (i + 1 < chars.size() ? chars[i + 1] : ""));
        return result;
    };
    auto left = grams(a), right = grams(b);
    if (left.empty() || right.empty()) return 0;
    size_t common = 0;
    for (const auto & value : left) common += right.count(value);
    return (double)common / (left.size() + right.size() - common);
}

LearningStore::LearningStore(const std::filesystem::path & directory, const std::filesystem::path & table)
    : directory_(directory), pronunciation_(table) {
    std::filesystem::create_directories(directory_);
#ifndef _WIN32
    if (chmod(directory_.c_str(), 0700) != 0) throw std::runtime_error("cannot protect learning directory");
#endif
    if (sqlite3_open_v2((directory_ / "learning.sqlite3").u8string().c_str(), &db_,
                        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nullptr) != SQLITE_OK) {
        std::string error = sqlite3_errmsg(db_); sqlite3_close(db_); db_ = nullptr;
        throw std::runtime_error("cannot open learning database: " + error);
    }
    sqlite3_busy_timeout(db_, 1000);
    exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA secure_delete=ON;"
         "CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,value TEXT NOT NULL);"
         "CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,clock INTEGER NOT NULL);"
         "CREATE TABLE IF NOT EXISTS recent(clock INTEGER PRIMARY KEY,payload TEXT NOT NULL);"
         "CREATE TABLE IF NOT EXISTS replay(slot INTEGER PRIMARY KEY,payload TEXT NOT NULL);"
         "CREATE TABLE IF NOT EXISTS words(text TEXT PRIMARY KEY,keys TEXT NOT NULL,full TEXT NOT NULL,initials TEXT NOT NULL,"
         "head TEXT NOT NULL,count INTEGER NOT NULL,last INTEGER NOT NULL,contexts TEXT NOT NULL,corrections INTEGER NOT NULL);"
         "CREATE INDEX IF NOT EXISTS words_head ON words(head); PRAGMA user_version=1;");
    exec("CREATE TABLE IF NOT EXISTS word_codes(text TEXT NOT NULL,keys TEXT NOT NULL,head TEXT NOT NULL,PRIMARY KEY(text,keys));"
         "CREATE INDEX IF NOT EXISTS codes_head ON word_codes(head);"
         "INSERT OR IGNORE INTO word_codes SELECT text,keys,head FROM words;"
         "CREATE TABLE IF NOT EXISTS selections(keys TEXT NOT NULL,text TEXT NOT NULL,corrections INTEGER NOT NULL,PRIMARY KEY(keys,text));");
    Statement aliases(db_, "SELECT text,keys FROM word_codes");
    while (aliases.next()) index(aliases.text(0), aliases.text(1));
}
LearningStore::~LearningStore() { if (db_) sqlite3_close(db_); }
void LearningStore::exec(const char * sql) const {
    char * message = nullptr;
    if (sqlite3_exec(db_, sql, nullptr, nullptr, &message) != SQLITE_OK) {
        std::string error = message ? message : sqlite3_errmsg(db_); sqlite3_free(message);
        throw std::runtime_error("learning database: " + error);
    }
}
Json LearningStore::setting(const std::string & name, const Json & initial) const {
    Statement query(db_, "SELECT value FROM settings WHERE name=?"); query.bind(1, name);
    return query.next() ? Json::parse(query.text(0)) : initial;
}
void LearningStore::set(const std::string & name, const Json & value) {
    Statement query(db_, "INSERT INTO settings VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value");
    query.bind(1, name); query.bind(2, value.dump()); query.next();
}
bool LearningStore::enabled() const { return setting("enabled", false).get<bool>(); }
bool LearningStore::collecting() const { return enabled() && !setting("paused", false).get<bool>(); }
uint64_t LearningStore::revision() const { return setting("revision", uint64_t(0)).get<uint64_t>(); }
void LearningStore::changed() { set("revision", revision() + 1); }
Json LearningStore::status() const {
    Statement count(db_, "SELECT (SELECT COUNT(*) FROM words),(SELECT COUNT(*) FROM recent),(SELECT COUNT(*) FROM replay)");
    count.next();
    auto trainer = setting("trainer", Json::object()), adapter = setting("active_adapter", Json::object());
    Json trainer_status = {{"installed", !trainer.empty()}}, adapter_status = Json::object();
    for (auto field : {"version", "backend", "base_sha256", "prompt_version"})
        if (trainer.contains(field)) trainer_status[field] = trainer[field];
    for (auto field : {"id", "base_sha256", "prompt_version", "rank", "alpha", "recipe", "clock", "training_seconds"})
        if (adapter.contains(field)) adapter_status[field] = adapter[field];
    return {{"enabled", enabled()}, {"paused", setting("paused", false)}, {"collecting", collecting()},
            {"words", count.integer(0)}, {"recent_samples", count.integer(1)}, {"replay_samples", count.integer(2)},
            {"confirmed_events", setting("clock", uint64_t(0))}, {"revision", revision()},
            {"trainer", trainer_status}, {"training", setting("training", {{"state", "not_installed"}})},
            {"adapter", adapter_status}, {"last_error", setting("last_error", "")},
            {"client_errors", setting("client_errors", uint64_t(0))}};
}

void LearningStore::learn(const Json & sample, uint64_t clock, bool correction) {
    std::string text = sample.at("target"), keys = sample.at("keys"), context = sample.at("context");
    auto codes = pronunciation_.codes(text, keys);
    auto normalized = key_letters(keys);
    if (normalized.empty()) return;
    Statement old(db_, "SELECT count,contexts,corrections FROM words WHERE text=?"); old.bind(1, text);
    uint64_t count = 0, corrections = 0;
    Json contexts = Json::array();
    if (old.next()) { count = old.integer(0); contexts = Json::parse(old.text(1)); corrections = old.integer(2); }
    if (!context.empty()) { contexts.push_back(context); while (contexts.size() > 4) contexts.erase(contexts.begin()); }
    Statement write(db_, "INSERT OR REPLACE INTO words VALUES(?,?,?,?,?,?,?,?,?)");
    write.bind(1, text); write.bind(2, normalize_keys(keys)); write.bind(3, codes.first); write.bind(4, codes.second);
    write.bind(5, normalized.substr(0, 1)); write.bind(6, count + 1); write.bind(7, clock);
    write.bind(8, contexts.dump()); write.bind(9, corrections + (correction ? 1 : 0)); write.next();
    for (const auto & code : {normalize_keys(keys), codes.first, codes.second}) if (!code.empty()) {
        Statement alias(db_, "INSERT OR IGNORE INTO word_codes VALUES(?,?,?)");
        alias.bind(1, text); alias.bind(2, code); alias.bind(3, code.substr(0, 1)); alias.next();
    }
    if (correction) {
        Statement selected(db_, "INSERT INTO selections VALUES(?,?,1) ON CONFLICT(keys,text) DO UPDATE SET corrections=corrections+1");
        selected.bind(1, key_letters(keys)); selected.bind(2, text); selected.next();
    }
}

void LearningStore::index(const std::string & text, const std::string & keys) {
    exact_[key_letters(keys)].insert(text);
    auto readings = pronunciation_.syllables(text, keys);
    if (readings.empty()) return;
    auto * node = &codes_;
    for (const auto & reading : readings) {
        auto & next = node->next[reading];
        if (!next) next = std::make_unique<CodeNode>();
        node = next.get();
    }
    node->words.insert(text);
}

bool LearningStore::feedback(const Json & event) {
    if (!collecting()) return false;
    Statement duplicate(db_, "SELECT 1 FROM events WHERE id=?"); duplicate.bind(1, event.at("event_id").get<std::string>());
    if (duplicate.next()) return false;
    Json samples = Json::array();
    std::set<std::pair<std::string, std::string>> seen;
    auto add = [&](const std::string & keys, const std::string & text, const std::string & first) {
        auto normalized = normalize_keys(keys);
        if (!normalized.empty() && letter_count(normalized) <= 40 && aligns(text, normalized) && seen.emplace(normalized, text).second) {
            samples.push_back(sample_from(event, keys, text));
            samples.back()["correction"] = !first.empty() && first != text;
        }
    };
    add(event.at("keys"), event.at("text"), event.value("first", ""));
    for (const auto & part : event.value("parts", Json::array())) add(part.at("keys"), part.at("text"), part.value("first", ""));
    if (samples.empty()) return false;
    exec("BEGIN IMMEDIATE");
    try {
        uint64_t clock = setting("clock", uint64_t(0)).get<uint64_t>() + 1;
        Statement insert(db_, "INSERT INTO events VALUES(?,?)");
        insert.bind(1, event.at("event_id").get<std::string>()); insert.bind(2, clock); insert.next();
        for (const auto & sample : samples) learn(sample, clock, sample.at("correction"));
        // Known words also gain evidence when committed inside a longer sentence.
        Statement known(db_, "SELECT text,keys FROM words WHERE instr(?,text)>0 AND text<>?");
        known.bind(1, event.at("text").get<std::string>()); known.bind(2, event.at("text").get<std::string>());
        std::vector<Json> contained;
        while (known.next()) if (!seen.count({normalize_keys(known.text(1)), known.text(0)}))
            contained.push_back(sample_from(event, known.text(1), known.text(0)));
        for (const auto & sample : contained) learn(sample, clock, false);
        Json payload = {{"event_id", event.at("event_id")}, {"samples", samples}};
        Statement recent(db_, "INSERT INTO recent VALUES(?,?)"); recent.bind(1, clock); recent.bind(2, payload.dump()); recent.next();
        exec("DELETE FROM recent WHERE clock NOT IN (SELECT clock FROM recent ORDER BY clock DESC LIMIT 512)");
        uint64_t slot = clock;
        if (clock > 4096) {
            static std::mt19937_64 rng(std::random_device{}());
            slot = std::uniform_int_distribution<uint64_t>(1, clock)(rng);
        }
        if (slot <= 4096) {
            Statement replay(db_, "INSERT OR REPLACE INTO replay VALUES(?,?)");
            replay.bind(1, slot); replay.bind(2, payload.dump()); replay.next();
        }
        set("clock", clock); changed(); exec("COMMIT");
    } catch (...) { exec("ROLLBACK"); throw; }
    for (const auto & sample : samples) index(sample.at("target"), sample.at("keys"));
    return true;
}

Json LearningStore::rank(const std::string & raw_keys, const std::string & context,
                         const std::vector<std::string> & generated, size_t limit) const {
    struct Candidate { std::string text; double score; bool personal; };
    std::vector<Candidate> options;
    for (const auto & text : generated) if (std::none_of(options.begin(), options.end(), [&](auto & c) { return fold_case(c.text) == fold_case(text); }))
        options.push_back({text, 2.0 / (options.size() + 1), false});
    if (enabled()) {
        const auto keys = key_letters(raw_keys);
        std::vector<std::vector<size_t>> model_bounds;
        std::vector<std::vector<std::string>> model_chars;
        for (const auto & candidate : generated) {
            model_bounds.push_back(pronunciation_.align(candidate, keys));
            model_chars.push_back(utf8_chars(candidate));
        }
        uint64_t clock = setting("clock", uint64_t(0));
        // Walk syllable prefixes, so arbitrary mixed codes do not scan the vocabulary.
        std::map<std::string, std::set<std::pair<size_t, size_t>>> matches;
        for (size_t start = 0; start < keys.size(); ++start) {
            std::set<std::pair<const CodeNode *, size_t>> visited;
            std::function<void(const CodeNode &, size_t)> walk = [&](const CodeNode & node, size_t offset) {
                if (!visited.emplace(&node, offset).second) return;
                for (const auto & text : node.words) matches[text].emplace(start, offset);
                if (offset == keys.size()) return;
                for (const auto & edge : node.next)
                    for (size_t n = 1; n <= edge.first.size() && offset + n <= keys.size(); ++n) {
                        if (keys[offset + n - 1] != edge.first[n - 1]) break;
                        walk(*edge.second, offset + n);
                    }
            };
            walk(codes_, start);
            for (size_t end = start + 1; end <= keys.size(); ++end) {
                auto found = exact_.find(keys.substr(start, end - start));
                if (found != exact_.end()) for (const auto & text : found->second) matches[text].emplace(start, end);
            }
        }
        for (const auto & match : matches) {
            const auto & text = match.first;
            Statement words(db_, "SELECT text,keys,count,last,contexts,"
                                "COALESCE((SELECT corrections FROM selections s WHERE s.text=w.text AND s.keys=?),0) "
                                "FROM words w WHERE text=?");
            words.bind(1, keys); words.bind(2, text);
            if (words.next()) {
                double strength = (double)words.integer(2) / (words.integer(2) + 2.0), similarity = 0;
                for (auto & previous : Json::parse(words.text(4))) similarity = std::max(similarity, context_similarity(context, previous));
                double bonus = strength * (1.5 + std::exp(-(double)(clock - words.integer(3)) / 32.0) + similarity);
                if (words.integer(5) && (context.empty() || similarity >= .2)) bonus += 2;
                std::set<std::string> additions;
                if (match.second.count({0, keys.size()})) additions.insert(text);
                auto term_chars = utf8_chars(text);
                for (size_t candidate_index = 0; candidate_index < generated.size(); ++candidate_index) {
                    const auto & bounds = model_bounds[candidate_index];
                    const auto & chars = model_chars[candidate_index];
                    if (bounds.empty() || term_chars.size() > chars.size()) continue;
                    for (size_t start = 0; start + term_chars.size() <= chars.size(); ++start) {
                        size_t end = start + term_chars.size();
                        if (!match.second.count({bounds[start], bounds[end]})) continue;
                        std::string composed;
                        for (size_t i = 0; i < start; ++i) composed += chars[i];
                        composed += text;
                        for (size_t i = end; i < chars.size(); ++i) composed += chars[i];
                        additions.insert(composed);
                    }
                }
                for (const auto & value : additions) {
                    auto found = std::find_if(options.begin(), options.end(), [&](auto & c) { return fold_case(c.text) == fold_case(value); });
                    double scaled = bonus * (double)term_chars.size() / std::max(term_chars.size(), utf8_chars(value).size());
                    if (found == options.end()) options.push_back({value, scaled, true});
                    else if (!found->personal) { found->score += scaled; found->personal = true; }
                }
            }
        }
        std::stable_sort(options.begin(), options.end(), [](auto & a, auto & b) { return a.score > b.score; });
        auto personal = std::find_if(options.begin(), options.end(), [](auto & c) { return c.personal; });
        if (limit && personal != options.end() && (size_t)(personal - options.begin()) >= limit) std::iter_swap(personal, options.begin() + limit - 1);
    }
    Json texts = Json::array(), sources = Json::array();
    for (size_t i = 0; i < std::min(limit, options.size()); ++i) {
        texts.push_back(options[i].text); sources.push_back(options[i].personal ? "personal" : "model");
    }
    return {{"candidates", texts}, {"sources", sources}, {"personalization_revision", revision()},
            {"learning_enabled", enabled()}, {"learning_paused", setting("paused", false)}};
}

Json LearningStore::snapshot() const {
    Json recent = Json::array(), history = Json::array();
    Statement rows(db_, "SELECT payload FROM recent ORDER BY clock DESC");
    std::set<std::string> ids;
    while (rows.next()) { auto row = Json::parse(rows.text(0)); ids.insert(row.at("event_id")); recent.push_back(row); }
    Statement replay(db_, "SELECT payload FROM replay ORDER BY slot");
    while (replay.next()) { auto row = Json::parse(replay.text(0)); if (!ids.count(row.at("event_id"))) history.push_back(row); }
    return {{"recent", recent}, {"history", history}, {"clock", setting("clock", uint64_t(0))}};
}
void LearningStore::reset() {
    uint64_t next = revision() + 1;
    auto trainer = setting("trainer", Json::object());
    exec("BEGIN IMMEDIATE");
    try {
        exec("DELETE FROM words; DELETE FROM word_codes; DELETE FROM selections; DELETE FROM events; DELETE FROM recent; DELETE FROM replay; DELETE FROM settings;");
        set("revision", next); set("trainer", trainer); exec("COMMIT");
    } catch (...) { exec("ROLLBACK"); throw; }
    exec("PRAGMA wal_checkpoint(TRUNCATE); VACUUM;");
    codes_ = CodeNode(); exact_.clear();
}
}
