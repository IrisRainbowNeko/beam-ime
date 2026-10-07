// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "store.hpp"
#include <chrono>
#include <memory>

namespace beam {
class Engine;
class LearningRuntime {
 public:
    LearningRuntime(LearningStore & store, Engine & engine, const std::string & model);
    ~LearningRuntime();
    Json command(const Json & request);
    void composition(const Json & request, uint64_t owner);
    void disconnect(uint64_t owner);
    void activity();
    void tick();
    Json status() const;
 private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
