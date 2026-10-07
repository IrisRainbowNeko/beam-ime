// librime plugin "beam": candidates from the local beamd keys-LLM service.
//
// Components:
//   beam_translator  asks beamd for whole-input candidates of an "abc" segment and returns them
//                    with top quality; the schema's normal translators keep running, so their
//                    candidates follow Beam's and take over whenever beamd is down or slow.
//   beam_processor   never consumes keys; it only notices Return/BackSpace outside a composition
//                    so the context sent to the model keeps the training format (turns joined
//                    by "｜", newest last).
//
// Schema options (all optional):
//   beam/socket: path          Unix only; default $BEAM_SOCKET, else $XDG_RUNTIME_DIR/beam-ime/beamd.sock
//   beam/timeout_ms: 400       whole request budget before falling back
//   beam/beam_ms: 150          beam search budget inside beamd (0 = Top-1 only)
//   beam/candidates: 5
//   beam/context: true         send recently committed text as context
//   beam/max_letters: 40       longer inputs are left to the normal translators
// Switch "beam_off" (when defined in the schema) turns Beam off at runtime.

#include "beam_client.hpp"
#include "keys.hpp"

#include <rime/candidate.h>
#include <rime/common.h>
#include <rime/commit_history.h>
#include <rime/composition.h>
#include <rime/config.h>
#include <rime/context.h>
#include <rime/engine.h>
#include <rime/filter.h>
#include <rime/key_event.h>
#include <rime/processor.h>
#include <rime/registry.h>
#include <rime/schema.h>
#include <rime/segmentation.h>
#include <rime/translation.h>
#include <rime/translator.h>
#include <rime_api.h>

#include <chrono>
#include <cstdlib>
#include <deque>
#include <map>
#include <mutex>
#include <string>
#include <atomic>
#include <set>

namespace beam {

namespace {

constexpr size_t kContextChars = 64;
constexpr auto kIdleReset = std::chrono::minutes(10);

std::string history_text(const ::rime::CommitHistory & history) {
    std::string text;
    for (const auto & record : history) text += record.text;
    return text;
}

std::string drop_last_char(const std::string & s) {
    auto ch = utf8_chars(s);
    if (!ch.empty()) ch.pop_back();
    std::string out;
    for (auto & c : ch) out += c;
    return out;
}

// What the user typed in this input context. Rime's own commit history is cleared on Return and
// BackSpace, so finished turns and the surviving part of the current turn are kept here.
struct TypedText {
    std::deque<std::string> turns;
    std::string carry;
    std::chrono::steady_clock::time_point last_activity = std::chrono::steady_clock::now();
    std::unique_ptr<AsyncClient> client;
    std::string session, composition, previous_input, before_context;
    uint64_t sequence = 0, generation = 0, reported_errors = 0;
    bool learning = false, paused = false, use_context = true, syncing = false;
    std::map<size_t, std::string> first;
    ::rime::connection update_connection, commit_connection, option_connection;

    ~TypedText() {
        update_connection.disconnect(); commit_connection.disconnect(); option_connection.disconnect();
    }

    void attach(::rime::Engine * engine, const std::string & socket, bool contexts) {
        if (client) return;
        client = std::make_unique<AsyncClient>(socket); use_context = contexts;
        static std::atomic<uint64_t> next{0};
        session = std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count()) + "-" + std::to_string(++next);
        update_connection = engine->context()->update_notifier().connect([this](::rime::Context * ctx) {
            if (ctx->IsComposing()) {
                begin(ctx);
                if (ctx->input() != previous_input) { previous_input = ctx->input(); notify("activity"); }
            } else if (!composition.empty()) { notify("cancel"); composition.clear(); first.clear(); ++generation; }
        });
        commit_connection = engine->context()->commit_notifier().connect([this](::rime::Context * ctx) {
            if (composition.empty()) return;
            if (learning && !paused) {
                nlohmann::json parts = nlohmann::json::array();
                std::string first_text;
                for (const auto & segment : ctx->composition()) {
                    if (auto candidate = segment.GetSelectedCandidate()) {
                        if (segment.start <= segment.end && segment.end <= ctx->input().size()) {
                            parts.push_back({{"keys", ctx->input().substr(segment.start, segment.end - segment.start)},
                                             {"text", candidate->text()}, {"first", first.count(segment.start) ? first[segment.start] : ""}});
                            first_text += first.count(segment.start) ? first[segment.start] : candidate->text();
                        }
                    }
                }
                client->post({{"op", "feedback"}, {"session", session}, {"composition", composition},
                              {"event_id", session + ":" + composition}, {"keys", ctx->input()},
                              {"text", ctx->GetCommitText()}, {"context", before_context}, {"first", first_text}, {"parts", parts}});
            }
            notify("end"); composition.clear(); first.clear(); ++generation;
        });
        option_connection = engine->context()->option_update_notifier().connect([this](::rime::Context * ctx, const std::string & name) {
            if (syncing) return;
            if (name == "beam_learning") {
                learning = ctx->get_option(name); paused = false;
                client->post({{"op", "learning"}, {"action", learning ? "enable" : "disable"}});
                ++generation;
            } else if (name == "beam_learning_paused" && learning) {
                paused = ctx->get_option(name);
                client->post({{"op", "learning"}, {"action", paused ? "pause" : "resume"}});
            }
        });
    }

