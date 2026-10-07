// SPDX-License-Identifier: Apache-2.0
#include "loss.hpp"
#include "ggml-alloc.h"
#include "ggml-opt.h"
#include <array>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

static ggml_opt_optimizer_params parameters(void *) {
    auto p = ggml_opt_get_default_optimizer_params(nullptr);
    p.sgd.alpha = .1f; p.sgd.wd = 0;
    return p;
}

static void near(double a, double b, double tolerance) {
    if (std::abs(a - b) > tolerance) throw std::runtime_error("expected " + std::to_string(b) + ", got " + std::to_string(a));
}

static double reference(const std::array<float, 10> & logits) {
    double value = 0;
    for (int i = 0; i < 2; ++i) {
        std::array<double, 5> p{};
        double sum = 0;
        for (int j = 0; j < 5; ++j) { p[j] = std::exp(logits[i * 5 + j]); sum += p[j]; }
        for (auto & x : p) x /= sum;
        value -= .7 / 2 * std::log(p[i == 0 ? 1 : 4]);
        const double a = i == 0 ? .55 : .2, b = i == 0 ? .25 : .5, rest = 1 - a - b;
        value += .4 * (a * std::log(a / p[0]) + b * std::log(b / p[2]) + rest * std::log(rest / (1 - p[0] - p[2])));
    }
    return value;
}

int main(int argc, char ** argv) {
#ifdef _WIN32
    wchar_t path[32768];
    if (!GetModuleFileNameW(nullptr, path, 32768)) throw std::runtime_error("cannot find test executable");
    auto executable = std::filesystem::path(path);
#else
    auto executable = std::filesystem::absolute(argv[0]);
#endif
    ggml_backend_load_all_from_path(executable.parent_path().u8string().c_str());
    auto cpu = ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_CPU, nullptr);
    if (!cpu) return 1;
    auto gpu = argc > 1 ? ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_IGPU, nullptr) : nullptr;
    if (argc > 1 && !gpu) gpu = ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_GPU, nullptr);
    if (argc > 1 && !gpu) throw std::runtime_error("no GPU for Vulkan loss test");
    auto backend = gpu ? gpu : cpu;
    ggml_backend_t backends[2]{backend, cpu};
    auto scheduler = ggml_backend_sched_new(backends, nullptr, gpu ? 2 : 1, 4096, false, true);
    auto storage = ggml_init({1024 * 1024, nullptr, true});
    auto weight = ggml_new_tensor_2d(storage, GGML_TYPE_F32, 5, 2);
    ggml_set_param(weight); ggml_set_name(weight, "test_logits");
    auto buffer = ggml_backend_alloc_ctx_tensors(storage, backend);
    const std::array<float, 10> initial{.1f, -.2f, .3f, -.4f, .5f, -.1f, .2f, -.3f, .4f, -.5f};
    ggml_backend_tensor_set(weight, initial.data(), 0, sizeof(initial));
    beam::TrainerLoss loss; loss.positions_count = 2; loss.top_count = 2;
    auto params = ggml_opt_default_params(scheduler, GGML_OPT_LOSS_TYPE_CROSS_ENTROPY_MASKED);
    params.opt_period = 2; params.max_grad_norm = .1f;
    params.optimizer = GGML_OPT_OPTIMIZER_TYPE_SGD; params.get_opt_pars = parameters;
    params.build_loss = beam::TrainerLoss::build; params.loss_userdata = &loss;
    auto optimizer = ggml_opt_init(params);
    for (int i = 0; i < 2; ++i) {
        auto ctx = ggml_init({4 * 1024 * 1024, nullptr, true});
        auto output = ggml_scale(ctx, weight, 1);
        auto graph = ggml_new_graph_custom(ctx, 4096, false);
        ggml_build_forward_expand(graph, output);
        ggml_opt_prepare_alloc(optimizer, ctx, graph, weight, output); ggml_opt_alloc(optimizer, true);
        const float labels[10]{0,1,0,0,0, 0,0,0,0,1}, masks[10]{1,0,0,0,0, 1,0,0,0,0};
        const int32_t positions[2]{0,1}, ids[4]{0,2,0,2};
        const float probs[4]{.55f,.25f,.2f,.5f}, rest[2]{.2f,.3f}, coefficients[2]{.35f,.2f};
        float entropy[2]{};
        for (int j = 0; j < 2; ++j) entropy[j] = probs[2*j]*std::log(probs[2*j]) + probs[2*j+1]*std::log(probs[2*j+1]) + rest[j]*std::log(rest[j]);
        auto set = [](ggml_tensor * tensor, const auto & values) { ggml_backend_tensor_set(tensor, values, 0, sizeof(values)); };
        set(ggml_opt_labels(optimizer), labels); set(ggml_opt_masks(optimizer), masks);
        set(loss.positions, positions); set(loss.teacher_ids, ids); set(loss.teacher_probs, probs);
        set(loss.teacher_rest, rest); set(loss.teacher_entropy, entropy); set(loss.coefficients, coefficients);
        ggml_opt_eval(optimizer, nullptr);
        float observed;
        ggml_backend_tensor_get(ggml_opt_loss(optimizer), &observed, 0, sizeof(observed));
        near(observed, reference(initial) / 2, 1e-6);
        ggml_free(ctx);
    }
    std::array<float, 10> actual{};
    ggml_backend_tensor_get(weight, actual.data(), 0, sizeof(actual));
    double norm = 0; std::array<double, 10> gradient{};
    for (int i = 0; i < 10; ++i) {
        auto a = initial, b = initial; a[i] += .001f; b[i] -= .001f;
        gradient[i] = (reference(a) - reference(b)) / (a[i] - b[i]); norm += gradient[i]*gradient[i];
    }
    norm = std::sqrt(norm);
    for (int i = 0; i < 10; ++i) near(actual[i], initial[i] - .1 * gradient[i] * .1 / norm, 2e-6);
    ggml_opt_free(optimizer);
    // Rebuilding dynamic graphs must reset accumulated gradients after each update.
    params = ggml_opt_default_params(scheduler, GGML_OPT_LOSS_TYPE_MEAN_SQUARED_ERROR);
    params.opt_period = 2; params.optimizer = GGML_OPT_OPTIMIZER_TYPE_SGD; params.get_opt_pars = parameters;
    optimizer = ggml_opt_init(params);
    std::array<float, 10> ones{}; ones.fill(.5f);
    ggml_backend_tensor_set(weight, ones.data(), 0, sizeof(ones));
    for (int i = 0; i < 4; ++i) {
        auto ctx = ggml_init({4 * 1024 * 1024, nullptr, true});
        auto output = ggml_scale(ctx, weight, 1);
        auto graph = ggml_new_graph_custom(ctx, 4096, false); ggml_build_forward_expand(graph, output);
        ggml_opt_prepare_alloc(optimizer, ctx, graph, weight, output); ggml_opt_alloc(optimizer, true);
        ggml_set_zero(ggml_opt_labels(optimizer)); ggml_opt_eval(optimizer, nullptr); ggml_free(ctx);
    }
    ggml_backend_tensor_get(weight, actual.data(), 0, sizeof(actual));
    for (float value : actual) near(value, .5 * .98 * .98, 1e-6);
    ggml_opt_free(optimizer); ggml_backend_sched_free(scheduler); ggml_backend_buffer_free(buffer);
    ggml_free(storage); if (gpu) ggml_backend_free(gpu); ggml_backend_free(cpu);
    std::cout << "CE/KL gradients, global clipping and dynamic accumulation passed\n";
    return 0;
}
