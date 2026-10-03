# Beam 0.1.0-beta.2

新增 Fedora 44 安装包，并优化推理缓冲区复用。本地 0.6B 模型支持简拼、全拼和中英混输，雾凇提供词典候选。

## 下载哪个包

所有安装包均为 **x86_64 / x64**。有网络时选轻量包；离线包包含相同程序和模型。

| 平台 | 输入法前端 | 轻量安装包 | 离线完整包 |
|---|---|---|---|
| Arch Linux | Fcitx5-Rime | [下载 .pkg.tar.zst](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-arch-x86_64.pkg.tar.zst) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-arch-x86_64-offline.tar) |
| Ubuntu 24.04 LTS | Fcitx5-Rime | [下载 .deb](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-ubuntu24.04-amd64.deb) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-ubuntu24.04-x86_64-offline.tar) |
| Fedora 44 | Fcitx5-Rime | [下载 .rpm](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-fedora44-x86_64.rpm) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-fedora44-x86_64-offline.tar) |
| Windows 11 | 小狼毫 Weasel 0.17.4 | [下载 setup.exe](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/Beam-0.1.0-beta.2-windows-x64-setup.exe) | [下载 offline.exe](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/Beam-0.1.0-beta.2-windows-x64-offline.exe) |

## Linux 安装

先在桌面环境启用 Fcitx5。按自己的发行版选择一条命令：

```sh
# Arch Linux
sudo pacman -U ./beam-ime-0.1.0-beta.2-arch-x86_64.pkg.tar.zst

# Ubuntu 24.04
sudo apt install ./beam-ime-0.1.0-beta.2-ubuntu24.04-amd64.deb

# Fedora 44
sudo dnf install ./beam-ime-0.1.0-beta.2-fedora44-x86_64.rpm
```

安装轻量包后，用桌面用户执行以下命令（不要加 sudo）：

```sh
beamctl model-install --download
beamctl setup
```

然后重新部署 Rime，在方案菜单中选择 **Beam 雾凇**。
离线包解压后运行 `bash install.sh`，会安装程序、导入模型并配置用户服务。
Linux 离线包不包含系统依赖，需预先安装 Fcitx5-Rime 等依赖；不同发行版的插件不能混用。

## Windows 安装

运行下载的 `.exe`。缺少兼容小狼毫时，安装器会提示安装固定版本。
轻量版按提示下载模型；离线版包含模型和小狼毫安装器。
安装器暂未签名。完整步骤见 [Windows 安装教程](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.1.0-beta.2/docs/install-windows.md)。

## 本次更新

- 新增 Fedora 44 RPM、离线包、DNF 安装方式和发行版原生构建任务。
- 复用推理 batch 和搜索缓冲区，减少完整词表 logits 的复制。本机 780M Vulkan 的 366 次请求对照中，平均耗时 57.0 → 54.6 ms，候选及顺序全部一致；结果仅代表该测试环境。
- 程序和模型独立升级。已有模型可继续使用，升级 Linux 程序包后运行 `beamctl setup` 即可。

## 模型、校验和文档

- [单独下载模型 GGUF，约 610 MiB](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.1/beam-0.6b-q8_0.gguf)；本版沿用已发布模型。
- [SHA256SUMS](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/SHA256SUMS) · [模型 manifest](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/model-manifest.json)
- [Arch PKGBUILD](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/PKGBUILD) · [源码包](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/beam-ime-0.1.0-beta.2-source.tar.gz) · [Windows 调试符号](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.2/Beam-0.1.0-beta.2-windows-x64-symbols.zip)
- [Linux 安装、升级与卸载](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.1.0-beta.2/docs/install-linux.md) · [编译与打包](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.1.0-beta.2/docs/build.md) · [故障排查](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.1.0-beta.2/docs/troubleshooting.md)

各平台验证范围见 [测试记录](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.1.0-beta.2/docs/testing.md)。
Windows 已有交叉构建、Wine 与 Unicode GUI 宿主测试；真实 Windows 11 桌面端到端验证仍待完成。
