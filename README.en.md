# Beam IME

A local Chinese input method powered by a keys-conditioned 0.6B language model.
Type initials, partial/full pinyin, or Chinese-English mixed input. Beam returns
whole-input candidates and keeps Rime Ice available as a fallback.

**Version 0.2.1:** x86_64 Arch Linux, Ubuntu 24.04, Fedora 44 and Windows 11.
[Downloads](https://github.com/IrisRainbowNeko/beam-ime/releases).

![How Beam works: the same keys go through Rime Ice and through Beam-LLM; Beam uses the context and writes whole-sentence candidates locally](docs/images/overview.en.svg)

A classic pinyin IME expands each initial into syllables, looks them up and joins words by
frequency, so long abbreviations only work when the dictionary happens to have the phrase.
Beam gives the context and the keys to Beam-LLM, a local Qwen3-0.6B fine-tune (Q8_0, llama.cpp),
which writes the whole sentence: an incremental Top-1 first, then a beam search bounded to
100 ms per key. Nothing leaves the computer, and if Beam takes over 400 ms the Rime Ice
candidates are shown for that key. All candidates in the figure are measured.

- Linux: install the distribution package, run `beamctl model-install --download`,
  run `beamctl setup`, redeploy Rime and choose Beam.
- Windows: run the light or offline installer. It installs the compatible
  Weasel version when needed. The offline installer includes the model.
- The model runs locally through llama.cpp with an optional Vulkan backend and
  a CPU fallback. Context is limited to text committed in the current Rime session.

## Local Personalization

Learning is opt-in: select **下载并开启模型学习** in the Rime schema menu to download,
install and enable the native component in the background (about 26 MiB on Linux,
34 MiB on Windows). Compatible installed components are reused. For vocabulary-only
learning, use the learning toggle or `beamctl learn enable`.
Confirmed words become available immediately, with feedback-based ranking.
The separate native QVAC Vulkan component reuses the installed Q8 GGUF for
periodic local r8 LoRA updates. Training pauses when input resumes.
Pause keeps learned results active; disable turns personalization off.

Learning requires a working Vulkan GPU driver. The component includes no Python,
PyTorch, CUDA runtime or additional full-precision base weights. See the
[Linux](docs/install-linux.md#本地个性化学习) and
[Windows](docs/install-windows.md#本地个性化学习) instructions.

[Learning-system flowchart (Chinese)](docs/images/learning-system.drawio.png) ·
[Editable diagram](docs/images/learning-system.drawio).

[Build instructions](docs/build.md), [architecture](docs/architecture.md),
[model card](docs/models/model-card.md), [privacy](PRIVACY.md).

Build: `bash tools/build.sh release`. Model-free tests: `bash tools/build.sh tests`.
Source code is Apache-2.0; bundled dependencies keep their original licenses.
