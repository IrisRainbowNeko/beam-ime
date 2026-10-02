// Beam keys-conditioned LLM engine: one llama.cpp model, incremental Top-1 decoding with KV
// prefix reuse and the previous result as a speculative draft, then a time-bounded beam search
// that re-ranks and fills in more candidates. Not thread-safe; the daemon serializes calls.
#pragma once

#include <memory>
#include <string>
#include <vector>

namespace beam {

struct EngineOptions {
    std::string model_path;
    int gpu_layers = 99;      // 0 = CPU only
    int threads = 0;          // 0 = hardware concurrency / 2
    int beams = 5;
    int context_chars = 64;   // matches the training data
    std::string backend_dir;
};

struct QueryOptions {
    int max_candidates = 5;
    int beam_budget_ms = 150; // 0 disables the beam; on timeout only the Top-1 is returned
};

struct QueryResult {
    std::vector<std::string> candidates;  // best first
    double greedy_ms = 0;
    double beam_ms = 0;
    bool beam_complete = false;
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
    // keys: raw input letters ("nhsj", "ni'hao"); context: text the user typed before, any length.
    QueryResult query(const std::string & keys, const std::string & context, const QueryOptions & options);

 private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace beam
