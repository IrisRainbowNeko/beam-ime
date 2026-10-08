# 发布流程

程序版本号在 `VERSION`、CMake、诊断命令、安装脚本、PKGBUILD 和发布说明中保持一致。
更新日志后提交代码并创建 `vVERSION` 标签。

Release packages 工作流从模型 manifest 的固定 URL 下载已发布模型，构建 Ubuntu、Arch、
Fedora 44 和 Windows 的轻量及离线包，并自动创建或更新 Release 草稿。
发布者可直接在 GitHub 上公开该 Release。0.x 为开发阶段，版本号不附加 beta 后缀。

自动打包：提交当前版本后推送 `v0.2.1` 标签，会启动 `Release packages` 工作流。
也可在 Actions 中手动运行：`tag` 填 `v0.2.1`，`ref` 填包含该版本代码的分支或提交。
三个 Linux 系统和 Windows 的程序包、离线包、Vulkan 学习组件、源码与校验和会汇总到同一份 Release 草稿。
各构建任务也提供 Actions artifacts，可在草稿生成前单独下载。

发布说明按平台列出前端、轻量包和离线包的直接链接，以及对应的安装命令。
链接中的 tag 与文件名应对应本次上传的资产；模型链接可以继续指向独立的旧版模型资产。

本地构建也可使用 `tools/package_linux.py` 和 `tools/package_windows.py`。
所有文件准备后运行 `python tools/checksums.py dist --model-manifest models/default.json`
生成包含独立模型的 SHA256SUMS；每个 GitHub 资产小于 2 GiB。
保留 Windows 未剥离 DLL 或单独调试符号，用于根据崩溃偏移定位问题。

程序和模型分别版本化。只更新程序时沿用已有模型 manifest；模型变化时发布新的文件和哈希，
旧版资产保持不变。用户原来的自定义模型不会因程序升级而被替换。

个性化学习组件独立于普通安装包。按 [模型开发](models/training.md#个人-lora-训练组件) 分别准备
Linux/Windows 的 Vulkan manifest 和对应 ZIP；三个 Linux 发行版共享在 Ubuntu 24.04 构建的 Linux 学习组件。
Release 工作流构建 QVAC 原生运行时，复用发布 GGUF，不打包 Python、PyTorch、CUDA runtime 或训练 checkpoint。
普通包包含拼音资源、SQLite 和 adapter 推理，安装组件由用户显式确认。
只更新安装入口时可复用已发布组件：在 `models/default.json` 的 `learning` 中固定其 manifest URL，
发布说明链接同一份旧版 manifest 和 ZIP；无需为程序版本重复上传组件。
本地包可用 `--output-dir /path/dist` 改变输出目录，Windows 可用 `--build /path/cross-build` 指向 `out/` 与 `symbols/`。
