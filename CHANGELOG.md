# Changelog

## 0.2.0 - 2026-10-07

- Opt-in commit feedback, persistent personal vocabulary, mixed-pinyin recall and contextual ranking.
- Bounded recent/replay samples and non-blocking Rime feedback with per-segment correction evidence.
- Local r8 LoRA training with replay, teacher KL, cooperative pause and exact optimizer/gradient resume.
- Composition-boundary adapter activation, paired rollback and model fingerprint isolation.
- Native QVAC Vulkan learning components, offline installation and `beamctl learn` controls; reuse the installed GGUF without Python, PyTorch, CUDA runtime or extra training weights.
- SQLite/OpenSSL packaging dependencies and model-free plus optional real-adapter regression tests.

## 0.1.0-beta.2 Windows Installer Fix - 2026-10-04

- Read Weasel's numeric version when its file-version text is empty.
- Initialize empty Rime configuration files and allow cleanup after a failed install.
- Preserve Unicode paths when reading installation state and add persistent setup logs.
- Test Windows PowerShell 5.1 and PowerShell 7; verify installation and restoration in Windows 11 with UAC enabled.

## 0.1.0-beta.2 - 2026-10-03

- Fedora 44 RPM and offline packages, native build jobs and DNF installation instructions.
- Distribution-specific backend library paths, including Fedora's `lib64` layout.
- Reuse inference batches and beam-search buffers; avoid copying full vocabulary logits.
- Direct light/offline download links and per-platform installation commands in release notes.
- Reuse the independently published model when releasing a program update.

## 0.1.0-beta.1 - 2026-10-02

- Standalone C++ Beam product repository with Rime integration.
- Shared released 0.6B Q8 model and atomic model installation.
- Arch / Ubuntu packages and Windows light / offline installer tooling.
- Optional dynamically loaded Vulkan backend and CPU fallback.
- Typed request validation, bounded IPC buffers and UTF-8 validation.
- Model-free tests, file restoration tests, CI and release automation.
