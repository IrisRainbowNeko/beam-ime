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
        : ::rime::Translator(ticket), typed_(typed_text_for(ticket.engine)), client_(LoadConfig(ticket)) {}

    ::rime::an<::rime::Translation> Query(const ::rime::string & input, const ::rime::Segment & segment) override {
        if (input.empty() || !segment.HasTag("abc") || !engine_) return nullptr;
        ::rime::Context * ctx = engine_->context();
        if (!ctx || ctx->get_option("beam_off")) return nullptr;
        std::string keys = normalize_keys(input);
        int letters = letter_count(keys);
        if (letters == 0 || letters > max_letters_) return nullptr;

        if (typed_->touch()) ctx->commit_history().clear();
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
        if (keys == last_keys_ && context == last_context_) {
            candidates = last_candidates_;
        } else {
            auto reply = client_.query(keys, context, max_candidates_, beam_ms_, timeout_ms_);
            if (!reply) return nullptr;
            candidates = *reply;
            last_keys_ = keys;
            last_context_ = context;
            last_candidates_ = candidates;
        }
        if (candidates.empty()) return nullptr;

        auto translation = ::rime::New<::rime::FifoTranslation>();
        double quality = 1000.0;
        for (const auto & text : candidates) {
            auto cand = ::rime::New<::rime::SimpleCandidate>("beam", segment.start, segment.end, text, "", input);
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
    Client client_;
    std::string last_keys_, last_context_;
    std::vector<std::string> last_candidates_;
};

}  // namespace beam

static void rime_beam_initialize() {
    auto & r = ::rime::Registry::instance();
    r.Register("beam_translator", new ::rime::Component<beam::BeamTranslator>);
    r.Register("beam_processor", new ::rime::Component<beam::BeamProcessor>);
}

static void rime_beam_finalize() {}

RIME_REGISTER_MODULE(beam)
