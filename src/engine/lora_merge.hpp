// Folds a LoRA adapter into the Q8_0 weights of a loaded llama.cpp model and takes it out again.
//
// Unmerged, every adapted matrix costs an extra pair of small matrix products on every token.
// Merged, the model is an ordinary one. The adapter is added to the quantized blocks as whole
// quantization steps (the block scale is kept), so nothing is requantized and the weights stay
// Q8_0. Both ways re-read the base blocks from the model file, which makes leaving the merge
// exact (the original bytes come back) and keeps only the adapter itself in memory.
#pragma once

#include <memory>
#include <string>

struct llama_model;

namespace beam {

class Q8LoraMerge {
 public:
    // The model must hold its weights in writable memory (loaded without mmap) and in Q8_0 for
    // every matrix the adapter touches; otherwise nullptr and an error code.
    static std::unique_ptr<Q8LoraMerge> open(llama_model * model, const std::string & model_path,
                                             const std::string & adapter_path, int threads, std::string & error);
    ~Q8LoraMerge();
    Q8LoraMerge(const Q8LoraMerge &) = delete;
    Q8LoraMerge & operator=(const Q8LoraMerge &) = delete;

    bool apply();    // model weights := base + adapter
    bool restore();  // model weights := base
    bool merged() const;
    double last_ms() const;  // duration of the last apply()/restore()

 private:
    Q8LoraMerge();
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace beam
