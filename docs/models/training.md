# 模型工具

v0.2.1 发布模型采用 `keys_llm_v1`。双模式开发使用 `keys_llm_v2`；任务开关需要配套训练后的模型。
以下工具用于训练模型；开发机使用的 Python/PyTorch 不进入用户安装包。
训练数据需采用 JSONL，每行包含 `keys`、`context`、`target`；直接输出模式不需要拼音切分。

```json
{"keys":"nihao","context":"","target":"你好"}
{"keys":"yonglinux","context":"讨论电脑系统","target":"用Linux"}
```

安装 PyTorch、Transformers、pypinyin、NumPy、safetensors；实际环境版本会写入训练 run-config。

```sh
python tools/models/keys_llm_train.py --model /path/Qwen3-0.6B-Base --prompt-version keys_llm_v1 \
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

## 双模式联合训练

从发布 GGUF 对应的完整精度 checkpoint 继续全参数训练，tokenizer 不增加词表。
先准备句子级 JSONL，`target` 为中文或中英混输原句，`translation` 为教师英文译文：

```json
{"target":"明天见","translation":"See you tomorrow.","session_id":"example-1","context":""}
{"target":"不用安装CUDA","translation":"You do not need to install CUDA.","keys":"byazcuda","session_id":"example-2","context":""}
```

`source` 与 `session_id` 标识会话；同句跨会话出现时合并分组。没有译文的句子仅用于普通任务。
原句和会话先划分 train/dev/test，再展开原始按键、全拼、简拼、混拼，英文词保持原拼写。
上下文最多 64 字。原句限当前 IME 支持的汉字及英文词；数字时间在中文字段中使用对应中文读法。
译文中的数字、标点与缩写不受拼音结构匹配限制。独立测试集需要覆盖否定、时间、专名、术语、口语和混输。

```sh
python tools/models/prepare_dual_task.py --input /path/paired.jsonl /path/ordinary.jsonl \
  --output /path/dual-data
python tools/models/keys_llm_train.py --model /path/release-checkpoint \
  --teacher /path/release-checkpoint --train /path/dual-data/train.jsonl \
  --dev /path/dual-data/dev.jsonl --output /path/dual-run
```

默认配方：AdamW、学习率 `1e-5`、有效 batch 256、microbatch 4、2 epoch、warmup 3%、
序列上限 512、梯度裁剪 1.0。每个有效 batch 固定普通/译英 192:64，较小任务池循环采样。
各任务独立按目标 token 归一化，再以 0.75/0.25 加权；普通任务额外加 0.25 倍旧模型教师 KL。
教师使用其原有 prompt，学生使用新 prompt，两者只对齐相同的正文目标 token。
可用 `--gpu-memory-fraction` 限制共享 GPU 显存；`--device cpu` 用于小模型开发测试。

### 正式训练流水线

`translation_corpus.py` 从 LCCC 原始会话和 GeneInput 抽样，保留会话分组，并补充人工编写的否定、
时间、专名和术语样本。默认抽取 40,000 条 LCCC、20,000 条 GeneInput，其中 20,000 条交给教师翻译，
其余作为普通回放；翻译结构无效的样本只参与普通任务，具体失败原因保存在对应记录中。
开发服务器使用 `Qwen/Qwen3-32B`，固定 revision
`9216db5781bf21249d130ec9da846c4624c16137`，BF16、非思考模式、确定性生成。
教师仅用于离线数据准备。

```sh
python tools/models/translation_corpus.py sample \
  --lccc /path/lccc_base_train.jsonl.gz --geneinput /path/geneinput/train.jsonl \
  --output /path/dual-corpus
CUDA_VISIBLE_DEVICES=0 python -u tools/models/run_dual_training.py \
  --corpus /path/dual-corpus --teacher /path/Qwen3-32B-teacher \
  --teacher-id Qwen/Qwen3-32B \
  --teacher-revision 9216db5781bf21249d130ec9da846c4624c16137 \
  --model /path/release-checkpoint --output /path/new-dual-experiment
