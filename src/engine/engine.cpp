#include "engine.hpp"

#include "keys.hpp"
#include "llama.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <thread>

namespace beam {

namespace {

using Clock = std::chrono::steady_clock;

double ms_since(Clock::time_point t0) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

struct Beam {
    std::vector<llama_token> toks;
    float score;
    int seq;
};

}  // namespace

struct Engine::Impl {
    EngineOptions options;
    std::string name;
    std::string backend = "cpu";
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    llama_memory_t mem = nullptr;
    int n_vocab = 0;

    std::vector<llama_token> cached;       // seq 0 tokens in the KV cache
    std::vector<llama_token> greedy_out;   // last Top-1 tokens (draft for the next query)
    std::vector<llama_token> prompt_toks;  // last prompt
    std::vector<float> first_logits;       // distribution of the first result token

    explicit Impl(const EngineOptions & o) : options(o) {
        name = std::filesystem::path(o.model_path).filename().string();
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level >= GGML_LOG_LEVEL_ERROR) fputs(text, stderr);
        }, nullptr);
        if (!o.backend_dir.empty()) {
            if (o.gpu_layers == 0) {
#ifdef _WIN32
                auto cpu = std::filesystem::path(o.backend_dir) / "ggml-cpu.dll";
#else
                auto cpu = std::filesystem::path(o.backend_dir) / "libggml-cpu.so";
#endif
                ggml_backend_load(cpu.string().c_str());
            } else ggml_backend_load_all_from_path(o.backend_dir.c_str());
        } else ggml_backend_load_all();
        auto mp = llama_model_default_params();
        mp.n_gpu_layers = o.gpu_layers;
        // --ngl 0 means no GPU at all: an empty device list keeps llama.cpp from creating GPU
        // buffers or offloading ops (a broken GPU driver must not take the CPU path down with it).
        static ggml_backend_dev_t no_devices[] = {nullptr};
        if (o.gpu_layers == 0) mp.devices = no_devices;
        model = llama_model_load_from_file(o.model_path.c_str(), mp);
        if (!model && o.gpu_layers != 0) {
            mp.n_gpu_layers = 0;
            mp.devices = no_devices;
            model = llama_model_load_from_file(o.model_path.c_str(), mp);
        }
        if (!model) return;
        if (mp.n_gpu_layers > 0 && ggml_backend_dev_by_type(GGML_BACKEND_DEVICE_TYPE_GPU)) backend = "gpu";
        auto cp = llama_context_default_params();
        cp.n_ctx = 1024;
        cp.n_batch = 512;
        cp.n_ubatch = 512;
        cp.n_seq_max = 1 + 2 * o.beams;
        cp.kv_unified = true;
        cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        if (backend == "cpu") cp.op_offload = false;
        int threads = o.threads > 0 ? o.threads : std::max(1u, std::thread::hardware_concurrency() / 2);
        cp.n_threads = cp.n_threads_batch = threads;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) return;
        vocab = llama_model_get_vocab(model);
        n_vocab = llama_vocab_n_tokens(vocab);
        mem = llama_get_memory(ctx);
        // Warm up the batch sizes used per keystroke so the first keys are not slow.
        for (int n = 1; n <= 64; n *= 2) {
            auto seed = tokenize("按");
            if (seed.empty()) break;
            std::vector<llama_token> warm(n, seed[0]);
            llama_memory_clear(mem, true);
            decode_seq0(warm, 0, 0);
        }
        llama_memory_clear(mem, true);
    }

    ~Impl() {
        if (ctx) llama_free(ctx);
        if (model) llama_model_free(model);
    }

    std::vector<llama_token> tokenize(const std::string & text) const {
        std::vector<llama_token> out(text.size() + 8);
        int n = llama_tokenize(vocab, text.c_str(), (int) text.size(), out.data(), (int) out.size(), false, false);
        if (n < 0) {
            out.resize(-n);
            n = llama_tokenize(vocab, text.c_str(), (int)text.size(), out.data(), (int)out.size(), false, false);
        }
        out.resize(std::max(n, 0));
        return out;
    }

    std::string piece(llama_token token) const {
        char buf[64];
        int n = llama_token_to_piece(vocab, token, buf, sizeof(buf), 0, false);
        if (n >= 0) return std::string(buf, n);
        std::string out(-n, '\0');
        n = llama_token_to_piece(vocab, token, out.data(), (int)out.size(), 0, false);
        out.resize(std::max(n, 0));
        return out;
    }

    std::string detok(const std::vector<llama_token> & tokens) const {
        std::string s;
        for (auto t : tokens) s += piece(t);
        return s;
    }

    llama_token argmax(const float * logits) const {
        return (llama_token) (std::max_element(logits, logits + n_vocab) - logits);
    }

    bool stops(llama_token t) const {
        return llama_vocab_is_eog(vocab, t) || piece(t).find('\n') != std::string::npos;
    }

    bool decode_seq0(const std::vector<llama_token> & tokens, int start, int logits_from) {
        llama_batch batch = llama_batch_init((int) tokens.size() - start, 0, 1);
        for (int i = start; i < (int) tokens.size(); ++i) {
            int j = batch.n_tokens++;
            batch.token[j] = tokens[i];
            batch.pos[j] = i;
            batch.n_seq_id[j] = 1;
            batch.seq_id[j][0] = 0;
            batch.logits[j] = i >= logits_from;
        }
        bool ok = llama_decode(ctx, batch) == 0;
        llama_batch_free(batch);
        return ok;
    }

    void reset_cache() {
        llama_memory_clear(mem, true);
        cached.clear();
        greedy_out.clear();
    }

    // Top-1 with KV prefix reuse; the previous Top-1 is verified as a draft in the same batch.
    bool greedy(const std::string & prompt, int max_new) {
        std::vector<llama_token> seq = tokenize(prompt);
        int np = (int) seq.size();
        if (np == 0 || np + max_new > 512) { reset_cache(); return false; }
        prompt_toks = seq;
        std::vector<llama_token> draft = greedy_out;
        if ((int) draft.size() > max_new) draft.resize(max_new);
        seq.insert(seq.end(), draft.begin(), draft.end());
        int common = 0;
        while (common < (int) cached.size() && common < (int) seq.size() && cached[common] == seq[common]) ++common;
        int start = std::min(common, np - 1);
        llama_memory_seq_rm(mem, 0, start, -1);
        if (!decode_seq0(seq, start, np - 1)) { reset_cache(); return false; }
        const float * l0 = llama_get_logits_ith(ctx, np - 1 - start);
        first_logits.assign(l0, l0 + n_vocab);

        std::vector<llama_token> out;
        llama_token next = -1;
        int accepted = 0;
        for (int j = 0; j <= (int) draft.size(); ++j) {
            llama_token tok = argmax(llama_get_logits_ith(ctx, np - 1 - start + j));
            if (j < (int) draft.size() && tok == draft[j] && !stops(tok)) { out.push_back(tok); ++accepted; continue; }
            next = tok;
            break;
        }
        llama_memory_seq_rm(mem, 0, np + accepted, -1);
        cached.assign(seq.begin(), seq.begin() + np + accepted);
        while (!stops(next) && (int) out.size() < max_new) {
            out.push_back(next);
            cached.push_back(next);
            if (!decode_seq0(cached, (int) cached.size() - 1, (int) cached.size() - 1)) { reset_cache(); return false; }
            next = argmax(llama_get_logits_ith(ctx, 0));
        }
        greedy_out = out;
        return true;
    }

    // k best tokens with their log-probabilities: one pass for max and top-k, one for the
    // normalizer.
    void log_softmax_topk(const float * logits, int k, std::vector<std::pair<float, llama_token>> & out) const {
        out.assign(k, {-INFINITY, -1});  // sorted descending
        float mx = -INFINITY;
        for (int i = 0; i < n_vocab; ++i) {
            float v = logits[i];
            mx = std::max(mx, v);
            if (v <= out[k - 1].first) continue;
            int j = k - 1;
            while (j > 0 && out[j - 1].first < v) { out[j] = out[j - 1]; --j; }
            out[j] = {v, (llama_token) i};
        }
        // Logits more than 20 below the max add < 2e-9 each to the normalizer; skip their exp().
        float sum = 0, floor = mx - 20.f;
        for (int i = 0; i < n_vocab; ++i)
            if (logits[i] > floor) sum += std::exp(logits[i] - mx);
        float lse = mx + std::log(sum);
        for (auto & entry : out) entry.first -= lse;
    }

    // Beam search over seqs 1..2*beams sharing the prompt cells of seq 0 (filled by greedy()).
    // Returns false when the deadline passes first.
    bool beam_search(int max_new, Clock::time_point deadline, std::vector<std::pair<float, std::string>> & out) {
        const int B = options.beams;
        int np = (int) prompt_toks.size();
        std::vector<Beam> beams = {{{}, 0.f, -1}};
        std::vector<std::pair<float, std::vector<llama_token>>> finished;
        int set = 0;
        std::vector<std::vector<float>> logits = {first_logits};
        bool ok = true;
        for (int step = 0; step < max_new && !beams.empty(); ++step) {
            if (Clock::now() > deadline) { ok = false; break; }
            struct Cand { float score; int parent; llama_token tok; };
            std::vector<Cand> cands;
            std::vector<std::pair<float, llama_token>> top;
            for (int b = 0; b < (int) beams.size(); ++b) {
                log_softmax_topk(logits[b].data(), 2 * B, top);
                for (auto & [lp, t] : top) cands.push_back({beams[b].score + lp, b, t});
            }
            std::sort(cands.begin(), cands.end(), [](auto & a, auto & b) { return a.score > b.score; });
            std::vector<Beam> next;
            std::vector<int> parent_seqs;
            for (auto & c : cands) {
                if ((int) next.size() >= B) break;
                auto & t = beams[c.parent].toks;
                if (stops(c.tok)) {
                    if (!t.empty()) finished.push_back({c.score / (float) (t.size() + 1), t});
                    continue;
                }
                next.push_back({t, c.score, -1});
                next.back().toks.push_back(c.tok);
                parent_seqs.push_back(beams[c.parent].seq);
            }
            if ((int) finished.size() >= B || next.empty()) break;
            // Rebuild KV for the new set: copy each parent's cells, then append the new token.
            int base = 1 + (1 - set) * B;
            for (int i = 0; i < B; ++i) llama_memory_seq_rm(mem, base + i, -1, -1);
            llama_batch batch = llama_batch_init((int) next.size(), 0, 1);
            for (int i = 0; i < (int) next.size(); ++i) {
                int parent_seq = parent_seqs[i];
                int dst = base + i;
                if (parent_seq < 0) llama_memory_seq_cp(mem, 0, dst, 0, np);
                else llama_memory_seq_cp(mem, parent_seq, dst, -1, -1);
                next[i].seq = dst;
                int j = batch.n_tokens++;
                batch.token[j] = next[i].toks.back();
                batch.pos[j] = np + (int) next[i].toks.size() - 1;
                batch.n_seq_id[j] = 1;
                batch.seq_id[j][0] = dst;
                batch.logits[j] = true;
            }
            bool decoded = llama_decode(ctx, batch) == 0;
            llama_batch_free(batch);
            if (!decoded) { ok = false; break; }
            int old_base = 1 + set * B;
            for (int i = 0; i < B; ++i) llama_memory_seq_rm(mem, old_base + i, -1, -1);
            set = 1 - set;
            logits.clear();
            for (int i = 0; i < (int) next.size(); ++i) {
                const float * l = llama_get_logits_ith(ctx, i);
                logits.emplace_back(l, l + n_vocab);
            }
            beams = next;
        }
        for (int s = 1; s <= 2 * B; ++s) llama_memory_seq_rm(mem, s, -1, -1);
        if (!ok) return false;
        for (auto & b : beams) finished.push_back({b.score / (float) (b.toks.size() + 1), b.toks});
        std::sort(finished.begin(), finished.end(), [](auto & a, auto & b) { return a.first > b.first; });
        out.clear();
        for (auto & [s, t] : finished) out.push_back({s, detok(t)});
        return true;
    }
};

