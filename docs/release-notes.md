# Beam 0.1.0-beta.1

首个独立产品 Beta：本地 0.6B 模型、简拼和中英混输、雾凇候选补充、CPU / Vulkan 推理。

- Arch Linux：安装 `.pkg.tar.zst`；librime 版本不同可使用 PKGBUILD 重建。
- Ubuntu 24.04：安装对应 `.deb`。
- Windows 11：选择轻量 setup.exe 或包含模型与小狼毫的 offline.exe。
- 模型 GGUF 单独提供，大小约 610 MiB；SHA256SUMS 用于校验下载。

安装、配置、编译和排错文档在仓库的 docs 目录。Windows 安装器暂未做代码签名。
Linux 安装后运行 beamctl setup 并重新部署 Rime。
