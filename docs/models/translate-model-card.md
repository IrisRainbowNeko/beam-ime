# Beam translate_en adapter v1

- Version: translate-en-v1
- Base: Beam 0.6B Q8_0 (`beam-0.6b-q8_0.gguf`, r5-v2ex lineage, from Qwen/Qwen3-0.6B-Base)
- Type: LoRA, rank 128, alpha 256, on all attention and MLP projections
- Task: keys in, `中文<TAB>English` out (prompt `keys_llm_v4`, `任务：译英`)
- Decoding: `diverse` (stored in the GGUF as `beam.decoding_strategy`)
- File: `beam-translate-en-v1.gguf`, 161,508,160 bytes
- SHA-256: see `SHA256SUMS` in the release
- License: Apache-2.0

Ordinary input never loads this adapter, so Chinese typing is unchanged. On entering English
translation mode `beamd` folds the adapter into the Q8_0 weights (about 0.3 s once) and restores the
original weights byte for byte on leaving; the personal LoRA is not active while translating.
The adapter needs a `beamd` built from `main` after this release; the 0.2.1 packages do not load it.

## Training

589,567 training rows: 522,570 input fragments cut from LCCC conversations, 57,469 fragments of
LLM-written chat in domains LCCC lacks (technical terms, embedded English), 6,466 GeneInput training
targets and 3,062 maintainer-written rows. English labels come from Qwen3.8-27B, Qwen3-32B and
GLM-5.3-Flash. Keys are simulated from pypinyin (full spelling, initials, mixed prefixes). The LoRA is
trained for three epochs with two distillation targets: the Chinese field follows the IME itself
(same model, adapter disabled), the English field follows a fully fine-tuned Chinese-to-English model
trained on the same corpus. Tools: `tools/models/onestage_data.py`, `onestage_train.py`,
`export_translate_adapter.py`; see `docs/models/training.md`. No private typing records were used.

GeneInput publishes its data without a license; LCCC is MIT. The raw training data is not bundled.

## Evaluation

On 307 real typing records of the maintainer (Vulkan on a Radeon 780M iGPU, beam budget 500 ms):
the right Chinese is first in 78.2% and among the five candidates in 93.5%; 4.8 candidates per input;
request latency p50 / p95 about 205 / 464 ms. The previous two-stage design (IME picks the Chinese,
a separate adapter translates it) scored 79.2% / 89.9% at 250 / 417 ms on the same records.
On public fragment sets judged blind by Qwen3.8-27B, the first English candidate scores at or above
the two-stage design on most spellings.

## Limitations

The English is only right when the Chinese reading is right. Alternatives for proper nouns can be odd
(新加坡 may also offer "Pune"). Initials remain ambiguous. One typing slip per syllable typed with two
or more letters is tolerated; more is not. Candidate order is not a confidence estimate.

The PEFT checkpoint (`beam-translate-en-v1-lora.tar.gz`) applies to the full-precision r5-v2ex
checkpoint, published as bf16 at [7eu7d7/beam-ime-0.6b](https://huggingface.co/7eu7d7/beam-ime-0.6b); its
`adapter_config.json` names that base `beam-0.6b-r5-v2ex`. The GGUF works with the released Q8_0 model.