    void begin(::rime::Context * ctx) {
        if (!composition.empty()) return;
        composition = std::to_string(++sequence); first.clear(); previous_input = ctx->input();
        before_context = use_context ? context(history_text(ctx->commit_history())) : "";
        notify("begin");
    }

    void notify(const char * state) {
        auto errors = client->errors();
        client->post({{"op", "composition"}, {"session", session}, {"composition", composition},
                      {"state", state}, {"client_errors", errors - reported_errors}});
        reported_errors = errors;
    }

    bool touch() {
        auto now = std::chrono::steady_clock::now();
        bool expired = now - last_activity > kIdleReset;
        if (expired) { turns.clear(); carry.clear(); }
        last_activity = now;
        return expired;
    }

    std::string context(const std::string & current) const {
        std::string all;
        for (size_t i = 0; i < turns.size(); ++i) all += (i ? "｜" : "") + turns[i];
        std::string inside = carry + current;
        if (!turns.empty() && !inside.empty()) all += "｜";
        all += inside;
        return utf8_tail(all, kContextChars);
    }
};

// Processor and translator of one engine share a TypedText. Keyed by Engine*, kept alive by the
// components holding it.
std::shared_ptr<TypedText> typed_text_for(const ::rime::Engine * engine) {
    static std::mutex mutex;
    static std::map<const ::rime::Engine *, std::weak_ptr<TypedText>> registry;
    std::lock_guard<std::mutex> lock(mutex);
    for (auto it = registry.begin(); it != registry.end();) it = it->second.expired() ? registry.erase(it) : ++it;
    auto & slot = registry[engine];
    auto shared = slot.lock();
    if (!shared) slot = shared = std::make_shared<TypedText>();
    return shared;
}

}  // namespace

class BeamProcessor final : public ::rime::Processor {
 public:
    explicit BeamProcessor(const ::rime::Ticket & ticket)
        : ::rime::Processor(ticket), typed_(typed_text_for(ticket.engine)) {}

    ::rime::ProcessResult ProcessKeyEvent(const ::rime::KeyEvent & key) override {
        if (key.release() || key.modifier() != 0 || !engine_) return ::rime::kNoop;
        ::rime::Context * ctx = engine_->context();
        if (!ctx || ctx->IsComposing()) return ::rime::kNoop;
        if (typed_->client) {
            typed_->syncing = true;
            ctx->set_option("beam_learning", typed_->learning);
            ctx->set_option("beam_learning_paused", typed_->paused);
            typed_->syncing = false;
        }
        if (key.keycode() == XK_Return || key.keycode() == XK_KP_Enter) {
            if (typed_->touch()) ctx->commit_history().clear();
            std::string turn = typed_->carry + history_text(ctx->commit_history());
            if (!turn.empty()) typed_->turns.push_back(turn);
            while (typed_->turns.size() > 8) typed_->turns.pop_front();
            typed_->carry.clear();
        } else if (key.keycode() == XK_BackSpace) {
            if (typed_->touch()) ctx->commit_history().clear();
            typed_->carry = drop_last_char(typed_->carry + history_text(ctx->commit_history()));
        }
        return ::rime::kNoop;
    }

