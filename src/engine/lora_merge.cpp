#include "lora_merge.hpp"

#include "ggml-backend.h"
#include "ggml.h"
#include "gguf.h"
#include "llama.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <thread>
#include <vector>
#if defined(__x86_64__) && defined(__GNUC__)
#include <immintrin.h>
#define BEAM_MERGE_AVX2 1
#endif

namespace beam {

namespace {

constexpr size_t kBlockBytes = 34;  // block_q8_0: f16 scale + 32 x int8
constexpr int kBlockValues = 32;

struct Target {
    std::string name;
    ggml_tensor * tensor = nullptr;
    int64_t in = 0, out = 0, rank = 0;
    size_t bytes = 0;
    uint64_t file_offset = 0;
    std::vector<float> a;  // [in / 32][rank][32]: one column block's slice of A is contiguous
    std::vector<float> b;  // [out][rank], already multiplied by alpha / rank
};

bool ends_with(const std::string & text, const char * suffix) {
    size_t n = std::strlen(suffix);
    return text.size() >= n && text.compare(text.size() - n, n, suffix) == 0;
}

void to_float(const ggml_tensor * t, std::vector<float> & out) {
    int64_t count = ggml_nelements(t);
    out.resize((size_t) count);
    if (t->type == GGML_TYPE_F32) { std::memcpy(out.data(), t->data, (size_t) count * sizeof(float)); return; }
    ggml_get_type_traits(t->type)->to_float(t->data, out.data(), count);
}

float block_scale(const uint8_t * block) {
    ggml_fp16_t half;
    std::memcpy(&half, block, sizeof(half));
    return ggml_fp16_to_fp32(half);
}

// Rows [first, last) of one matrix: each value moves by round((B * A)[row][value] / scale)
// quantization steps, kept inside the int8 range. A block's 32 columns are one unit of work.
void merge_rows_scalar(const Target & t, uint8_t * bytes, int64_t first, int64_t last) {
    const int64_t blocks = t.in / kBlockValues;
    for (int64_t row = first; row < last; ++row) {
        const float * b = t.b.data() + row * t.rank;
        for (int64_t block = 0; block < blocks; ++block) {
            uint8_t * data = bytes + (size_t) (row * blocks + block) * kBlockBytes;
            float scale = block_scale(data);
            if (!(scale > 0.0f)) continue;
            float acc[kBlockValues] = {};
            for (int64_t k = 0; k < t.rank; ++k) {
                const float * a = t.a.data() + (block * t.rank + k) * kBlockValues;
                for (int j = 0; j < kBlockValues; ++j) acc[j] += b[k] * a[j];
            }
            auto * q = reinterpret_cast<int8_t *>(data + 2);
            for (int j = 0; j < kBlockValues; ++j)
                q[j] = (int8_t) std::clamp((int) q[j] + (int) std::lrintf(acc[j] / scale), -127, 127);
        }
    }
}

#ifdef BEAM_MERGE_AVX2
__attribute__((target("avx2,fma")))
void merge_rows_avx2(const Target & t, uint8_t * bytes, int64_t first, int64_t last) {
    const int64_t blocks = t.in / kBlockValues;
    const __m256i low = _mm256_set1_epi32(-127), high = _mm256_set1_epi32(127);
    // Columns outermost: the rank x 32 slice of A (4 KiB) stays in L1 while every row passes over it.
    for (int64_t block = 0; block < blocks; ++block) {
        const float * a = t.a.data() + block * t.rank * kBlockValues;
        for (int64_t row = first; row < last; ++row) {
            uint8_t * data = bytes + (size_t) (row * blocks + block) * kBlockBytes;
            float scale = block_scale(data);
            if (!(scale > 0.0f)) continue;
            const float * b = t.b.data() + row * t.rank;
            __m256 acc0 = _mm256_setzero_ps(), acc1 = acc0, acc2 = acc0, acc3 = acc0;
            for (int64_t k = 0; k < t.rank; ++k) {
                __m256 w = _mm256_broadcast_ss(b + k);
                const float * source = a + k * kBlockValues;
                acc0 = _mm256_fmadd_ps(w, _mm256_loadu_ps(source), acc0);
                acc1 = _mm256_fmadd_ps(w, _mm256_loadu_ps(source + 8), acc1);
                acc2 = _mm256_fmadd_ps(w, _mm256_loadu_ps(source + 16), acc2);
                acc3 = _mm256_fmadd_ps(w, _mm256_loadu_ps(source + 24), acc3);
            }
            const __m256 inverse = _mm256_set1_ps(1.0f / scale);
            const __m256 sums[4] = {acc0, acc1, acc2, acc3};
            for (int g = 0; g < 4; ++g) {
                __m256i steps = _mm256_cvtps_epi32(_mm256_mul_ps(sums[g], inverse));  // round to nearest even
                __m256i q = _mm256_cvtepi8_epi32(_mm_loadl_epi64(reinterpret_cast<const __m128i *>(data + 2 + 8 * g)));
                __m256i v = _mm256_min_epi32(_mm256_max_epi32(_mm256_add_epi32(q, steps), low), high);
                __m128i narrow = _mm_packs_epi32(_mm256_castsi256_si128(v), _mm256_extracti128_si256(v, 1));
                narrow = _mm_packs_epi16(narrow, narrow);
                _mm_storel_epi64(reinterpret_cast<__m128i *>(data + 2 + 8 * g), narrow);
            }
        }
    }
}
#endif

void merge_rows(const Target & t, uint8_t * bytes, int64_t first, int64_t last) {
#ifdef BEAM_MERGE_AVX2
    static const bool avx2 = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma");
    if (avx2) { merge_rows_avx2(t, bytes, first, last); return; }
#endif
    merge_rows_scalar(t, bytes, first, last);
}

}  // namespace

struct Q8LoraMerge::Impl {
    std::string model_path;
    std::vector<Target> targets;
    int threads = 1;
    bool is_merged = false;
    double ms = 0;

