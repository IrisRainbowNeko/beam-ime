# Beam IME

A local Chinese input method powered by a keys-conditioned 0.6B language model.
Type initials, partial/full pinyin, or Chinese-English mixed input. Beam returns
whole-input candidates and keeps Rime Ice available as a fallback.

**Beta 0.1.0-beta.2:** x86_64 Arch Linux, Ubuntu 24.04, Fedora 44 and Windows 11.
[Downloads](https://github.com/IrisRainbowNeko/beam-ime/releases).

- Linux: install the distribution package, run `beamctl model-install --download`,
  run `beamctl setup`, redeploy Rime and choose Beam.
- Windows: run the light or offline installer. It installs the compatible
  Weasel version when needed. The offline installer includes the model.
- The model runs locally through llama.cpp with an optional Vulkan backend and
  a CPU fallback. Context is limited to text committed in the current Rime session.

[Build instructions](docs/build.md), [architecture](docs/architecture.md),
[model card](docs/models/model-card.md), [privacy](PRIVACY.md).

Build: `bash tools/build.sh release`. Model-free tests: `bash tools/build.sh tests`.
Source code is Apache-2.0; bundled dependencies keep their original licenses.
