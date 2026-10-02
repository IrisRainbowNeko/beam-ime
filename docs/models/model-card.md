# Beam 0.6B Q8_0

- Version: 0.1.0-beta.1
- Base: Qwen/Qwen3-0.6B-Base (Apache-2.0)
- Task: keys-conditioned Chinese / English text generation
- Prompt: keys_llm_v1, direct result output
- Quantization: Q8_0; identical output / input embedding tensor stored once
- File: beam-0.6b-q8_0.gguf, 639,446,720 bytes
- SHA-256: 35eccd83d31b123e41313fe59bbcd0fefa2b929865fa66dff8ab77187203309c
- Release: r5-v2ex lineage, published by the maintainer
- License: Apache-2.0

Training used full-parameter SFT and synthetic typing variants. The lineage
includes LCCC, GeneInput, V2EX, Rime vocabulary and maintainer-provided chat and
vocabulary data. The raw training data is not bundled with this repository.
This is the existing model selected by the maintainer for the public release,
not a new training run.

The intended use is local everyday Chinese typing with full pinyin, initials,
mixed syllable prefixes and complete English words. Initials remain ambiguous.
The model can produce unexpected colloquial choices. Candidate scores are not
confidence estimates; the user chooses what to commit.

The input method uses at most 64 recent committed characters as context.
No remote inference is used. More aggressive quantization has not been made
the default. See `docs/testing.md` for measurements from the packaged engine.

Base model: https://huggingface.co/Qwen/Qwen3-0.6B-Base
