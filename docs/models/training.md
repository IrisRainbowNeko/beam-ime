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
