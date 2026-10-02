# 发布流程

版本号在 `VERSION`、CMake、默认模型 manifest 和发布说明中保持一致。
更新日志后提交代码并创建 `vVERSION` 标签。

先创建 GitHub Release 草稿并上传 manifest 对应的 GGUF，再触发 Release packages 工作流。
工作流下载该模型，构建 Ubuntu、Arch 和 Windows 的轻量及离线包，将制品上传至同一草稿。
发布者可直接在 GitHub 上公开该 Beta Release。

本地构建也可使用 `tools/package_linux.py` 和 `tools/package_windows.py`。
所有文件准备后运行 `python tools/checksums.py dist --model-manifest models/default.json`
生成包含独立模型的 SHA256SUMS；每个 GitHub 资产小于 2 GiB。
保留 Windows 未剥离 DLL 或单独调试符号，用于根据崩溃偏移定位问题。

程序和模型分别版本化。只更新程序时沿用已有模型 manifest；模型变化时发布新的文件和哈希，
旧版资产保持不变。用户原来的自定义模型不会因程序升级而被替换。
