# 模型工具

本次发布复用既有模型；以下工具用于复现训练方法或训练自己的兼容模型。
训练数据需采用 JSONL，每行包含 `keys`、`context`、`target`；直接输出模式不需要拼音切分。

```json
{"keys":"nihao","context":"","target":"你好"}
{"keys":"yonglinux","context":"讨论电脑系统","target":"用Linux"}
```

安装 PyTorch、Transformers、pypinyin、NumPy、safetensors；实际环境版本会写入训练 run-config。

```sh
python tools/models/keys_llm_train.py --model /path/Qwen3-0.6B-Base \
  --train /path/train.jsonl --dev /path/dev.jsonl --output runs/public \
  --no-segments --epochs 1 --batch-size 256 --micro-batch-size 32
python tools/models/keys_llm_eval.py --help
```

使用固定 revision 的 llama.cpp 转换模型：

```sh
python /path/llama.cpp/convert_hf_to_gguf.py runs/public/epoch-1 \
  --outfile model-q8.gguf --outtype q8_0
PYTHONPATH=/path/llama.cpp/gguf-py python tools/models/tie_embeddings.py model-q8.gguf model-tied.gguf
```

去重工具只有在两份 embedding 权重逐字节相同时才会移除输出层。发布新模型时更新
manifest、模型卡和评测结果，不修改旧版本的文件内容。

## 个人 LoRA 训练组件

普通安装包内的 C++ 词库可以独立使用。发行版参数学习由 `src/trainer` 的 QVAC 原生进程执行，
直接读取发布的 Q8 GGUF 及其内嵌 tokenizer，不下载第二份训练权重。
`tools/models/personal_trainer.py` 及其 PyTorch 依赖只用于开发对照实验，不进入发行包。

首版配方固定为 q/k/v/o/gate/up/down 全线性投影 r8、alpha16、dropout0，冻结基座及 embedding/输出层。
AdamW 学习率为 2e-4，梯度裁剪 1.0，每轮 24 步，每步通过 microbatch1 累积 8 条近期、4 条历史和 4 条保底输入。
损失为个人 CE + 0.25 倍保底 CE + 基座教师 KL；教师采用 top32 加剩余概率缓存。
近期/长期池按上屏事件采样，避免整句及片段重复放大，按原始按键对齐后增加去重的全拼和简拼变体。
保底样本混合通用数据与独立参考上下文中的同码普通用法。

训练时输入会请求暂停；在 microbatch 边界保存适配器、累积梯度、优化器和随机数状态后退出。
再次空闲时读取固定任务快照续跑。导出独立 GGUF adapter，完成组词后加载，无需重写基座。
同一基座保留当前及上一套完整状态，回退后下一轮从恢复的优化器继续。

### 构建运行时

依赖 C++17、CMake、Ninja、Git、Vulkan 开发文件、glslc、SQLite 和 OpenSSL。
QVAC revision、源码哈希及补丁由仓库锁定，与 daemon 的 llama.cpp 分别编译为独立进程。
Python/NumPy 只供构建测试使用，用户运行训练器不需要 Python。

```sh
python3 -m pip install numpy==2.2.6 PyYAML==6.0.2
python3 tools/build_learning.py
```

Linux 组件在 Ubuntu 24.04 构建以兼容支持的 Linux 发行版。Windows 可在 MSYS2 MinGW x64 环境用相同命令构建，
也可复用普通包准备的 MinGW/Vulkan 依赖交叉编译：

```sh
bash packaging/windows/build.sh deps beamd
python3 tools/build_learning.py --cross --build-dir build/learning-win --prefix build/learning-win-runtime -- \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/packaging/windows/mingw-w64-x86_64.cmake" \
  -DBEAM_WIN_PREFIX="$PWD/build/win/prefix" \
  -DVulkan_INCLUDE_DIR="$PWD/build/win/prefix/include" \
  -DVulkan_LIBRARY="$PWD/build/win/prefix/lib/libvulkan-1.dll.a" \
  -DVulkan_GLSLC_EXECUTABLE="$(command -v glslc)" -DCMAKE_POLICY_VERSION_MINIMUM=3.5
```

发布默认 Vulkan，不自动退回 CPU 训练。CPU 后端用于无 GPU 的小模型 CI；普通 CPU 输入推理独立工作。

### 打包与离线安装

默认保底池在 `src/trainer/replay.json`，为人工编写的通用 `{keys,context,target}` 样本。
可用 `--replay` 指定 16 到 256 条公开样本。提供已编译的运行时、对应 GGUF 和普通包生成的拼音资源：

```sh
python tools/package_learning.py \
  --runtime build/learning-runtime --model models/beam-0.6b-q8_0.gguf \
  --pinyin build/release/data/pinyin.tsv --platform linux-x86_64 \
  --release-url "https://github.com/IrisRainbowNeko/beam-ime/releases/download/v$(cat VERSION)"
```

输出 `beam-learning-<版本>-<平台>-vulkan.json` 和一份 ZIP。
Windows 使用 `--runtime build/learning-win-runtime --platform windows-x86_64`。
组件包含原生可执行文件、动态 CPU/Vulkan 后端、拼音、保底样本及许可证；不含 Python、PyTorch 或完整基座。
在线和离线安装使用相同大小及 SHA-256 校验。模型的完整 SHA-256 同时绑定基座权重与内嵌 tokenizer。
`--output-dir` 可将资产放到独立磁盘；manifest 中的资源路径相对于组件目录。

### 测试

```sh
ctest --test-dir build/learning --output-on-failure
BEAM_TEST_MODEL=/path/model.gguf BEAM_TEST_BINARY="$PWD/build/release/bin/beamd" \
  BEAM_TEST_ADAPTER=/path/generation/manifest.json BEAM_TEST_NGL=99 \
  python -m unittest discover -s tests -p test_daemon.py -v
BEAM_TEST_LEARNING_BENCH=1 build/release/bin/beam_learning_tests build/release/data/pinyin.tsv
```

第一项验证 CE/KL 的数值梯度、梯度裁剪、累积重置，以及随机小型 Qwen3 的连续训练和 microbatch 续训一致性。
普通 CI 只使用 NumPy 生成微型 GGUF，不下载完整模型，也不安装 PyTorch。
第二项单独验证真实 Q8 adapter 的加载、组词屏障、禁用、回退、失败保留以及更换基座。
