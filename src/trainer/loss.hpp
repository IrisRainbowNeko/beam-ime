// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "ggml.h"
#include "ggml-backend.h"
#include <vector>

namespace beam {
struct TrainerLoss {
    int positions_count = 1;
    int top_count = 32;
    ggml_tensor * positions = nullptr;
    ggml_tensor * teacher_ids = nullptr;
    ggml_tensor * teacher_probs = nullptr;
    ggml_tensor * teacher_rest = nullptr;
    ggml_tensor * teacher_entropy = nullptr;
    ggml_tensor * coefficients = nullptr;

    static ggml_tensor * build(ggml_context * ctx, ggml_tensor * logits,
                               ggml_tensor * labels, ggml_tensor * masks, void * userdata) {
        auto & self = *static_cast<TrainerLoss *>(userdata);
        auto input = [ctx](ggml_type type, int64_t n0, int64_t n1, const char * name) {
            auto tensor = ggml_new_tensor_2d(ctx, type, n0, n1);
            ggml_set_input(tensor); ggml_set_name(tensor, name);
            return tensor;
        };
        const int count = self.positions_count;
        self.positions = input(GGML_TYPE_I32, count, 1, "beam_positions");
        self.teacher_ids = input(GGML_TYPE_I32, self.top_count, count, "beam_teacher_ids");
        self.teacher_probs = input(GGML_TYPE_F32, self.top_count, count, "beam_teacher_probs");
        self.teacher_rest = input(GGML_TYPE_F32, 1, count, "beam_teacher_rest");
        self.teacher_entropy = input(GGML_TYPE_F32, 1, count, "beam_teacher_entropy");
        self.coefficients = input(GGML_TYPE_F32, 2, 1, "beam_loss_coefficients");
        auto selected = ggml_get_rows(ctx, logits, self.positions);
        auto ce = ggml_cross_entropy_loss_masked(ctx, selected,
            ggml_get_rows(ctx, labels, self.positions), ggml_get_rows(ctx, masks, self.positions));
        auto probabilities = ggml_soft_max(ctx, selected);
        auto picked = ggml_reshape_2d(ctx, ggml_get_rows(ctx,
            ggml_reshape_3d(ctx, probabilities, 1, logits->ne[0], count), self.teacher_ids), self.top_count, count);
        auto floor = [ctx](ggml_tensor * value) {
            return ggml_add(ctx, value, ggml_relu(ctx, ggml_scale_bias(ctx, value, -1.0f, 1e-8f)));
        };
        auto remaining = floor(ggml_scale_bias(ctx, ggml_sum_rows(ctx, picked), -1.0f, 1.0f));
        auto top = ggml_sum_rows(ctx, ggml_mul(ctx, self.teacher_probs, ggml_log(ctx, floor(picked))));
        auto kl = ggml_sum(ctx, ggml_sub(ctx, ggml_sub(ctx, self.teacher_entropy, top),
            ggml_mul(ctx, self.teacher_rest, ggml_log(ctx, remaining))));
        return ggml_add(ctx,
            ggml_mul(ctx, ce, ggml_view_1d(ctx, self.coefficients, 1, 0)),
            ggml_mul(ctx, kl, ggml_view_1d(ctx, self.coefficients, 1, sizeof(float))));
    }
};
}
