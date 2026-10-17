// Beam keys-conditioned LLM engine: one llama.cpp model, incremental Top-1 decoding with KV
// prefix reuse and the previous result as a speculative draft, then a time-bounded beam search
// that re-ranks and fills in more candidates. Not thread-safe; the daemon serializes calls.
#pragma once

#include <memory>
#include <string>
#include <vector>
#include "task.hpp"

namespace beam {

struct EngineOptions {
    std::string model_path;
    int gpu_layers = 99;      // 0 = CPU only
    int threads = 0;          // 0 = hardware concurrency / 2
    int beams = 5;
    int context_chars = 64;   // matches the training data
    std::string backend_dir;
    std::string pinyin_path;
};

struct QueryOptions {
    InputMode mode = InputMode::Ime;
    int max_candidates = 5;
    int beam_budget_ms = 150; // 0 disables the beam; on timeout only the Top-1 is returned
    int translate_budget_ms = 1000;  // English for the IME's candidates (translate adapter only)
};

struct QueryResult {
    std::vector<std::string> candidates;  // best first
    std::vector<std::string> comments;
    double greedy_ms = 0;
    double beam_ms = 0;
    double translate_ms = 0;
    bool beam_complete = false;
    std::string translation_status = "ready";
};

class Engine {
 public:
    explicit Engine(const EngineOptions & options);
    ~Engine();
    Engine(const Engine &) = delete;
    Engine & operator=(const Engine &) = delete;

    bool ok() const;
    const std::string & model_name() const;
    const std::string & backend_name() const;
    const std::string & prompt_version() const;
    bool supports(InputMode mode) const;
    bool set_adapter(const std::string & path, std::string & error);
    // English translation for an IME-only model: a LoRA adapter (GGUF metadata
    // beam.prompt_version zh_en_v1, or keys_llm_v4 for older ones). In translate_en mode the IME's candidates are translated
    // one by one in a second context that holds the adapter; ordinary input never sees it.
    bool set_translate_adapter(const std::string & path, std::string & error);
    // keys: raw input letters ("nhsj", "ni'hao"); context: text the user typed before, any length.
    QueryResult query(const std::string & keys, const std::string & context, const QueryOptions & options);

 private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace beam
