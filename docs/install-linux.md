# Linux 安装

支持 x86_64，桌面使用 Fcitx5-Rime。首次先在桌面环境中启用 Fcitx5；Ubuntu
可运行 `im-config` 选择 Fcitx5 后注销重新登录。Wayland 输入法设置取决于桌面环境。

## Arch Linux

```sh
sudo pacman -S --needed fcitx5 fcitx5-rime python python-yaml
sudo pacman -U ./beam-ime-0.2.0-arch-x86_64.pkg.tar.zst
```

Arch 包匹配构建时的 librime。系统滚动更新导致版本不匹配时，使用
`packaging/linux/PKGBUILD` 在当前系统重新构建：`makepkg -si`。

## Ubuntu 24.04

```sh
sudo apt update
sudo apt install fcitx5 fcitx5-rime librime-plugin-lua python3-yaml
sudo apt install ./beam-ime-0.2.0-ubuntu24.04-amd64.deb
```

## Fedora 44

```sh
sudo dnf install fcitx5 fcitx5-rime librime-lua python3-pyyaml
sudo dnf install ./beam-ime-0.2.0-fedora44-x86_64.rpm
```

Fedora Workstation 默认的 IBus 与 Beam 使用的 Fcitx5 是不同前端，需要先在桌面环境启用 Fcitx5。
RPM 会安装匹配的 librime 依赖，程序运行库位于 `/usr/lib64/beam-ime`。

不要混用不同发行版的插件。插件需要与本系统 librime 的 C++ ABI 匹配。

## 模型和首次启用

以下命令不要加 sudo：

```sh
beamctl model-install --download
beamctl setup
```

离线完整包解开后运行 `bash install.sh`，或者自行安装程序包后运行：

```sh
beamctl model-install --source ./beam-0.6b-q8_0.gguf
beamctl setup
```

离线包包含程序和模型，系统依赖需事先安装。重新部署 Rime 后，在 F4 或 Ctrl+`
菜单中选「Beam 雾凇」。首次部署雾凇词库需要一些时间。

已有兼容模型：`beamctl setup --model /absolute/path/model.gguf`。
旧版 `~/.local/share/beam-ime/models/beam.gguf` 会在没有新模型时被自动沿用。
强制 CPU：`beamctl setup --cpu`。

## 升级和卸载

升级程序包后运行 `beamctl setup`，模型不需要重复下载。librime 升级后需要匹配的新包或重编译。

先运行 `beamctl disable`，再按发行版使用 `sudo pacman -R beam-ime`、
`sudo apt remove beam-ime` 或 `sudo dnf remove beam-ime` 卸载程序。
随后重新部署 Rime。模型、用户词库和备份保留；被用户改过的资源文件也会保留。
用户配置在首次修改前保存为 `default.custom.yaml.beam-backup`。

`beamctl doctor` 提供不含输入文本的诊断。日志：`journalctl --user -u beam-ime.service`。
## 本地个性化学习

普通安装后，学习默认关闭。可从 Rime 菜单开启“学习开启”，或运行 `beamctl learn enable`。
词库立即学习，训练组件需单独安装：

```sh
beamctl learn install --manifest ./beam-learning-0.2.0-linux-x86_64-vulkan.json --download
# 离线资产与 manifest 位于同一目录时：
beamctl learn install --manifest ./beam-learning-0.2.0-linux-x86_64-vulkan.json --source .
beamctl learn status
beamctl learn train
```

使用 Release 中与当前模型匹配的实际 manifest 文件名。`--download` 明确允许下载其中列出的固定资产；
离线安装核对同样的大小与 SHA-256，失败不替换已安装的训练组件。
原生 Vulkan 组件适用于三个 Linux 发行版，复用当前 Q8 GGUF，不再下载完整精度训练权重。
训练组件与普通程序包独立发布，下载 manifest 和一份 ZIP 即可离线安装，无需配置 Python 或 PyTorch。
需要安装对应显卡的 Vulkan 驱动；建议学习时至少有 4 GiB 可用内存，并为训练状态保留磁盘空间。
默认在累计 64 条新上屏记录、连续 5 分钟无 Beam 输入、接通电源时训练，成功后间隔至少 4 小时。
`train` 手动触发一轮；输入恢复后训练在当前 microbatch 结束时暂停并释放内存，空闲后续跑。
`pause` 暂停采集及训练，`resume` 恢复，`disable` 停用个性化，`rollback` 恢复上一适配器及训练状态。
`reset --yes` 清空个人学习数据。全部操作都以桌面用户运行。

没有可用 Vulkan 驱动时，训练状态显示错误，已学词库仍可使用；普通 CPU 推理不受影响。
从旧版 Python 学习组件升级时，安装新的 Vulkan manifest。已有适配器可继续使用，下一轮建立原生优化器状态。
