#include "engine.hpp"

#include "keys.hpp"
#include "store.hpp"
#include "llama.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <thread>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

namespace beam {

namespace {

using Clock = std::chrono::steady_clock;

double ms_since(Clock::time_point t0) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t0).count();
}

// Translations that differ only in case, spacing or punctuation are one candidate.
std::string translation_key(const std::string & text) {
    std::string key;
    for (unsigned char ch : text) {
        if (std::isalnum(ch) && ch < 128) key += (char) std::tolower(ch);
        else if (ch >= 128) key += (char) ch;
    }
    return key;
}

struct Beam {
    std::vector<llama_token> toks;
    float score;
    int seq;
    std::vector<float> probabilities;
};

}  // namespace

struct Engine::Impl {
    EngineOptions options;
    std::string name;
    std::string backend = "cpu";
    std::string prompt_version = kPromptV1;     // the base model's task format
    std::string decoding_strategy = "beam";
    std::string active_version = kPromptV1;     // format and scoring of the current mode
    std::string active_strategy = "beam";
    InputMode mode = InputMode::Ime;
    // English for an IME-only model: a second context on the same weights carries the translation
    // adapter and writes the English for the Chinese candidates the IME chose. The IME context
    // never sees the adapter, so ordinary input stays unchanged.
    static constexpr int kTranslateSeqs = 8;
    llama_adapter_lora * translate_adapter = nullptr;
    llama_context * tctx = nullptr;
    llama_memory_t tmem = nullptr;
    llama_batch tbatch = {};
    std::vector<llama_token> tcached;      // seq 0 of tctx: the last translation prompt
    llama_context_params cparams = {};
    bool greedy_finished = false;
    llama_model * model = nullptr;
    llama_context * ctx = nullptr;
    const llama_vocab * vocab = nullptr;
    llama_memory_t mem = nullptr;
    llama_batch batch = {};
    llama_adapter_lora * adapter = nullptr;
    int n_vocab = 0;
    std::unique_ptr<Pronunciations> pronunciation;

    std::vector<llama_token> cached;       // seq 0 tokens in the KV cache
    std::vector<llama_token> greedy_out;   // last Top-1 tokens (draft for the next query)
    std::vector<llama_token> prompt_toks;  // last prompt
    std::vector<float> first_logits;       // distribution of the first result token

