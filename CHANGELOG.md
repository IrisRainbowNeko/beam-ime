# Changelog

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
