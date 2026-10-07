// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <nlohmann/json.hpp>
#include <filesystem>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

struct sqlite3;

namespace beam {
using Json = nlohmann::json;

class Pronunciations {
 public:
    explicit Pronunciations(const std::filesystem::path & path);
    std::vector<size_t> align(const std::string & text, const std::string & keys) const;
    std::pair<std::string, std::string> codes(const std::string & text, const std::string & keys) const;
    std::vector<std::string> syllables(const std::string & text, const std::string & keys) const;
 private:
    std::map<std::string, std::vector<std::string>> table_;
};

class LearningStore {
 public:
    LearningStore(const std::filesystem::path & directory, const std::filesystem::path & pronunciations);
    ~LearningStore();
    LearningStore(const LearningStore &) = delete;
    LearningStore & operator=(const LearningStore &) = delete;
    Json status() const;
    Json setting(const std::string & name, const Json & initial = nullptr) const;
    void set(const std::string & name, const Json & value);
    bool enabled() const;
    bool collecting() const;
    uint64_t revision() const;
    void changed();
    bool feedback(const Json & event);
    Json rank(const std::string & keys, const std::string & context,
              const std::vector<std::string> & generated, size_t limit) const;
    Json snapshot() const;
    void reset();
    const std::filesystem::path & directory() const { return directory_; }
 private:
    void exec(const char * sql) const;
    void learn(const Json & sample, uint64_t clock, bool correction);
    void index(const std::string & text, const std::string & keys);
    struct CodeNode {
        std::map<std::string, std::unique_ptr<CodeNode>> next;
        std::set<std::string> words;
    };
    CodeNode codes_;
    std::map<std::string, std::set<std::string>> exact_;
    sqlite3 * db_ = nullptr;
    std::filesystem::path directory_;
    Pronunciations pronunciation_;
};

std::filesystem::path learning_data_directory();
double context_similarity(const std::string & a, const std::string & b);
}
