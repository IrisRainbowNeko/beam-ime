# 模型工具

v0.2.1 发布模型采用 `keys_llm_v1`。当前片段改进实验使用 `keys_llm_v4`，v2/v3 用于既有双模式对照；任务开关需要配套训练后的模型。
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
运行时和 QVAC 现已支持 v3，以及增加等待状态的 v4。
评测统一解析为英文正文和中文注释，分别记录中文命中、双字段结构、英文夹中文情况，以及 COMET/chrF++。
COMET 的分数不表示正确率；`correct_comment` 是中文注释完全正确的条件子集，各模型的子集大小可能不同。

```sh
CUDA_VISIBLE_DEVICES=0 python -u tools/models/run_dual_ablation.py \
  --data /path/dual-data --ime /path/release-checkpoint --base /path/Qwen3-0.6B-Base \
  --reference /path/previous-dual-run/epoch-2 --output /path/new-ablation \
  --scorer-python /path/comet-env/bin/python --comet-checkpoint /path/comet/checkpoints/model.ckpt \
  --comet-model Unbabel/wmt22-comet-da --comet-revision 2760a223ac957f30acfb18c8aa649b01cf1d75f2
```

COMET 使用独立开发环境，安装 CPU 版 `torch==2.7.1`、`unbabel-comet==2.2.7`、
`transformers==4.51.3` 和 `pytorch-lightning==2.6.6`；评分报告记录实际依赖版本和模型 SHA-256。
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

## 输入片段修复实验

`fragment_corpus.py` 从公开会话生成 30,000 条训练片段，另留各 2,000 条验证和测试片段。
长度桶按 1–2、3–4、5 字以上分配为 50/30/20；上下文中文、英文、混合、空为 50/25/15/10。
原会话与完整发言先划分，再切片；片段使用此前已输入的文本。旧整句集保留原划分。
教师返回 `translation_status`、英文片段和英文上下文；无效教师输出显式计数并从联合数据排除。
可译短词正常输出；`need_more_input` 编码为 `中文<TAB><WAIT>`，不按长度强制等待。

```sh
python tools/models/fragment_corpus.py --lccc /path/lccc_base_train.jsonl.gz \
  --base-corpus /path/ordinary.jsonl /path/paired.jsonl --output /path/fragment-corpus
CUDA_VISIBLE_DEVICES=0 python -u tools/models/run_fragment_training.py \
  --corpus /path/fragment-corpus --output /path/fragment-run \
  --teacher /path/Qwen3-32B-teacher --ime /path/release-checkpoint \
  --baseline /path/B/epoch-1 --scorer-python /path/comet-env/bin/python \
  --comet-checkpoint /path/comet/checkpoints/model.ckpt --llama-source /path/llama.cpp
```

D0 固定 B 权重比较整段 beam、贪心优先、分字段评分。D1 与 D2 使用相同初始化、样本顺序、
长度及上下文分桶、3:1 任务比例、batch256、学习率 5e-6、2 epoch。
D1 使用原字段损失；D2 使用每样本字段均值：普通 CE .65、中文注释 CE .15、英文 CE .20，
旧 IME 教师的普通 KL .325、中文注释 KL .075。注释 KL 只对齐中文内容的同 tokenizer token，
排除跨 TAB 边界的 token、等待标记和 EOS。
D3 从 D2 接着训练 200 步，学习率 1e-6；每步联合回放外加 40 对英文偏好，DPO β=.1、权重 .1。
8,000 对训练集偏好由正确教师译文和实体、数字、否定、遗漏、增译、重复等受控错误构成，
不将合成负例效果等同于真实翻译质量。

结果目录保存 D0–D3 日志、候选、等待/误等待指标、语义评测样本和模型。
中文还原指标包含格式有效的等待结果；等待结果不会被当作已展示的英文候选。
P@1/P@5、等待召回和误等待率附请求级 Wilson 95% 区间，拼法变体存在相关性，不能当作独立原句数。
`semantic-500.jsonl` 供独立语义检查；等待集不足 200 条时 `status.json` 记录实际数量，需补齐后比较目标。
选型先比较中文保持，再比较同一公开验证子集的端到端可用率；D0 并列时选延迟较低的策略。
Qwen3-32B 自动判定忠实度并分列实体、数字、否定、增译、漏译和重复错误，缺失候选计入失败。
这些自动判定与人工检查分别记录，COMET 只作为辅助指标。私人输入回放保存在用户本机、仓库外。
已完成的阶段可以通过 `--start-phase 阶段名` 接续，要求之前的输出已完整写入。
胜出模型导出时使用 `tie_embeddings.py --prompt-version keys_llm_v4 --decoding-strategy 策略名`，
QVAC 组件使用 `--prompt-version keys_llm_v4`；内置回放同时包含正常译英和等待样例。

量化后的本机回放使用隔离 daemon，读取实际展示候选，同时记录请求、生成、搜索的 p50/p95 和峰值驻留内存：

```sh
python tools/models/keys_llm_eval.py --runtime build/release/bin/beamd \
  --checkpoint /path/beam-fragments-q8.gguf --input /path/held-out.jsonl \
  --output /path/cpu-replay.json --ngl 0 --threads 4 --beam-ms 2000
python tools/models/keys_llm_eval.py --runtime build/release/bin/beamd \
  --checkpoint /path/beam-fragments-q8.gguf --input /path/held-out.jsonl \
  --output /path/vulkan-replay.json --ngl 99 --threads 4 --beam-ms 2000 \
  --backend-dir build/release/bin
```

旧模型与新模型使用相同输入、线程数和搜索预算。完整搜索与实际交互预算分开跑，
后者设置 `--beam-ms 150 --client-timeout-ms 400`；超过客户端等待上限的响应计为空候选和超时，
不会作为可用结果抬高准确率。原生等待响应不带中文字段，等待时的中文还原由 checkpoint 评测记录。

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