```

流水线依次生成译文、划分并展开数据、联合训练、评测新模型和旧模型同集中文结果。
默认教师 batch 16、学生 microbatch 32、GPU 显存比例上限 0.7，其余使用上述首轮配方。
验证集按两种任务各取最多 2,000 条，避免输入文件排序使某种任务漏评。
输出目录必须为新目录；`status.json` 记录当前阶段，每阶段单独写日志，子进程失败即停止并记录退出码。
长任务可放入 tmux 会话运行。模型权重、教师、语料与实验输出保存在仓库外。

### 生成顺序与初始化对照

`run_dual_ablation.py` 复用上述固定 train/dev/test 数据，依次训练 A/B/C 三组：
A 从旧 IME 开始，英文在前；B 从旧 IME 开始，中文在前；C 从原始 0.6B 开始，中文在前。
三组均使用旧 IME 作为普通输入教师，沿用 3:1 抽样、batch 256、学习率 `1e-5`、2 epoch 和相同随机种子。
`--loss-scheme field` 将普通输入、英文正文、中文注释分别按 token 归一化，CE 权重为 0.6/0.3/0.1；
普通输入另外加 0.25 倍教师 KL。分隔符和 EOS 均归入注释部分，避免改变顺序时改变 EOS 的权重。
验证损失单独记录英文与注释，两个 epoch 均做生成评测。

中文先行实验标记为 `keys_llm_v3`，输入 prompt 与 v2 相同，输出为 `中文注释<TAB>英文`。
这是模型实验格式；运行时和 QVAC 接入随胜出配方另行更新。
评测统一解析为英文正文和中文注释，分别记录中文命中、双字段结构、英文夹中文情况，以及 COMET/chrF++。
COMET 的分数不表示正确率；`correct_comment` 是中文注释完全正确的条件子集，各模型的子集大小可能不同。

```sh
CUDA_VISIBLE_DEVICES=0 python -u tools/models/run_dual_ablation.py \
  --data /path/dual-data --ime /path/release-checkpoint --base /path/Qwen3-0.6B-Base \
  --reference /path/previous-dual-run/epoch-2 --output /path/new-ablation \
  --scorer-python /path/comet-env/bin/python --comet-checkpoint /path/comet/checkpoints/model.ckpt \
  --comet-model Unbabel/wmt22-comet-da --comet-revision 2760a223ac957f30acfb18c8aa649b01cf1d75f2
```

COMET 在独立的开发环境安装 `unbabel-comet==2.2.7`，使用 CPU 评分。
先完成三组训练和全部 GPU 生成，再评分；各组与旧模型同集结果写入 `comparison.json`。
默认 microbatch 8、显存比例上限 0.25，可按训练卡资源显式调整；三组使用相同配置。

```sh
python tools/models/keys_llm_eval.py --checkpoint /path/dual-run/epoch-2 \
  --input /path/dual-data/test.jsonl --output /path/dual-eval.json --batch-size 4
python /path/llama.cpp/convert_hf_to_gguf.py /path/dual-run/epoch-2 \
  --outfile /path/dual-q8.gguf --outtype q8_0
PYTHONPATH=/path/llama.cpp/gguf-py python tools/models/tie_embeddings.py \
  /path/dual-q8.gguf /path/beam-dual-q8.gguf --prompt-version keys_llm_v2
```

评测按任务分别输出结果，译英记录正文、中文注释、双字段结构有效率及参考译文精确匹配。
英文精确匹配不能代替语义评价，至少 500 条独立样例还需检查否定、遗漏和两字段一致性。
中文 P@1/P@5 与旧模型同集比较，目标下降不超过 0.5 个百分点；延迟需使用量化后模型分别测 CPU/Vulkan。
新 manifest 使用 `promptVersion: keys_llm_v2`、`modes: [ime, translate_en]` 及实际 GGUF 大小和 SHA-256。

## 个人 LoRA 训练组件

普通安装包内的 C++ 词库可以独立使用。发行版参数学习由 `src/trainer` 的 QVAC 原生进程执行，
直接读取发布的 Q8 GGUF 及其内嵌 tokenizer，不下载第二份训练权重。
`tools/models/personal_trainer.py` 及其 PyTorch 依赖只用于开发对照实验，不进入发行包。

首版配方固定为 q/k/v/o/gate/up/down 全线性投影 r8、alpha16、dropout0，冻结基座及 embedding/输出层。
AdamW 学习率为 2e-4，梯度裁剪 1.0，每轮 24 步，每步通过 microbatch1 累积 8 条近期、4 条历史和 4 条保底输入。
损失为个人 CE + 0.25 倍保底 CE + 基座教师 KL；教师采用 top32 加剩余概率缓存。
近期/长期池按上屏事件采样，避免整句及片段重复放大，按原始按键对齐后增加去重的全拼和简拼变体。
保底样本混合通用数据与独立参考上下文中的同码普通用法。
双模式基座的四条保底固定为 1 条普通中文、1 条同码中文、2 条译英；译英两字段均受 CE 和基座教师 KL 约束。
当任务快照没有可生成的同码普通样本时，该位置仍使用通用中文保底。
个人采集只接收普通输入，旧基座适配器和优化器不会加载到新基座，词库及历史上屏记录继续保留。

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

`--prompt-version keys_llm_v2` 用于双模式模型；省略时采用 `models/default.json` 的 prompt 版本。
内置保底池包含人工双语样本，v1 打包自动排除译英样本。训练器要求组件声明与实际 GGUF 元数据一致。

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
