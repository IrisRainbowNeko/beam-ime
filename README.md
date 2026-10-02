# Beam 输入法

用首字母、部分拼音、全拼或中英文混输，直接生成整句候选。
Beam 在本机运行 0.6B 语言模型，通过 Rime 接入桌面输入法；雾凇候选作为日常输入的补充。

[下载安装包](https://github.com/IrisRainbowNeko/beam-ime/releases) · [English](README.en.md) · [安装教程](docs/install-linux.md) · [Windows](docs/install-windows.md)

## 下载和安装

当前版本：**0.1.0-beta.1**。面向 x86_64 的 Arch Linux、Ubuntu 24.04 和 Windows 11。

| 系统 | 安装包 | 教程 |
|---|---|---|
| Arch Linux | `.pkg.tar.zst`，另有 PKGBUILD | [Linux 安装](docs/install-linux.md) |
| Ubuntu 24.04 | `.deb` | [Linux 安装](docs/install-linux.md) |
| Windows 11 | `windows-x64-setup.exe` / `windows-x64-offline.exe` | [Windows 安装](docs/install-windows.md) |

轻量包在安装或首次配置时下载模型。离线完整包包含同一个模型；Windows 离线包也包含小狼毫。
Linux 的离线包仍需要系统已有 Fcitx5-Rime 及发行版依赖。

建议至少 4 GiB 内存、3 GiB 可用磁盘空间。GPU 不是必需项；Vulkan 不可用时使用 CPU。

Linux 安装程序包后，以桌面用户运行：

```sh
beamctl model-install --download
beamctl setup
```

在 Rime 菜单中重新部署，然后选择 **Beam 雾凇**。Windows 安装器会完成小狼毫、模型和方案配置。

## 使用

- 首字母：`nhsj`；全拼：`nihao`；混合输入：`yonglinux`。
- 前面最多五个候选来自模型，后面接雾凇词典候选。
- 菜单中的「智能 / 经典」开关可切回雾凇。
- 模型服务不可用或响应超时后，当前按键自动使用雾凇候选。
- 上下文来自当前 Rime 会话已经上屏的文字，最多取最近 64 字；可完全关闭。

模型适合中文日常输入和中英混输。简拼会有歧义，补充几个拼音字母通常能缩小候选范围。
数字、URL、特殊符号和英文词前缀补全不属于模型的完整输入能力，继续由 Rime 处理。

本次发布模型的实际无上下文示例：`nihao` → `你好`，`yonglinux` → `用Linux`，
`nhsj` → `你换手机`。最后一例也说明简拼不是唯一映射；详见 [测试记录](docs/testing.md)。

## 构建和开发

```sh
git clone https://github.com/IrisRainbowNeko/beam-ime.git
cd beam-ime
bash tools/build.sh release
```

依赖和完整命令见 [编译指南](docs/build.md)。只运行无模型测试：

```sh
bash tools/build.sh tests
```

| 目录 | 内容 |
|---|---|
| `src/engine` | llama.cpp 推理、按键与本机通信 |
| `src/daemon` | `beamd` 服务 |
| `src/rime` | 插件与雾凇方案 |
| `packaging` | Linux / Windows 打包和安装 |
| `tools/models` | 训练、评测、GGUF 工具 |
| `tests` | 核心、配置、安装恢复和可选模型测试 |

[架构与协议](docs/architecture.md) · [配置与排错](docs/troubleshooting.md) · [模型说明](docs/models/model-card.md) · [构建记录](docs/testing.md)

## 项目说明

本项目是 Beam 研究实现的产品化版本，保留 keys-conditioned LLM、KV 复用、草稿验证和限时 beam search。
程序自写代码使用 Apache-2.0；Rime、小狼毫、雾凇和其他依赖保留各自许可，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

输入推理在本机完成，默认不记录按键或候选文本，也不上传输入内容。详见 [隐私说明](PRIVACY.md)。
反馈问题请附 `beamctl doctor` 或 Windows 诊断输出，参见 [贡献指南](CONTRIBUTING.md)。