Engine::Engine(const EngineOptions & options) : impl_(std::make_unique<Impl>(options)) {}
Engine::~Engine() = default;

bool Engine::ok() const { return impl_->ctx != nullptr; }
const std::string & Engine::model_name() const { return impl_->name; }
const std::string & Engine::backend_name() const { return impl_->backend; }

QueryResult Engine::query(const std::string & raw_keys, const std::string & context, const QueryOptions & options) {
    Impl & m = *impl_;
    QueryResult result;
    std::string keys = normalize_keys(raw_keys);
    if (keys.empty() || !ok()) return result;
    std::string c = utf8_tail(context, (size_t) m.options.context_chars);
    std::string prompt = (c.empty() ? "" : "上文：" + c + "\n") + "按键：" + spaced(keys) + "\n结果：";
    int max_new = 2 * letter_count(keys) + 8;

    auto t0 = Clock::now();
    if (!m.greedy(prompt, max_new)) return result;
    result.greedy_ms = ms_since(t0);
    std::string top = m.detok(m.greedy_out);

    std::vector<std::string> ranked, folded;
    if (options.beam_budget_ms > 0 && options.max_candidates > 1) {
        auto t1 = Clock::now();
        std::vector<std::pair<float, std::string>> fin;
        if (m.beam_search(max_new, t1 + std::chrono::milliseconds(options.beam_budget_ms), fin)) {
            // Beam order replaces the greedy guess; greedy is kept only if the beam missed it.
            // Candidates differing only in English letter case collapse to the best-ranked casing.
            for (auto & [s, text] : fin) {
                if ((int) ranked.size() >= options.max_candidates) break;
                std::string f = fold_case(text);
                if (aligns(text, keys) && std::find(folded.begin(), folded.end(), f) == folded.end()) {
                    ranked.push_back(text);
                    folded.push_back(f);
                }
            }
            result.beam_complete = true;
        }
        result.beam_ms = ms_since(t1);
    }
    if (aligns(top, keys) && std::find(folded.begin(), folded.end(), fold_case(top)) == folded.end()) {
        if (ranked.empty()) ranked.push_back(top);
        else {
            if ((int) ranked.size() >= options.max_candidates) ranked.pop_back();
            ranked.push_back(top);
        }
    }
    result.candidates = ranked;
    return result;
}

}  // namespace beam
