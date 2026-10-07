// SPDX-License-Identifier: Apache-2.0
#include "loss.hpp"
#include "store.hpp"
#include "keys.hpp"
#include "llama.h"
#include "llama-adapter.h"
#include "ggml-backend.h"
#include "ggml-opt.h"
#include "gguf.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <numeric>
#include <random>
#include <set>
#include <sstream>
#include <stdexcept>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <bcrypt.h>
#else
#include <openssl/evp.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;
using Json = nlohmann::json;
constexpr const char * recipe = "beam-personal-r8-qvac-v1";
constexpr int steps = 24, batch_size = 16;

static Json read_json(const fs::path & path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("cannot read " + path.u8string());
    return Json::parse(stream);
}

static void write_json(const fs::path & path, const Json & value) {
    auto temporary = path; temporary += ".tmp";
    std::ofstream stream(temporary);
    stream << value.dump() << '\n'; stream.close();
    if (!stream) throw std::runtime_error("cannot write " + temporary.u8string());
#ifdef _WIN32
    if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("cannot replace training state");
#else
    fs::rename(temporary, path);
#endif
}

static std::string digest(std::istream & input) {
    std::array<unsigned char, 32> bytes{};
    std::array<char, 65536> buffer{};
#ifdef _WIN32
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) != 0)
        throw std::runtime_error("SHA256 unavailable");
    if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) != 0) {
        BCryptCloseAlgorithmProvider(algorithm, 0); throw std::runtime_error("SHA256 init failed");
    }
    bool ok = true;
    while (input.read(buffer.data(), buffer.size()) || input.gcount())
        if (BCryptHashData(hash, reinterpret_cast<PUCHAR>(buffer.data()), (ULONG)input.gcount(), 0) != 0) ok = false;
    ok = ok && BCryptFinishHash(hash, bytes.data(), bytes.size(), 0) == 0;
    BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm, 0);
#else
    auto hash = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    bool ok = hash && EVP_DigestInit_ex(hash.get(), EVP_sha256(), nullptr) == 1;
    while (ok && (input.read(buffer.data(), buffer.size()) || input.gcount()))
        ok = EVP_DigestUpdate(hash.get(), buffer.data(), input.gcount()) == 1;
    ok = ok && EVP_DigestFinal_ex(hash.get(), bytes.data(), nullptr) == 1;
#endif
    if (!ok || input.bad()) throw std::runtime_error("SHA256 failed");
    std::ostringstream result;
    for (auto value : bytes) result << std::hex << std::setw(2) << std::setfill('0') << (unsigned)value;
    return result.str();
}

static std::string digest(const std::string & text) { std::istringstream input(text); return digest(input); }

struct Paused {};
using Model = std::unique_ptr<llama_model, decltype(&llama_model_free)>;
using Context = std::unique_ptr<llama_context, decltype(&llama_free)>;

struct Trainer {
    Json job, component, samples, schedule;
    fs::path root, learning, cache;
    std::string base_hash, backend;
    beam::Pronunciations pronunciation;
    Model model{nullptr, llama_model_free};
    Context context{nullptr, llama_free};
    const llama_vocab * vocab = nullptr;
    llama_adapter_lora * adapter = nullptr;
    beam::TrainerLoss loss;
    int completed = 0;
    int width = 128;
    std::chrono::steady_clock::time_point started = std::chrono::steady_clock::now();

    explicit Trainer(const fs::path & path)
        : job(read_json(path)), component(job.at("component")),
          root(fs::u8path(job.at("directory").get<std::string>())), learning(root.parent_path().parent_path()),
          pronunciation(fs::u8path(component.at("pinyin").get<std::string>())) {
        if (component.value("schemaVersion", 0) != 2 || component.value("recipe", "") != recipe ||
            component.value("prompt_version", "") != "keys_llm_v1") throw std::runtime_error("incompatible trainer component");
        backend = component.at("backend");
        if (backend != "vulkan" && backend != "cpu") throw std::runtime_error("unsupported training backend");
        std::ifstream input(fs::u8path(job.at("model").get<std::string>()), std::ios::binary);
        if (!input) throw std::runtime_error("cannot open the inference GGUF");
        base_hash = digest(input);
        if (base_hash != job.at("base_sha256") || base_hash != component.at("base_sha256"))
            throw std::runtime_error("training base does not match the inference GGUF");
        cache = learning / "teacher-cache" / base_hash / recipe;
        fs::create_directories(cache);
        std::vector<fs::directory_entry> entries;
        uintmax_t size = 0;
        for (const auto & entry : fs::directory_iterator(cache)) if (entry.path().extension() == ".json") {
            size += entry.file_size(); entries.push_back(entry);
        }
        std::sort(entries.begin(), entries.end(), [](const auto & a, const auto & b) { return a.last_write_time() < b.last_write_time(); });
        for (const auto & entry : entries) {
            if (size <= 64 * 1024 * 1024) break;
            size -= entry.file_size(); fs::remove(entry.path());
        }
    }