 private:
    std::shared_ptr<TypedText> typed_;
};

class BeamTranslator final : public ::rime::Translator {
 public:
    explicit BeamTranslator(const ::rime::Ticket & ticket)
        : ::rime::Translator(ticket), typed_(typed_text_for(ticket.engine)) {
        auto socket = LoadConfig(ticket);
        typed_->attach(ticket.engine, socket, use_context_);
    }

    ::rime::an<::rime::Translation> Query(const ::rime::string & input, const ::rime::Segment & segment) override {
        if (input.empty() || !segment.HasTag("abc") || !engine_) return nullptr;
        ::rime::Context * ctx = engine_->context();
        if (!ctx || ctx->get_option("beam_off")) return nullptr;
        std::string keys = normalize_keys(input);
        int letters = letter_count(keys);
        if (letters == 0 || letters > max_letters_) return nullptr;

        if (typed_->touch()) ctx->commit_history().clear();
        typed_->begin(ctx);
        std::string context;
        if (use_context_) {
            // Text before this segment: committed text plus candidates already chosen in the
            // composition (e.g. a partial selection from the normal dictionary).
            std::string current = history_text(ctx->commit_history());
            for (const ::rime::Segment & seg : ctx->composition()) {
                if (seg.start >= segment.start) break;
                if (auto cand = seg.GetSelectedCandidate()) current += cand->text();
            }
            context = typed_->context(current);
        }

        std::vector<std::string> candidates;
        if (keys == last_keys_ && context == last_context_ && last_generation_ == typed_->generation) {
            candidates = last_candidates_;
        } else {
            auto reply = typed_->client->query({{"op", "query"}, {"keys", keys}, {"context", context},
                                                {"max", max_candidates_}, {"beam_ms", beam_ms_}}, timeout_ms_);
            if (!reply || !reply->value("ok", false) || !reply->contains("candidates") || !(*reply)["candidates"].is_array()) return nullptr;
            for (auto & candidate : (*reply)["candidates"]) if (candidate.is_string()) candidates.push_back(candidate);
            sources_ = reply->value("sources", std::vector<std::string>{});
            typed_->learning = reply->value("learning_enabled", false); typed_->paused = reply->value("learning_paused", false);
            last_keys_ = keys;
            last_context_ = context;
            last_candidates_ = candidates;
            last_generation_ = typed_->generation;
        }
        if (candidates.empty()) return nullptr;

        auto translation = ::rime::New<::rime::FifoTranslation>();
        double quality = 1000.0;
        for (size_t i = 0; i < candidates.size(); ++i) {
            const auto & text = candidates[i];
            std::string type = i < sources_.size() && sources_[i] == "personal" ? "beam_personal" : "beam";
            auto cand = ::rime::New<::rime::SimpleCandidate>(type, segment.start, segment.end, text, "", input);
            cand->set_quality(quality);
            quality -= 1.0;
            translation->Append(cand);
        }
        return translation;
    }

 private:
    std::string LoadConfig(const ::rime::Ticket & ticket) {
        std::string socket;  // empty: net::default_location()
        if (ticket.schema) {
            if (::rime::Config * config = ticket.schema->config()) {
                std::string value;
                bool flag;
                if (config->GetString("beam/socket", &value) && !value.empty()) socket = value;
                config->GetInt("beam/timeout_ms", &timeout_ms_);
                config->GetInt("beam/beam_ms", &beam_ms_);
                config->GetInt("beam/candidates", &max_candidates_);
                config->GetInt("beam/max_letters", &max_letters_);
                if (config->GetBool("beam/context", &flag)) use_context_ = flag;
            }
        }
        return socket;
    }

    std::shared_ptr<TypedText> typed_;
    int timeout_ms_ = 400;
    int beam_ms_ = 150;
    int max_candidates_ = 5;
    int max_letters_ = 40;
    bool use_context_ = true;
    uint64_t last_generation_ = uint64_t(-1);
    std::string last_keys_, last_context_;
    std::vector<std::string> last_candidates_, sources_;
};