    // The original blocks of one target, straight from the model file.
    bool read_base(const Target & t, std::vector<uint8_t> & bytes) const {
        std::ifstream file(std::filesystem::u8path(model_path), std::ios::binary);
        if (!file) return false;
        bytes.resize(t.bytes);
        file.seekg((std::streamoff) t.file_offset);
        file.read(reinterpret_cast<char *>(bytes.data()), (std::streamsize) t.bytes);
        return (size_t) file.gcount() == t.bytes;
    }

    bool write(bool with_adapter) {
        auto t0 = std::chrono::steady_clock::now();
        std::vector<uint8_t> bytes;
        for (const auto & t : targets) {
            if (!read_base(t, bytes)) return false;
            if (with_adapter) {
                int workers = (int) std::min<int64_t>(threads, t.out);
                std::vector<std::thread> pool;
                for (int w = 1; w < workers; ++w)
                    pool.emplace_back(merge_rows, std::cref(t), bytes.data(), t.out * w / workers, t.out * (w + 1) / workers);
                merge_rows(t, bytes.data(), 0, t.out / workers);
                for (auto & worker : pool) worker.join();
            }
            ggml_backend_tensor_set(t.tensor, bytes.data(), 0, t.bytes);
        }
        ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        return true;
    }
};

Q8LoraMerge::Q8LoraMerge() : impl_(new Impl) {}
Q8LoraMerge::~Q8LoraMerge() = default;
bool Q8LoraMerge::merged() const { return impl_->is_merged; }
double Q8LoraMerge::last_ms() const { return impl_->ms; }

bool Q8LoraMerge::apply() {
    auto & m = *impl_;
    if (m.is_merged) return true;
    m.is_merged = true;  // a failed write may leave a mix: restore() must rewrite everything
    return m.write(true);
}

bool Q8LoraMerge::restore() {
    auto & m = *impl_;
    if (!m.is_merged) return true;
    if (!m.write(false)) return false;
    m.is_merged = false;
    return true;
}

std::unique_ptr<Q8LoraMerge> Q8LoraMerge::open(llama_model * model, const std::string & model_path,
                                               const std::string & adapter_path, int threads, std::string & error) {
    std::unique_ptr<Q8LoraMerge> merge(new Q8LoraMerge);
    auto & m = *merge->impl_;
    m.model_path = model_path;
    m.threads = std::max(1, threads);

    ggml_context * adapter_ctx = nullptr;
    gguf_init_params adapter_params = {false, &adapter_ctx};
    gguf_context * adapter = gguf_init_from_file(adapter_path.c_str(), adapter_params);
    if (!adapter) { error = "merge_adapter_unreadable"; return nullptr; }
    struct Cleanup {
        gguf_context * adapter; ggml_context * adapter_ctx; gguf_context * base = nullptr;
        ~Cleanup() { gguf_free(adapter); ggml_free(adapter_ctx); if (base) gguf_free(base); }
    } cleanup{adapter, adapter_ctx};

    long long alpha_key = gguf_find_key(adapter, "adapter.lora.alpha");
    if (alpha_key < 0) { error = "merge_adapter_alpha_missing"; return nullptr; }
    float alpha = gguf_get_val_f32(adapter, alpha_key);

    gguf_init_params base_params = {true, nullptr};
    gguf_context * base = gguf_init_from_file(model_path.c_str(), base_params);
    if (!base) { error = "merge_model_unreadable"; return nullptr; }
    cleanup.base = base;
    uint64_t data_offset = gguf_get_data_offset(base);

    std::map<std::string, ggml_tensor *> a_tensors, b_tensors;
    for (ggml_tensor * t = ggml_get_first_tensor(adapter_ctx); t; t = ggml_get_next_tensor(adapter_ctx, t)) {
        std::string name = t->name;
        if (ends_with(name, ".lora_a")) a_tensors[name.substr(0, name.size() - 7)] = t;
        else if (ends_with(name, ".lora_b")) b_tensors[name.substr(0, name.size() - 7)] = t;
    }
    if (a_tensors.empty() || a_tensors.size() != b_tensors.size()) { error = "merge_adapter_incomplete"; return nullptr; }

    for (const auto & [name, a] : a_tensors) {
        auto b_it = b_tensors.find(name);
        if (b_it == b_tensors.end()) { error = "merge_adapter_incomplete"; return nullptr; }
        ggml_tensor * b = b_it->second;
        ggml_tensor * weight = llama_model_get_tensor(model, name.c_str());
        long long index = gguf_find_tensor(base, name.c_str());
        if (!weight || index < 0) { error = "merge_tensor_missing"; return nullptr; }
        if (weight->type != GGML_TYPE_Q8_0 || gguf_get_tensor_type(base, index) != GGML_TYPE_Q8_0) {
            error = "merge_needs_q8_0"; return nullptr;
        }
        Target t;
        t.name = name;
        t.tensor = weight;
        t.in = weight->ne[0]; t.out = weight->ne[1]; t.rank = a->ne[1];
        if (weight->ne[2] != 1 || weight->ne[3] != 1 || t.in % kBlockValues != 0 || a->ne[0] != t.in ||
            b->ne[0] != t.rank || b->ne[1] != t.out || t.rank <= 0) { error = "merge_shape_mismatch"; return nullptr; }
        t.bytes = ggml_nbytes(weight);
        if (t.bytes != gguf_get_tensor_size(base, index) || t.bytes != (size_t) (t.in / kBlockValues * t.out) * kBlockBytes) {
            error = "merge_shape_mismatch"; return nullptr;
        }
        t.file_offset = data_offset + gguf_get_tensor_offset(base, index);
        std::vector<float> a_values;
        to_float(a, a_values);  // [rank][in]
        t.a.resize(a_values.size());
        for (int64_t k = 0; k < t.rank; ++k)
            for (int64_t i = 0; i < t.in; ++i)
                t.a[(size_t) (((i / kBlockValues) * t.rank + k) * kBlockValues + i % kBlockValues)] = a_values[(size_t) (k * t.in + i)];
        to_float(b, t.b);
        float scale = alpha / (float) t.rank;
        for (auto & v : t.b) v *= scale;
        m.targets.push_back(std::move(t));
    }
    return merge;
}

}  // namespace beam