    void check_pause() const { if (fs::exists(root / "pause.json")) throw Paused{}; }
    void progress(const char * phase) const {
        std::ofstream stream(root / "progress.jsonl", std::ios::app);
        stream << Json{{"state", "running"}, {"phase", phase}, {"step", completed / batch_size},
            {"microbatch", completed % batch_size}, {"total", steps}, {"backend", backend}}.dump() << '\n';
        stream.close();
        if (!stream) throw std::runtime_error("cannot write training progress");
    }
    std::vector<llama_token> tokenize(const std::string & text) const {
        std::vector<llama_token> tokens(text.size() + 16);
        int n = llama_tokenize(vocab, text.data(), (int)text.size(), tokens.data(), (int)tokens.size(), false, true);
        if (n < 0) { tokens.resize(-n); n = llama_tokenize(vocab, text.data(), (int)text.size(), tokens.data(), (int)tokens.size(), false, true); }
        if (n < 0) throw std::runtime_error("tokenization failed");
        tokens.resize(n); return tokens;
    }
    std::string prompt(const Json & row) const {
        auto text = beam::utf8_tail(row.value("context", ""), 64);
        return (text.empty() ? "" : "上文：" + text + "\n") + "按键：" + beam::spaced(beam::normalize_keys(row.at("keys"))) + "\n结果：";
    }
    Json encode(const Json & row) const {
        auto prefix = tokenize(prompt(row)), target = tokenize(row.at("target"));
        target.push_back(llama_vocab_eos(vocab));
        const size_t start = prefix.size();
        prefix.insert(prefix.end(), target.begin(), target.end());
        if (start == 0 || prefix.size() > 256) throw std::runtime_error("learning example exceeds model input limit");
        return {{"tokens", prefix}, {"start", start}, {"target_count", target.size()}};
    }
    Json variants(const Json & row) const {
        Json result = Json::array({row});
        std::set<std::string> seen{beam::normalize_keys(row.at("keys"))};
        const auto codes = pronunciation.codes(row.at("target"), row.at("keys"));
        for (const auto & keys : {codes.first, codes.second}) if (!keys.empty() && keys.size() <= 40 && seen.insert(keys).second) {
            auto item = row; item["keys"] = keys; result.push_back(item);
        }
        return result;
    }
    void create_context(int size, bool training) {
        context.reset();
        auto params = llama_context_default_params();
        params.n_ctx = params.n_batch = params.n_ubatch = size;
        params.n_threads = params.n_threads_batch = 4;
        params.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_DISABLED;
        params.training = training;
        context.reset(llama_init_from_model(model.get(), params));
        if (!context) throw std::runtime_error("cannot initialize training context");
        if (training && llama_n_ctx(context.get()) != (uint32_t)size)
            throw std::runtime_error("training context size differs from the sample width");
    }
    void load_model() {
        check_pause(); progress("loading");
        std::vector<ggml_backend_dev_t> devices;
        if (backend == "vulkan") {
            auto * registry = ggml_backend_reg_by_name("Vulkan");
            if (!registry || ggml_backend_reg_dev_count(registry) == 0)
                throw std::runtime_error("Vulkan training requires a working Vulkan GPU driver");
            devices.push_back(ggml_backend_reg_dev_get(registry, 0));
        }
        devices.push_back(nullptr);
        auto params = llama_model_default_params();
        params.devices = devices.data();
        params.n_gpu_layers = backend == "vulkan" ? 999 : 0;
        params.load_mode = LLAMA_LOAD_MODE_NONE;
        params.use_extra_bufts = false;
        model.reset(llama_model_load_from_file(job.at("model").get<std::string>().c_str(), params));
        if (!model) throw std::runtime_error("cannot load the inference GGUF for training");
        vocab = llama_model_get_vocab(model.get());
        loss.top_count = std::min(32, llama_vocab_n_tokens(vocab));
        create_context(256, false);
    }
    void decode(const std::vector<llama_token> & tokens, bool all_logits) {
        llama_memory_clear(llama_get_memory(context.get()), true);
        auto batch = llama_batch_init((int)tokens.size(), 0, 1);
        batch.n_tokens = (int)tokens.size();
        for (int i = 0; i < batch.n_tokens; ++i) {
            batch.token[i] = tokens[i]; batch.pos[i] = i; batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0;
            batch.logits[i] = all_logits || i + 1 == batch.n_tokens;
        }
        int result = llama_decode(context.get(), batch);
        llama_batch_free(batch);
        if (result != 0) throw std::runtime_error("teacher model evaluation failed: " + std::to_string(result));
    }
    Json teacher(const Json & row) {
        check_pause();
        auto encoded = encode(row);
        const auto path = cache / (digest(encoded.dump()) + ".json");
        if (fs::exists(path)) return read_json(path);
        auto tokens = encoded.at("tokens").get<std::vector<llama_token>>();
        decode(tokens, true);
        Json targets = Json::array();
        const int count = llama_vocab_n_tokens(vocab);
        std::vector<int> ids(count);
        std::vector<float> probabilities(count);
        for (size_t position = encoded.at("start").get<size_t>() - 1; position + 1 < tokens.size(); ++position) {
            const float * logits = llama_get_logits_ith(context.get(), (int)position);
            float maximum = *std::max_element(logits, logits + count);
            double sum = 0;
            for (int i = 0; i < count; ++i) { probabilities[i] = std::exp(logits[i] - maximum); sum += probabilities[i]; }
            for (auto & probability : probabilities) probability /= (float)sum;
            std::iota(ids.begin(), ids.end(), 0);
            std::partial_sort(ids.begin(), ids.begin() + loss.top_count, ids.end(), [&](int a, int b) {
                return probabilities[a] == probabilities[b] ? a < b : probabilities[a] > probabilities[b];
            });
            std::vector<int> selected(ids.begin(), ids.begin() + loss.top_count);
            std::vector<float> top;
            float mass = 0, entropy = 0;
            for (int id : selected) { top.push_back(probabilities[id]); mass += probabilities[id]; entropy += probabilities[id] * std::log(std::max(probabilities[id], 1e-8f)); }
            float rest = std::max(1.0f - mass, 1e-8f);
            entropy += rest * std::log(rest);
            targets.push_back({{"ids", selected}, {"probs", top}, {"rest", rest}, {"entropy", entropy}});
        }
        encoded["teacher"] = targets;
        write_json(path, encoded);
        return encoded;
    }
    std::string generate(const Json & row, int first_rank) {
        auto tokens = tokenize(prompt(row));
        std::string text;
        for (size_t step = 0; step < 2 * row.at("keys").get<std::string>().size() + 8; ++step) {
            check_pause(); decode(tokens, false);
            const float * logits = llama_get_logits_ith(context.get(), -1);
            std::vector<int> ids(llama_vocab_n_tokens(vocab)); std::iota(ids.begin(), ids.end(), 0);
            const int rank = step == 0 ? first_rank : 0;
            std::partial_sort(ids.begin(), ids.begin() + rank + 1, ids.end(), [&](int a, int b) {
                return logits[a] == logits[b] ? a < b : logits[a] > logits[b];
            });
            const auto token = ids[rank];
            if (llama_vocab_is_eog(vocab, token)) break;
            std::vector<char> piece(256);
            int n = llama_token_to_piece(vocab, token, piece.data(), (int)piece.size(), 0, false);
            if (n < 0) { piece.resize(-n); n = llama_token_to_piece(vocab, token, piece.data(), (int)piece.size(), 0, false); }
            if (n < 0) throw std::runtime_error("cannot decode teacher token");
            text.append(piece.data(), n); tokens.push_back(token);
            if (text.find('\n') != std::string::npos) { text.resize(text.find('\n')); break; }
        }
        return text;
    }
    Json anchors(const Json & replay) {
        progress("collision_replay");
        std::vector<std::string> contexts, codes;
        std::set<std::string> terms;
        for (const auto & row : replay) {
            auto value = beam::utf8_tail(row.value("context", ""), 64);
            if (beam::utf8_chars(value).size() >= 8 && std::find(contexts.begin(), contexts.end(), value) == contexts.end()) contexts.push_back(value);
            if (contexts.size() == 12) break;
        }
        for (const auto & event : job.at("recent")) for (const auto & row : event.at("samples")) {
            auto text = row.at("target").get<std::string>();
            auto characters = beam::utf8_chars(text);
            if (characters.size() < 2 || characters.size() > 8 ||
                std::any_of(characters.begin(), characters.end(), [](const auto & ch) { return ch.size() == 1; })) continue;
            auto code = pronunciation.codes(text, row.at("keys")).second;
            if (code.empty()) continue;
            terms.insert(text);
            if (codes.size() < 12 && std::find(codes.begin(), codes.end(), code) == codes.end()) codes.push_back(code);
        }
        const auto path = root / "anchors.json";
        Json saved = fs::exists(path) ? read_json(path) : Json{{"cursor", 0}, {"rows", Json::array()}};
        size_t index = 0;
        for (const auto & code : codes) for (const auto & text : contexts) {
            if (index++ < saved.at("cursor").get<size_t>()) continue;
            Json row{{"keys", code}, {"context", text}};
            for (int rank = 0; rank < std::min(5, llama_vocab_n_tokens(vocab)); ++rank) {
                auto target = generate(row, rank);
                if (!terms.count(target) && !pronunciation.align(target, code).empty()) {
                    row["target"] = target; saved["rows"].push_back(row); break;
                }
            }
            saved["cursor"] = index; write_json(path, saved);
        }
        return saved.at("rows");
    }
    void prepare() {
        const auto snapshot = root / "samples.json";
        if (fs::exists(snapshot)) {
            auto value = read_json(snapshot); samples = value.at("samples"); schedule = value.at("schedule"); return;
        }
        auto replay = read_json(fs::u8path(component.at("replay").get<std::string>()));
        if (!replay.is_array() || replay.size() < 16) throw std::runtime_error("learning component needs at least 16 replay rows");
        if (replay.size() > 256) replay.erase(replay.begin() + 256, replay.end());
        auto collision = anchors(replay);
        progress("teacher_cache");
        samples = Json::array();
        using Pool = std::vector<std::vector<std::vector<size_t>>>;
        auto pool = [&](const Json & events) {
            Pool result;
            for (const auto & event : events) {
                std::vector<std::vector<size_t>> utterances;
                for (const auto & row : event.at("samples")) {
                    std::vector<size_t> ids;
                    for (const auto & variant : variants(row)) { ids.push_back(samples.size()); samples.push_back(encode(variant)); }
                    utterances.push_back(ids);
                }
                if (!utterances.empty()) result.push_back(utterances);
            }
            return result;
        };
        const auto recent = pool(job.at("recent")), history = pool(job.at("history"));
        if (recent.empty()) throw std::runtime_error("training snapshot contains no recent events");
        std::vector<size_t> generic, collisions;
        for (const auto & row : replay) { generic.push_back(samples.size()); samples.push_back(teacher(row)); }
        for (const auto & row : collision) { collisions.push_back(samples.size()); samples.push_back(teacher(row)); }
        std::mt19937 random((uint32_t)std::stoul(digest(job.at("id").get<std::string>()).substr(0, 8), nullptr, 16));
        auto pick = [&](const auto & values) -> const auto & { return values[random() % values.size()]; };
        schedule = Json::array();
        for (int step = 0; step < steps; ++step) {
            std::vector<size_t> selected;
            for (int i = 0; i < 12; ++i) {
                const auto & events = i < 8 || history.empty() ? recent : history;
                selected.push_back(pick(pick(pick(events))));
            }
            for (int i = 0; i < 4; ++i) selected.push_back(pick(i >= 2 && !collisions.empty() ? collisions : generic));
            int target_tokens = 0;
            for (int i = 0; i < 12; ++i) target_tokens += samples[selected[i]].at("target_count").get<int>();
            for (int i = 0; i < 16; ++i) {
                int count = samples[selected[i]].at("target_count");
                schedule.push_back({{"sample", selected[i]}, {"ce", i < 12 ? (float)count / target_tokens : 0.25f / 4},
                                    {"kl", i < 12 ? 0.0f : 1.0f / (4 * count)}});
            }
        }
        std::ostringstream random_state; random_state << random;
        write_json(snapshot, {{"samples", samples}, {"schedule", schedule}, {"rng", random_state.str()}, {"recipe", recipe}});
    }
    void import_adapter(const fs::path & path) {
        auto source = std::unique_ptr<llama_adapter_lora, decltype(&llama_adapter_lora_free)>(
            llama_adapter_lora_init(model.get(), path.u8string().c_str()), llama_adapter_lora_free);
        if (!source || source->ab_map.size() != adapter->ab_map.size() || source->alpha != 16)
            throw std::runtime_error("incompatible personal adapter");
        for (const auto & item : adapter->ab_map) {
            auto found = source->ab_map.find(item.first);
            if (found == source->ab_map.end()) throw std::runtime_error("personal adapter has different target projections");
            for (const auto & pair : {std::make_pair(item.second.a, found->second.a), std::make_pair(item.second.b, found->second.b)}) {
                if (!ggml_are_same_shape(pair.first, pair.second)) throw std::runtime_error("personal adapter rank mismatch");
                std::vector<float> values(ggml_nelements(pair.first));
                if (pair.second->type == GGML_TYPE_F32) ggml_backend_tensor_get(pair.second, values.data(), 0, ggml_nbytes(pair.second));
                else if (pair.second->type == GGML_TYPE_F16) {
                    std::vector<ggml_fp16_t> halves(values.size());
                    ggml_backend_tensor_get(pair.second, halves.data(), 0, ggml_nbytes(pair.second));
                    ggml_fp16_to_fp32_row(halves.data(), values.data(), (int64_t)values.size());
                } else throw std::runtime_error("unsupported personal adapter tensor type");
                ggml_backend_tensor_set(pair.first, values.data(), 0, ggml_nbytes(pair.first));
            }
        }
    }
    static ggml_opt_optimizer_params optimizer_parameters(void *) {
        auto params = ggml_opt_get_default_optimizer_params(nullptr);
        params.adamw.alpha = 2e-4f; params.adamw.wd = 0.01f;
        return params;
    }
    static void before_batch(ggml_opt_context_t, int64_t index, void * userdata) {
        auto & self = *static_cast<Trainer *>(userdata);
        const auto & selection = self.schedule.at(index);
        const auto & row = self.samples.at(selection.at("sample").get<size_t>());
        const int count = row.at("target_count");
        const int start = row.at("start");
        std::vector<int32_t> positions(self.loss.positions_count, (int)row.at("tokens").size() - 1);
        std::vector<int32_t> ids(self.loss.top_count * self.loss.positions_count, 0);
        std::vector<float> probabilities(ids.size(), 0), rest(positions.size(), 0), entropy(positions.size(), 0);
        for (int i = 0; i < count; ++i) {
            positions[i] = start - 1 + i;
            if (row.contains("teacher")) {
                const auto & teacher = row.at("teacher").at(i);
                for (int j = 0; j < self.loss.top_count; ++j) {
                    ids[i * self.loss.top_count + j] = teacher.at("ids").at(j);
                    probabilities[i * self.loss.top_count + j] = teacher.at("probs").at(j);
                }
                rest[i] = teacher.at("rest"); entropy[i] = teacher.at("entropy");
            }
        }
        auto upload = [](ggml_tensor * target, const auto & values) {
            ggml_backend_tensor_set(target, values.data(), 0, ggml_nbytes(target));
        };
        upload(self.loss.positions, positions); upload(self.loss.teacher_ids, ids);
        upload(self.loss.teacher_probs, probabilities); upload(self.loss.teacher_rest, rest); upload(self.loss.teacher_entropy, entropy);
        const std::array<float, 2> weights{selection.at("ce"), selection.at("kl")}; upload(self.loss.coefficients, weights);
    }
    static Trainer * active;
    static void after_batch(bool train, ggml_opt_context_t, ggml_opt_dataset_t, ggml_opt_result_t,
                            int64_t completed, int64_t, int64_t) {
        if (!train) return;
        active->completed = (int)completed;
        active->progress("training");
        if (fs::exists(active->root / "pause.json")) llama_opt_request_stop(active->context.get());
    }
    fs::path checkpoint() {
        const auto pointer = root / "resume.json";
        auto previous = fs::exists(pointer) ? read_json(pointer).value("slot", "") : "";
        std::string slot = previous == "resume-a" ? "resume-b" : "resume-a";
        auto destination = root / slot;
        fs::create_directories(destination);
        if (!llama_lora_save_adapter(adapter, (destination / "adapter.gguf").u8string().c_str(), model.get()) ||
            !llama_opt_save_state(context.get(), (destination / "optimizer.gguf").u8string().c_str()))
            throw std::runtime_error("cannot save native training state");
        write_json(destination / "state.json", {{"recipe", recipe}, {"base_sha256", base_hash}, {"completed", completed}});
        write_json(pointer, {{"slot", slot}});
        return destination;
    }
    int publish(const fs::path & saved_state) {
        progress("exporting");
        auto destination = learning / "generations" / job.at("id").get<std::string>();
        fs::create_directories(destination / "checkpoint");
        for (auto name : {"adapter.gguf", "optimizer.gguf", "state.json"})
            fs::copy_file(saved_state / name, destination / "checkpoint" / name, fs::copy_options::overwrite_existing);
        fs::copy_file(saved_state / "adapter.gguf", destination / "adapter.gguf", fs::copy_options::overwrite_existing);
        Json manifest{{"id", job.at("id")}, {"path", (destination / "adapter.gguf").u8string()},
            {"checkpoint", (destination / "checkpoint").u8string()}, {"base_sha256", base_hash}, {"tokenizer", "gguf-embedded"},
            {"prompt_version", "keys_llm_v1"}, {"rank", 8}, {"alpha", 16}, {"recipe", recipe}, {"clock", job.at("clock")},
            {"training_seconds", std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count()}};
        write_json(destination / "manifest.json", manifest); write_json(root / "result.json", manifest);
        return 0;
    }
    int train() {
        load_model(); prepare(); check_pause();
        width = 32; loss.positions_count = 1;
        for (const auto & selection : schedule) {
            const auto & row = samples.at(selection.at("sample").get<size_t>());
            width = std::max(width, (int)row.at("tokens").size());
            loss.positions_count = std::max(loss.positions_count, row.at("target_count").get<int>());
        }
        width = ((width + 31) / 32) * 32;
        create_context(width, true);
        llama_lora_training_params parameters{};
        parameters.target_modules = LLAMA_LORA_TARGET_ATTN_Q | LLAMA_LORA_TARGET_ATTN_K | LLAMA_LORA_TARGET_ATTN_V |
            LLAMA_LORA_TARGET_ATTN_O | LLAMA_LORA_TARGET_FFN_GATE | LLAMA_LORA_TARGET_FFN_UP | LLAMA_LORA_TARGET_FFN_DOWN;
        parameters.rank = 8; parameters.alpha = 16; parameters.init_std = 0.02f;
        parameters.seed = (uint32_t)std::stoul(digest(job.at("id").get<std::string>()).substr(0, 8), nullptr, 16);
        adapter = llama_lora_training_init(context.get(), model.get(), &parameters);
        if (!adapter) throw std::runtime_error("cannot initialize personal adapter");
        fs::path saved;
        if (fs::exists(root / "resume.json")) {
            saved = root / read_json(root / "resume.json").at("slot").get<std::string>();
            auto state = read_json(saved / "state.json");
            if (state.at("base_sha256") != base_hash || state.at("recipe") != recipe) throw std::runtime_error("training state belongs to another model or recipe");
            completed = state.at("completed");
            if (completed < 0 || completed > steps * batch_size) throw std::runtime_error("invalid training progress");
            if (completed == steps * batch_size) return publish(saved);
            import_adapter(saved / "adapter.gguf");
        } else if (!job.value("previous", Json::object()).empty()) {
            const auto & previous = job.at("previous");
            import_adapter(fs::u8path(previous.at("path").get<std::string>()));
            auto candidate = fs::u8path(previous.value("checkpoint", ""));
            if (previous.value("recipe", "") == recipe && fs::exists(candidate / "optimizer.gguf")) saved = candidate;
        }
        const std::string optimizer_path = saved.empty() ? "" : (saved / "optimizer.gguf").u8string();
        auto options = llama_opt_default_params();
        options.param_filter = llama_opt_param_filter_lora;
        options.get_opt_pars = optimizer_parameters;
        options.assistant_loss_only = true; options.accumulation_steps = 16; options.max_grad_norm = 1.0f;
        options.build_loss = beam::TrainerLoss::build; options.loss_userdata = &loss;
        options.prepare_batch = before_batch; options.batch_userdata = this;
        options.load_optimizer_state = !saved.empty(); options.checkpoint_path = optimizer_path.c_str();
        llama_opt_init(context.get(), model.get(), options);
        auto dataset = ggml_opt_dataset_init_with_masks(GGML_TYPE_I32, GGML_TYPE_I32, GGML_TYPE_I32,
            width, width, width, (int64_t)schedule.size(), 1);
        auto * data = (int32_t *)ggml_opt_dataset_data(dataset)->data;
        auto * labels = (int32_t *)ggml_opt_dataset_labels(dataset)->data;
        auto * masks = (int32_t *)ggml_opt_dataset_masks(dataset)->data;
        for (size_t i = 0; i < schedule.size(); ++i) {
            const auto & row = samples.at(schedule[i].at("sample").get<size_t>());
            const auto tokens = row.at("tokens").get<std::vector<int32_t>>();
            for (int j = 0; j < width; ++j) {
                data[i * width + j] = j < (int)tokens.size() ? tokens[j] : llama_vocab_eos(vocab);
                labels[i * width + j] = j + 1 < (int)tokens.size() ? tokens[j + 1] : llama_vocab_eos(vocab);
                masks[i * width + j] = j + 1 >= row.at("start").get<int>() && j + 1 < (int)tokens.size() ? 1 : 0;
            }
        }
        active = this; progress("training");
        auto result = ggml_opt_result_init();
        llama_opt_epoch_resume(context.get(), dataset, result, nullptr, (int64_t)schedule.size(), after_batch, nullptr, completed - 1);
        ggml_opt_result_free(result); ggml_opt_dataset_free(dataset); active = nullptr;
        auto saved_state = checkpoint();
        if (completed < steps * batch_size) return 75;
        return publish(saved_state);
    }
};
Trainer * Trainer::active = nullptr;

