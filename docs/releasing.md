# 发布流程

程序版本号在 `VERSION`、CMake、诊断命令、安装脚本、PKGBUILD 和发布说明中保持一致。
更新日志后提交代码并创建 `vVERSION` 标签。

Release packages 工作流从模型 manifest 的固定 URL 下载已发布模型，构建 Ubuntu、Arch、
Fedora 44 和 Windows 的轻量及离线包，并自动创建或更新 Release 草稿。
发布者可直接在 GitHub 上公开该 Beta Release。

发布说明按平台列出前端、轻量包和离线包的直接链接，以及对应的安装命令。
链接中的 tag 与文件名应对应本次上传的资产；模型链接可以继续指向独立的旧版模型资产。

本地构建也可使用 `tools/package_linux.py` 和 `tools/package_windows.py`。
所有文件准备后运行 `python tools/checksums.py dist --model-manifest models/default.json`
生成包含独立模型的 SHA256SUMS；每个 GitHub 资产小于 2 GiB。
保留 Windows 未剥离 DLL 或单独调试符号，用于根据崩溃偏移定位问题。

程序和模型分别版本化。只更新程序时沿用已有模型 manifest；模型变化时发布新的文件和哈希，
旧版资产保持不变。用户原来的自定义模型不会因程序升级而被替换。