## 译英 LoRA（D5 路线）

联合微调会让普通输入下降（真实记录约 2.6 个百分点），所以译英用独立的 LoRA：基座保持原 IME，`--translate-only`
只训练译英样本，普通输入不加载 adapter，结果与原模型逐位一致。训练使用不含 `need_more_input` 的数据：

```sh
python tools/models/keys_llm_train.py --model /path/ime-checkpoint --prompt-version keys_llm_v4 \
  --loss-scheme sample_field --fragment-sampling --wait-share 0 --translate-only --lora-rank 32 --teacher-weight 0 \
  --train train-nowait.jsonl --dev dev-nowait.jsonl --output run --epochs 2 --batch-size 256 \
  --micro-batch-size 8 --learning-rate 2e-4 --max-length 512
PYTHONPATH=/path/llama.cpp/gguf-py python tools/models/export_translate_adapter.py \
  --llama-source /path/llama.cpp --base /path/ime-checkpoint --adapter run/epoch-2/adapter \
  --output beam-translate.gguf
```

`beamd --translate-adapter beam-translate.gguf`，或把文件命名为 `beam-translate.gguf` 放在模型旁，即可启用
`translate_en`。adapter 的 GGUF 元数据记录 `beam.prompt_version=keys_llm_v4` 和解码策略；基座已经是双模式模型时拒绝加载。
译英模式只使用译英 adapter，个人 LoRA 仍只作用于普通输入；切换模式会清空 KV 并重新应用 adapter。

### 运行时合并

默认情况下，`beamd` 在进入译英模式时把 adapter 直接加到权重上，离开时换回原权重，不需要第二份模型文件：

- 基座必须是 Q8_0，`beamd` 以非 mmap 方式加载权重（只在存在译英 adapter 时）。adapter 被加成整数量化步长
  （`round(ΔW / 块尺度)`，块尺度保持不变，越界截断到 ±127），不重新量化，所以权重仍是 Q8_0。
- 进入和离开都从模型文件重读原始 Q8 块再写入：离开后权重与文件逐字节相同（`restore` 之后比较全部 196 个矩阵，0 字节差异），
  内存里只多一份 f32 的 adapter（约 80 MB）。
- 进入一次约 0.3 s（AVX2 路径；其他 CPU 退回标量实现，数秒），离开约 0.1 s；这段时间内的第一次译英查询会等待。
  合并后每个 token 少两次小矩阵乘，Vulkan 上译英 p50 约降低 25%。
- 英文 exact-match（semantic-500）：不合并 26.8% / 37.4%（P@1 / P@5），在线合并 27.2% / 37.6%，差异在置信区间内。
- 个人 LoRA 在译英模式下不起作用（权重已合并译英 adapter），回到普通输入后照常生效。

`--translate-merge off` 关闭合并，改用 llama.cpp 的非合并 LoRA（随时可切换，每个 token 稍慢）。
合并需要 llama.cpp 的一个公开访问器 `llama_model_get_tensor`，由 `src/engine/llama-model-tensor.patch` 提供，CMake 配置时自动应用到
llama.cpp 源码（已应用则跳过）。

## 译英 one-stage adapter（当前默认）

当前发布的译英 adapter 仍是 IME 基座上的一个 LoRA（r128），输入按键直接输出 `中文<TAB>English`，由两位教师蒸馏：

1. 语料：`mt_fragments.py` 从 LCCC 切出输入片段，`mt_domain.py` 生成 LCCC 缺少的领域对话（术语、夹英文），
   `mt_teacher.py` 用 OpenAI 兼容接口的大模型标注英文，`mt_hard.py` 挑难例交给更强的教师，`mt_merge.py` 合并去重。
2. 英文教师：`mt_train.py` 在同一语料上全量微调一个 `zh_en_v1`（中文→英文，无按键）模型，只作教师用，
   不随输入法发布；运行时也不加载 `zh_en_v1` adapter。
3. `onestage_data.py` 给语料配上模拟按键（全拼 / 简拼 / 混拼），`onestage_train.py` 训练 LoRA：
   中文部分对齐 IME 自身（同一模型关闭 adapter、用 keys_llm_v1 提示）做 KL 蒸馏，英文部分对齐英文教师。
4. 导出时写入解码策略：

```sh
PYTHONPATH=/path/llama.cpp/gguf-py python tools/models/export_translate_adapter.py \
  --llama-source /path/llama.cpp --base /path/ime-checkpoint --adapter os/epoch-3 \
  --decoding-strategy diverse --output beam-translate.gguf
```

`diverse` 解码：beam 只搜到中文字段末尾的 TAB，保留若干个不同的中文读法，再用一次批量 greedy 给每个读法写英文；
候选不足时用普通 beam 的其他英文补位。读法检查允许一处手误（在至少打了两个字母的音节内错一个、多一个、少一个或相邻对调），
所以有错字的输入也有候选（`Pronunciations::spells`）。`beamd --translate-strategy` 可覆盖 adapter 里的策略。

评测：`onestage_diverse.py`（HF 版同一解码，`--max-edits 1` 对应运行时的手误容忍）、`onestage_compare.py`
（与"IME 选中文 + 再翻译"对比，输出整份前五候选）、`mt_judge.py`（大模型盲评）。在 307 条真实输入记录上（Vulkan，
beam 500 ms），中文 Top1 / Top5 为 78.2% / 93.5%，平均 4.8 个候选，请求延迟 p50 / p95 约 205 / 464 ms。
`key_noise.py` 可按 GeneInput 的打字习惯模拟按键，但用它训练的模型在真实记录上反而更差，默认不使用。
