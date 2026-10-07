// SPDX-License-Identifier: Apache-2.0
#include "store.hpp"
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <cstdlib>

void require(bool value, const char * message) { if (!value) throw std::runtime_error(message); }
int main(int argc, char ** argv) {
    if (argc != 2) return 2;
    auto temp = std::filesystem::temp_directory_path() / ("beam-learning-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    auto event = beam::Json{{"session", "test"}, {"composition", "1"}, {"event_id", "test:1"},
                           {"keys", "xinglanyinqing"}, {"text", "星澜引擎"}, {"first", "星蓝引擎"}, {"context", "新的开发组件"}};
    {
        beam::LearningStore store(temp, argv[1]);
        require(!store.feedback(event), "off must not collect");
        require(store.status()["recent_samples"] == 0, "off has no samples");
        store.set("enabled", true);
        require(store.feedback(event), "first commit learned");
        require(!store.feedback(event), "duplicate ignored");
        require(store.status()["recent_samples"] == 1, "event counted once");
        require(!store.status()["trainer"].contains("checkpoint"), "diagnostics do not expose local paths");
        auto ranked = store.rank("xlyq", "新的开发组件", {"心理预期"}, 5);
        require(std::find(ranked["candidates"].begin(), ranked["candidates"].end(), "星澜引擎") != ranked["candidates"].end(), "initial recall");
        ranked = store.rank("xinglyinq", "新的开发组件", {}, 5);
        require(ranked["candidates"][0] == "星澜引擎", "mixed recall");
        ranked = store.rank("jianchaxinglanyinqingderizhi", "", {"检查星蓝引擎的日志"}, 5);
        require(std::find(ranked["candidates"].begin(), ranked["candidates"].end(), "检查星澜引擎的日志") != ranked["candidates"].end(), "sentence composition");
        store.set("paused", true); event["event_id"] = "test:2";
        require(!store.feedback(event), "paused must not collect");
        require(!store.rank("xlyq", "", {}, 5)["candidates"].empty(), "paused retains recall");
        store.set("enabled", false);
        require(store.rank("xlyq", "", {"心理预期"}, 5)["candidates"].size() == 1, "disabled has only model");
    }
    {
        beam::LearningStore store(temp, argv[1]);
        store.set("enabled", true); store.set("paused", false);
        require(!store.rank("xlyq", "", {}, 5)["candidates"].empty(), "persistent recall");
        for (int i = 2; i <= 520; ++i) { event["event_id"] = "test:" + std::to_string(i); store.feedback(event); }
        require(store.status()["recent_samples"] == 512, "recent bounded");
        require(store.status()["confirmed_events"] == 520, "confirmation count");
        auto snapshot = store.snapshot();
        require(snapshot["history"].size() == 8, "history excludes recent events");
        auto segmented = beam::Json{{"event_id", "segmented:1"}, {"keys", "xinglanyinqingnihao"},
            {"text", "星澜引擎你好"}, {"first", "星蓝引擎你好"}, {"parts", beam::Json::array({
                {{"keys", "xinglanyinqing"}, {"text", "星澜引擎"}, {"first", "星蓝引擎"}},
                {{"keys", "nihao"}, {"text", "你好"}, {"first", "你好"}}})}};
        require(store.feedback(segmented), "segmented commit learned");
        auto parts = store.snapshot()["recent"][0]["samples"];
        require(parts[1]["correction"] == true && parts[2]["correction"] == false, "correction belongs to the selected segment");
        if (std::getenv("BEAM_TEST_LEARNING_BENCH")) {
            const std::vector<std::string> chars = {"一", "二", "三", "四", "五", "六", "七", "八", "九", "十"};
            const std::vector<std::string> keys = {"yi", "er", "san", "si", "wu", "liu", "qi", "ba", "jiu", "shi"};
            for (int i = 0; i < 4096; ++i) {
                std::string text = "星", code = "xing";
                for (int n = i, j = 0; j < 4; ++j, n /= 10) { text += chars[n % 10]; code += keys[n % 10]; }
                store.feedback({{"event_id", "perf:" + std::to_string(i)}, {"keys", code}, {"text", text}});
            }
            auto found = store.rank("xinglyinq", "", {}, 5);
            require(found["candidates"][0] == "星澜引擎", "old mixed-code word survives a large vocabulary");
            std::vector<double> times;
            for (int i = 0; i < 200; ++i) {
                auto start = std::chrono::steady_clock::now();
                store.rank("jianchaxinglanyinqingderizhi", "检查组件", {"检查星蓝引擎的日志"}, 5);
                times.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
            }
            std::sort(times.begin(), times.end());
            std::cout << "4096-term ranking p95_ms=" << times[times.size() * 95 / 100] << '\n';
        }
        store.reset();
        require(!store.enabled() && store.status()["words"] == 0, "reset clears and disables");
    }
    std::filesystem::remove_all(temp);
    std::cout << "learning tests passed\n";
}