    explicit Impl(const EngineOptions & o) : options(o) {
        name = std::filesystem::u8path(o.model_path).filename().u8string();
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level >= GGML_LOG_LEVEL_ERROR) fputs(text, stderr);
        }, nullptr);
        if (!o.backend_dir.empty()) {
#ifdef _WIN32
            // The pinned ggml path loader uses the ANSI code page on Windows.
            for (const auto * name : {L"ggml-cpu.dll", L"ggml-vulkan.dll"}) {
                if (o.gpu_layers == 0 && std::wstring(name) == L"ggml-vulkan.dll") continue;
                auto library = std::filesystem::u8path(o.backend_dir) / name;
                HMODULE module = LoadLibraryW(library.c_str());
                if (module) {
                    auto init = reinterpret_cast<ggml_backend_reg_t (*)()>(GetProcAddress(module, "ggml_backend_init"));
                    if (init) {
                        auto reg = init();
                        if (reg) ggml_backend_register(reg);
                    }
                    // Backends can own worker threads; retain the module until process exit.
                }
            }
#else
            if (o.gpu_layers == 0) {
                auto cpu = std::filesystem::path(o.backend_dir) / "libggml-cpu.so";
                ggml_backend_load(cpu.u8string().c_str());
            } else ggml_backend_load_all_from_path(o.backend_dir.c_str());
#endif
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
        char version[64];
        int length = llama_model_meta_val_str(model, "beam.prompt_version", version, sizeof(version));
        if (length > 0) {
            if (length >= (int)sizeof(version)) { llama_model_free(model); model = nullptr; throw std::runtime_error("unsupported prompt version"); }
            prompt_version.assign(version, length);
            if (prompt_version != kPromptV1 && !dual_prompt(prompt_version)) {
                llama_model_free(model); model = nullptr;
                throw std::runtime_error("unsupported prompt version");
            }
        }
        if (prompt_version == kPromptV4) {
            try { pronunciation = std::make_unique<Pronunciations>(std::filesystem::u8path(o.pinyin_path)); }
            catch (...) { llama_model_free(model); model = nullptr; throw; }
        }
        length = llama_model_meta_val_str(model, "beam.decoding_strategy", version, sizeof(version));
        if (length >= (int)sizeof(version)) { llama_model_free(model); model = nullptr; throw std::runtime_error("unsupported decoding strategy"); }
        if (length > 0) decoding_strategy.assign(version, length);
        if (decoding_strategy != "beam" && decoding_strategy != "greedy_first" && decoding_strategy != "field")
            { llama_model_free(model); model = nullptr; throw std::runtime_error("unsupported decoding strategy"); }
        active_version = prompt_version;
        active_strategy = decoding_strategy;
        if (mp.n_gpu_layers > 0) {
            auto device = ggml_backend_dev_by_type(GGML_BACKEND_DEVICE_TYPE_GPU);
            if (!device) device = ggml_backend_dev_by_type(GGML_BACKEND_DEVICE_TYPE_IGPU);
            if (device) {
                backend = ggml_backend_reg_name(ggml_backend_dev_backend_reg(device));
                std::transform(backend.begin(), backend.end(), backend.begin(), [](unsigned char ch) { return (char)std::tolower(ch); });
            }
        }
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
        cparams = cp;
        ctx = llama_init_from_model(model, cp);
        if (!ctx) return;
        vocab = llama_model_get_vocab(model);
        n_vocab = llama_vocab_n_tokens(vocab);
        mem = llama_get_memory(ctx);
        batch = llama_batch_init(std::max(512, o.beams), 0, 1);
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
        llama_batch_free(batch);
        llama_batch_free(tbatch);
        if (ctx) llama_free(ctx);
        if (tctx) llama_free(tctx);
        if (adapter) llama_adapter_lora_free(adapter);
        if (translate_adapter) llama_adapter_lora_free(translate_adapter);
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
        batch.n_tokens = 0;
        for (int i = start; i < (int) tokens.size(); ++i) {
            int j = batch.n_tokens++;
            batch.token[j] = tokens[i];
            batch.pos[j] = i;
            batch.n_seq_id[j] = 1;
            batch.seq_id[j][0] = 0;
            batch.logits[j] = i >= logits_from;
        }
        return llama_decode(ctx, batch) == 0;
    }

    bool apply_adapters() {
        float scale = 1.0f;
        return llama_set_adapters_lora(ctx, adapter ? &adapter : nullptr, adapter ? 1 : 0, adapter ? &scale : nullptr) == 0;
    }

    // Switches the task of a dual-mode model (one that translates by itself).
    bool select_mode(InputMode next) {
        if (mode == next) return true;
        reset_cache(); prompt_toks.clear(); first_logits.clear();
        mode = next;
        return true;
    }

    bool decode_translate(int n) {
        tbatch.n_tokens = n;
        return llama_decode(tctx, tbatch) == 0;
    }

    void add_translate_token(int & n, llama_token token, int pos, int seq, bool logits) {
        tbatch.token[n] = token;
        tbatch.pos[n] = pos;
        tbatch.n_seq_id[n] = 1;
        tbatch.seq_id[n][0] = seq;
        tbatch.logits[n] = logits;
        ++n;
    }

    // English for each Chinese candidate, decoded side by side and greedily: seq 0 holds the
    // shared prompt (reused across keystrokes), seq i + 1 continues it with "<Chinese i>\t".
    // A sequence still running at the deadline gets no English.
    bool translate(const std::string & keys, const std::string & context, const std::vector<std::string> & chinese,
                   Clock::time_point deadline, std::vector<std::string> & english) {
        english.assign(chinese.size(), "");
        const int n = std::min((int) chinese.size(), kTranslateSeqs);
        if (n == 0) return true;
        std::string text = task_prompt(keys, context, InputMode::TranslateEn, kPromptV4);
        auto prompt = tokenize(text);
        const int np = (int) prompt.size();
        const int max_new = 48;
        std::vector<std::vector<llama_token>> tails(n);
        int cells = np, queued = 0;
        for (int i = 0; i < n; ++i) {
            auto full = tokenize(text + chinese[i] + "\t");
            // The prompt must tokenize the same alone and followed by the candidate to share its cells.
            if ((int) full.size() <= np || !std::equal(prompt.begin(), prompt.end(), full.begin())) continue;
            tails[i].assign(full.begin() + np, full.end());
            cells += (int) tails[i].size() + max_new;
            queued += (int) tails[i].size();
        }
        if (np == 0 || cells > (int) llama_n_ctx(tctx) || np > tbatch_capacity || queued > tbatch_capacity) return false;

        for (int s = 1; s <= n; ++s) llama_memory_seq_rm(tmem, s, -1, -1);
        int common = 0;
        while (common < (int) tcached.size() && common < np && tcached[common] == prompt[common]) ++common;
        llama_memory_seq_rm(tmem, 0, common, -1);
        tcached.resize(common);
        int count = 0;
        for (int i = common; i < np; ++i) add_translate_token(count, prompt[i], i, 0, false);
        if (count && !decode_translate(count)) { llama_memory_clear(tmem, true); tcached.clear(); return false; }
        tcached = prompt;

        std::vector<int> active, row;
        count = 0;
        for (int i = 0; i < n; ++i) {
            if (tails[i].empty()) continue;
            llama_memory_seq_cp(tmem, 0, i + 1, 0, np);
            for (int k = 0; k < (int) tails[i].size(); ++k)
                add_translate_token(count, tails[i][k], np + k, i + 1, k + 1 == (int) tails[i].size());
            active.push_back(i);
            row.push_back(count - 1);
        }
        bool ok = !active.empty() && decode_translate(count);
        std::vector<std::vector<llama_token>> out(n);
        for (int step = 0; ok && !active.empty() && step < max_new && Clock::now() < deadline; ++step) {
            std::vector<int> next;
            count = 0;
            for (size_t a = 0; a < active.size(); ++a) {
                int i = active[a];
                llama_token token = argmax(llama_get_logits_ith(tctx, row[a]));
                if (stops(token)) { english[i] = detok(out[i]); continue; }
                out[i].push_back(token);
                add_translate_token(count, token, np + (int) (tails[i].size() + out[i].size()) - 1, i + 1, true);
                next.push_back(i);
            }
            active.swap(next);
            row.clear();
            for (int r = 0; r < count; ++r) row.push_back(r);
            if (count && !decode_translate(count)) ok = false;
        }
        for (int s = 1; s <= n; ++s) llama_memory_seq_rm(tmem, s, -1, -1);
        if (!ok) { llama_memory_clear(tmem, true); tcached.clear(); }
        return ok;
    }
    int tbatch_capacity = 512;

    void reset_cache() {
        llama_memory_clear(mem, true);
        cached.clear();
        greedy_out.clear();
        greedy_finished = false;
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
        greedy_finished = stops(next);
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
        std::vector<Beam> next;
        std::vector<std::pair<float, std::vector<llama_token>>> finished;
        struct Cand { float score; int parent; llama_token tok; };
        std::vector<Cand> cands;
        std::vector<std::pair<float, llama_token>> top;
        std::vector<int> parent_seqs;
        cands.reserve(2 * B * B);
        top.reserve(2 * B);
        parent_seqs.reserve(B);
        int set = 0;
        // Consume these borrowed rows before the next decode overwrites them.
        std::vector<const float *> logits = {first_logits.data()};
        logits.reserve(B);
        bool ok = true;
        for (int step = 0; step < max_new && !beams.empty(); ++step) {
            if (Clock::now() > deadline) { ok = false; break; }
            cands.clear();
            for (int b = 0; b < (int) beams.size(); ++b) {
                log_softmax_topk(logits[b], 2 * B, top);
                for (auto & [lp, t] : top) cands.push_back({beams[b].score + lp, b, t});
            }
            std::sort(cands.begin(), cands.end(), [](auto & a, auto & b) { return a.score > b.score; });
            next.resize(B);
            int n_next = 0;
            parent_seqs.clear();
            for (auto & c : cands) {
                if (n_next >= B) break;
                auto & t = beams[c.parent].toks;
                if (stops(c.tok)) {
                    if (!t.empty()) {
                        float score = c.score / (float) (t.size() + 1);
                        if (mode == InputMode::TranslateEn && active_strategy == "field") {
                            std::vector<std::string> pieces;
                            for (auto token : t) pieces.push_back(piece(token));
                            score = bilingual_score(beams[c.parent].probabilities, pieces, active_version);
                        }
                        finished.push_back({score, t});
                    }
                    continue;
                }
                auto & child = next[n_next++];
                child.toks = t;
                child.toks.push_back(c.tok);
                child.score = c.score;
                child.probabilities = beams[c.parent].probabilities;
                child.probabilities.push_back(c.score - beams[c.parent].score);
                parent_seqs.push_back(beams[c.parent].seq);
            }
            next.resize(n_next);
            if ((int) finished.size() >= B || next.empty()) break;
            // Rebuild KV for the new set: copy each parent's cells, then append the new token.
            int base = 1 + (1 - set) * B;
            for (int i = 0; i < B; ++i) llama_memory_seq_rm(mem, base + i, -1, -1);
            batch.n_tokens = 0;
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
            if (!decoded) { ok = false; break; }
            int old_base = 1 + set * B;
            for (int i = 0; i < B; ++i) llama_memory_seq_rm(mem, old_base + i, -1, -1);
            set = 1 - set;
            logits.clear();
            for (int i = 0; i < (int) next.size(); ++i) {
                logits.push_back(llama_get_logits_ith(ctx, i));
            }
            beams.swap(next);
        }
        for (int s = 1; s <= 2 * B; ++s) llama_memory_seq_rm(mem, s, -1, -1);
        if (!ok) return false;
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
const std::string & Engine::prompt_version() const { return impl_->prompt_version; }
bool Engine::supports(InputMode mode) const {
    return mode == InputMode::Ime || dual_prompt(impl_->prompt_version) || impl_->translate_adapter != nullptr;
}

bool Engine::set_translate_adapter(const std::string & path, std::string & error) {
    auto & m = *impl_;
    if (dual_prompt(m.prompt_version)) { error = "model_already_translates"; return false; }
    auto * next = llama_adapter_lora_init(m.model, path.c_str());
    if (!next) { error = "translate_adapter_load_failed"; return false; }
    char value[64];
    int length = llama_adapter_meta_val_str(next, "beam.prompt_version", value, sizeof(value));
    if (length <= 0 || length >= (int) sizeof(value) || std::string(value, length) != kPromptV4) {
        llama_adapter_lora_free(next); error = "translate_adapter_prompt_unsupported"; return false;
    }
    if (!m.tctx) {
        auto cp = m.cparams;
        cp.n_ctx = 1024;
        cp.n_seq_max = 1 + Impl::kTranslateSeqs;
        m.tctx = llama_init_from_model(m.model, cp);
        if (!m.tctx) { llama_adapter_lora_free(next); error = "translate_context_failed"; return false; }
        m.tmem = llama_get_memory(m.tctx);
        m.tbatch = llama_batch_init(m.tbatch_capacity, 0, 1);
    }
    float scale = 1.0f;
    if (llama_set_adapters_lora(m.tctx, &next, 1, &scale) != 0) {
        llama_set_adapters_lora(m.tctx, m.translate_adapter ? &m.translate_adapter : nullptr, m.translate_adapter ? 1 : 0,
                                m.translate_adapter ? &scale : nullptr);
        llama_adapter_lora_free(next); error = "translate_adapter_apply_failed"; return false;
    }
    if (m.translate_adapter) llama_adapter_lora_free(m.translate_adapter);
    m.translate_adapter = next;
    llama_memory_clear(m.tmem, true);
    m.tcached.clear();
    // Warm up the batch sizes of a translation so the first one is not slow.
    for (int n : {1, 8, 64}) {
        int count = 0;
        auto seed = m.tokenize("按");
        if (seed.empty()) break;
        for (int i = 0; i < n; ++i) m.add_translate_token(count, seed[0], i, 0, i + 1 == n);
        m.decode_translate(count);
        llama_memory_clear(m.tmem, true);
    }
    return true;
}

bool Engine::set_adapter(const std::string & path, std::string & error) {
    auto & m = *impl_;
    auto * next = path.empty() ? nullptr : llama_adapter_lora_init(m.model, path.c_str());
    if (!path.empty() && !next) { error = "adapter_load_failed"; return false; }
    auto * previous = m.adapter;
    m.adapter = next;
    if (!m.apply_adapters()) {
        m.adapter = previous;
        if (next) llama_adapter_lora_free(next);
        error = "adapter_apply_failed";
        return false;
    }
    m.reset_cache();
    m.prompt_toks.clear(); m.first_logits.clear();
    if (previous) llama_adapter_lora_free(previous);
    return true;
}

QueryResult Engine::query(const std::string & raw_keys, const std::string & context, const QueryOptions & options) {
    Impl & m = *impl_;
    QueryResult result;
    std::string keys = normalize_keys(raw_keys);
    if (keys.empty() || !ok()) return result;
    std::string c = utf8_tail(context, (size_t) m.options.context_chars);
    if (options.mode == InputMode::TranslateEn && m.translate_adapter) {
        // The IME picks the Chinese (with the personal adapter), the adapter writes its English.
        QueryOptions ime = options;
        ime.mode = InputMode::Ime;
        ime.max_candidates = std::min(Impl::kTranslateSeqs, options.max_candidates + 2);
        QueryResult chinese = query(raw_keys, context, ime);
        result.greedy_ms = chinese.greedy_ms;
        result.beam_ms = chinese.beam_ms;
        result.beam_complete = chinese.beam_complete;
        auto t0 = Clock::now();
        std::vector<std::string> english;
        if (!m.translate(keys, c, chinese.candidates, t0 + std::chrono::milliseconds(options.translate_budget_ms), english))
            return result;
        result.translate_ms = ms_since(t0);
        std::vector<std::string> seen;
        for (size_t i = 0; i < chinese.candidates.size() && (int) result.candidates.size() < options.max_candidates; ++i) {
            auto parsed = parse_candidate(chinese.candidates[i] + "\t" + english[i], keys, InputMode::TranslateEn, kPromptV4);
            if (english[i].empty() || !parsed || parsed->need_more_input) continue;
            auto key = translation_key(parsed->text);
            if (std::find(seen.begin(), seen.end(), key) != seen.end()) continue;
            seen.push_back(key);
            result.candidates.push_back(parsed->text);
            result.comments.push_back(parsed->comment);
        }
        return result;
    }
    if (!m.select_mode(options.mode)) return result;
    std::string prompt = task_prompt(keys, c, options.mode, m.active_version);
    int max_new = output_budget(options.mode, letter_count(keys));

    auto t0 = Clock::now();
    if (!m.greedy(prompt, max_new)) return result;
    result.greedy_ms = ms_since(t0);
    std::string top = m.detok(m.greedy_out);
    auto parse = [&](const std::string & text) {
        auto parsed = parse_candidate(text, keys, options.mode, m.active_version);
        if (parsed && options.mode == InputMode::TranslateEn && m.pronunciation && m.pronunciation->align(parsed->comment, keys).empty())
            return std::optional<TaskCandidate>{};
        return parsed;
    };

    std::vector<std::string> ranked, folded;
    if (options.beam_budget_ms > 0 && options.max_candidates > 1) {
        auto t1 = Clock::now();
        std::vector<std::pair<float, std::string>> fin;
        if (m.beam_search(max_new, t1 + std::chrono::milliseconds(options.beam_budget_ms), fin)) {
            // Beam order replaces the greedy guess; greedy is kept only if the beam missed it.
            // Candidates differing only in English letter case collapse to the best-ranked casing.
            for (auto & [s, text] : fin) {
                if ((int) ranked.size() >= options.max_candidates) break;
                auto parsed = parse(text);
                std::string f = parsed ? (options.mode == InputMode::Ime ? fold_case(parsed->text) : translation_key(parsed->text)) : "";
                if (parsed && std::find(folded.begin(), folded.end(), f) == folded.end()) {
                    ranked.push_back(text);
                    folded.push_back(f);
                }
            }
            result.beam_complete = true;
        }
        result.beam_ms = ms_since(t1);
    }
    auto parsed_top = !m.greedy_finished ? std::nullopt : parse(top);
    auto top_id = parsed_top ? (options.mode == InputMode::Ime ? fold_case(parsed_top->text) : translation_key(parsed_top->text)) : "";
    if (parsed_top && options.mode == InputMode::TranslateEn && m.active_strategy == "greedy_first") {
        ranked.erase(std::remove_if(ranked.begin(), ranked.end(), [&](const std::string & text) {
            return parse(text)->text == parsed_top->text;
        }), ranked.end());
        ranked.insert(ranked.begin(), top);
        if ((int)ranked.size() > options.max_candidates) ranked.pop_back();
    } else if (parsed_top && std::find(folded.begin(), folded.end(), top_id) == folded.end()) {
        if (ranked.empty()) ranked.push_back(top);
        else {
            if ((int) ranked.size() >= options.max_candidates) ranked.pop_back();
            ranked.push_back(top);
        }
    }
    for (const auto & text : ranked) {
        auto candidate = parse(text);
        if (candidate->need_more_input) {
            if (result.candidates.empty()) { result.translation_status = "need_more_input"; return result; }
            continue;
        }
        result.candidates.push_back(candidate->text);
        result.comments.push_back(candidate->comment);
    }
    return result;
}

}  // namespace beam
