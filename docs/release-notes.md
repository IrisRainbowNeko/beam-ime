# Beam 0.2.0

新增本地个性化学习：即时个人词库、反馈排序和 QVAC Vulkan 原生 r8 LoRA 训练。
学习组件复用已有 Q8 GGUF，不携带 Python、PyTorch、CUDA runtime 或第二份训练权重。
0.x 为开发阶段版本，版本号统一使用 `0.2.0`，不加 beta 后缀。

## 下载哪个包

所有安装包均为 **x86_64 / x64**。有网络时选轻量包；离线包包含相同程序和模型。

| 平台 | 输入法前端 | 轻量安装包 | 离线完整包 |
|---|---|---|---|
| Arch Linux | Fcitx5-Rime | [下载 .pkg.tar.zst](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-arch-x86_64.pkg.tar.zst) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-arch-x86_64-offline.tar) |
| Ubuntu 24.04 LTS | Fcitx5-Rime | [下载 .deb](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-ubuntu24.04-amd64.deb) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-ubuntu24.04-x86_64-offline.tar) |
| Fedora 44 | Fcitx5-Rime | [下载 .rpm](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-fedora44-x86_64.rpm) | [下载离线 .tar](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-fedora44-x86_64-offline.tar) |
| Windows 11 | 小狼毫 Weasel 0.17.4 | [下载 setup.exe](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/Beam-0.2.0-windows-x64-setup.exe) | [下载 offline.exe](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/Beam-0.2.0-windows-x64-offline.exe) |

## Linux 安装

先在桌面环境启用 Fcitx5。按自己的发行版选择一条命令：

```sh
# Arch Linux
sudo pacman -U ./beam-ime-0.2.0-arch-x86_64.pkg.tar.zst

# Ubuntu 24.04
sudo apt install ./beam-ime-0.2.0-ubuntu24.04-amd64.deb

# Fedora 44
sudo dnf install ./beam-ime-0.2.0-fedora44-x86_64.rpm
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
安装器暂未签名。完整步骤见 [Windows 安装教程](https://github.com/IrisRainbowNeko/beam-ime/blob/main/docs/install-windows.md)。

## 本次更新

- 新增默认关闭的本地个性化：即时词库、反馈排序、空闲时的个人 r8 LoRA 训练，以及暂停、关闭、回退和清空操作。
- 学习组件与普通安装包分开，使用 QVAC 原生 Vulkan 训练，复用 Q8 GGUF；不再分发 Python、PyTorch 或额外训练权重。
- 修复 Windows 中文路径下的 YAML / Lua 文件读取，以及学习组件的长路径解压。
- 四个平台的程序包和独立学习组件使用统一版本，支持在线与离线安装。
- 程序和模型独立升级。已有模型可继续使用，升级 Linux 程序包后运行 `beamctl setup` 即可。

## 学习组件

普通安装后即可开启即时词库；需要周期 LoRA 时再下载对应组件。在线安装只需 manifest，离线安装需同时下载对应的一份 ZIP。

| 平台 | manifest | QVAC Vulkan 学习组件 |
|---|---|---|
| Arch / Ubuntu 24.04 / Fedora 44 | [Linux manifest](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-learning-0.2.0-linux-x86_64-vulkan.json) | [Linux 原生组件 ZIP](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-learning-0.2.0-linux-x86_64-vulkan.zip) |
| Windows 11 | [Windows manifest](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-learning-0.2.0-windows-x86_64-vulkan.json) | [Windows 原生组件 ZIP](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-learning-0.2.0-windows-x86_64-vulkan.zip) |

训练需要可用的 Vulkan 显卡驱动。直接复用已安装的模型，不下载完整精度基座或 CUDA runtime。
详见 [Linux 学习教程](https://github.com/IrisRainbowNeko/beam-ime/blob/main/docs/install-linux.md#本地个性化学习)
和 [Windows 学习教程](https://github.com/IrisRainbowNeko/beam-ime/blob/main/docs/install-windows.md#本地个性化学习)。

## 模型、校验和文档

- [单独下载模型 GGUF，约 610 MiB](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.1.0-beta.1/beam-0.6b-q8_0.gguf)；本版沿用已发布模型。
- [SHA256SUMS](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/SHA256SUMS) · [模型 manifest](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/model-manifest.json)
- [Arch PKGBUILD](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/PKGBUILD) · [源码包](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/beam-ime-0.2.0-source.tar.gz) · [Windows 调试符号](https://github.com/IrisRainbowNeko/beam-ime/releases/download/v0.2.0/Beam-0.2.0-windows-x64-symbols.zip)
- [Linux 安装、升级与卸载](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.2.0/docs/install-linux.md) · [编译与打包](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.2.0/docs/build.md) · [故障排查](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.2.0/docs/troubleshooting.md)

各平台验证范围见 [测试记录](https://github.com/IrisRainbowNeko/beam-ime/blob/main/docs/testing.md)。
Windows 个性化训练为实验支持：安装及原生 CPU 续训已在 Windows 11 虚拟机验证，Vulkan 训练需真实显卡验证。
Windows 中文用户目录下，卸载恢复原版小狼毫 DLL 后可能遇到其部署程序退出 `0xc0000409`；恢复入口会保留，确认 DLL 已恢复后再次卸载可完成清理，详见 [故障排查](https://github.com/IrisRainbowNeko/beam-ime/blob/v0.2.0/docs/troubleshooting.md#卸载后宿主部署失败)。