static int run(int argc, char ** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << "Beam native trainer " << recipe << '\n'; return 0;
        }
        if (argc != 3 || std::string(argv[1]) != "--job") throw std::runtime_error("usage: beam-trainer --job JOB.json");
#ifndef _WIN32
        nice(10);
#endif
        ggml_backend_load_all_from_path(fs::absolute(fs::u8path(argv[0])).parent_path().u8string().c_str());
        llama_backend_init();
        int code;
        { Trainer trainer(fs::u8path(argv[2])); code = trainer.train(); }
        llama_backend_free();
        return code;
    } catch (const Paused &) { return 75; }
      catch (const std::exception & error) {
        std::cerr << "Beam training: " << error.what() << '\n';
        if (argc == 3 && std::string(argv[1]) == "--job") {
            try { write_json(fs::u8path(argv[2]).parent_path() / "error.json", {{"error", error.what()}}); }
            catch (const std::exception & state_error) { std::cerr << "Training error state: " << state_error.what() << '\n'; }
        }
        return 1;
      }
}
#ifdef _WIN32
int wmain(int argc, wchar_t ** arguments) {
    std::vector<std::string> utf8; std::vector<char *> pointers;
    for (int i = 0; i < argc; ++i) {
        int length = WideCharToMultiByte(CP_UTF8, 0, arguments[i], -1, nullptr, 0, nullptr, nullptr);
        std::string value(length, '\0');
        WideCharToMultiByte(CP_UTF8, 0, arguments[i], -1, value.data(), length, nullptr, nullptr);
        value.pop_back(); utf8.push_back(std::move(value));
    }
    for (auto & value : utf8) pointers.push_back(value.data());
    return run(argc, pointers.data());
}
#else
int main(int argc, char ** argv) { return run(argc, argv); }
#endif