class PersonalTranslation final : public ::rime::Translation {
 public:
    PersonalTranslation(::rime::an<::rime::Translation> source, std::shared_ptr<TypedText> typed, int size)
        : source_(source) {
        std::set<std::string> personal_ids, ordinary_ids;
        for (int i = 0; i < 32 && !source_->exhausted(); ++i) {
            auto candidate = source_->Peek(); source_->Next();
            if (candidate) {
                auto id = identity(candidate);
                if (personal(candidate)) personal_ids.insert(id);
                if (ordinary(candidate)) ordinary_ids.insert(id);
                if (seen_.insert(id).second) front_.push_back(candidate);
                else if (ordinary(candidate)) {
                    auto duplicate = std::find_if(front_.begin(), front_.end(), [&](const auto & value) { return identity(value) == id; });
                    if (duplicate != front_.end()) *duplicate = candidate;
                }
            }
            if ((int)front_.size() >= size && (!typed->learning || ordinary(candidate))) break;
        }
        if (typed->learning && size >= 2) {
            reserve(size - 1, [&](const auto & candidate) { return personal_ids.count(identity(candidate)); });
            reserve(size, [&](const auto & candidate) { return ordinary_ids.count(identity(candidate)); });
        }
        if (!front_.empty()) typed->first[front_[0]->start()] = front_[0]->text();
        set_exhausted(front_.empty() && source_->exhausted());
    }
    bool Next() override {
        if (position_ < front_.size()) ++position_;
        else if (!source_->exhausted()) source_->Next();
        if (position_ >= front_.size()) {
            while (!source_->exhausted()) {
                auto candidate = source_->Peek();
                if (candidate && seen_.insert(identity(candidate)).second) break;
                source_->Next();
            }
        }
        set_exhausted(position_ >= front_.size() && source_->exhausted());
        return !exhausted();
    }
    ::rime::an<::rime::Candidate> Peek() override {
        return position_ < front_.size() ? front_[position_] : source_->Peek();
    }
 private:
    static std::string identity(const ::rime::an<::rime::Candidate> & candidate) {
        return std::to_string(candidate->start()) + ":" + std::to_string(candidate->end()) + ":" + fold_case(candidate->text());
    }
    static std::string type(const ::rime::an<::rime::Candidate> & candidate) {
        if (!candidate) return "";
        auto genuine = ::rime::Candidate::GetGenuineCandidate(candidate);
        return genuine ? genuine->type() : candidate->type();
    }
    static bool personal(const ::rime::an<::rime::Candidate> & candidate) { return type(candidate) == "beam_personal"; }
    static bool ordinary(const ::rime::an<::rime::Candidate> & candidate) { auto t = type(candidate); return !t.empty() && t != "beam" && t != "beam_personal"; }
    template<class Predicate> void reserve(int size, Predicate predicate) {
        auto found = std::find_if(front_.begin(), front_.end(), predicate);
        if (found == front_.end() || found - front_.begin() < size) return;
        auto value = *found; front_.erase(found); front_.insert(front_.begin() + size - 1, value);
    }
    ::rime::an<::rime::Translation> source_;
    ::rime::CandidateList front_;
    std::set<std::string> seen_;
    size_t position_ = 0;
};

class BeamFilter final : public ::rime::Filter {
 public:
    explicit BeamFilter(const ::rime::Ticket & ticket) : ::rime::Filter(ticket), typed_(typed_text_for(ticket.engine)) {
        if (ticket.schema && ticket.schema->config()) ticket.schema->config()->GetInt("menu/page_size", &page_size_);
        page_size_ = std::clamp(page_size_, 1, 10);
    }
    ::rime::an<::rime::Translation> Apply(::rime::an<::rime::Translation> translation, ::rime::CandidateList *) override {
        return ::rime::New<PersonalTranslation>(translation, typed_, page_size_);
    }
 private:
    std::shared_ptr<TypedText> typed_;
    int page_size_ = 5;
};

}  // namespace beam

static void rime_beam_initialize() {
    auto & r = ::rime::Registry::instance();
    r.Register("beam_translator", new ::rime::Component<beam::BeamTranslator>);
    r.Register("beam_processor", new ::rime::Component<beam::BeamProcessor>);
    r.Register("beam_filter", new ::rime::Component<beam::BeamFilter>);
}

static void rime_beam_finalize() {}

RIME_REGISTER_MODULE(beam)
