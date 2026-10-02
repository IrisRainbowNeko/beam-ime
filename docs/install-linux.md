# Linux 安装

支持 x86_64，桌面使用 Fcitx5-Rime。首次先在桌面环境中启用 Fcitx5；Ubuntu
可运行 `im-config` 选择 Fcitx5 后注销重新登录。Wayland 输入法设置取决于桌面环境。

## Arch Linux

```sh
sudo pacman -S --needed fcitx5 fcitx5-rime python python-yaml
sudo pacman -U ./beam-ime-0.1.0-beta.1-arch-x86_64.pkg.tar.zst
```

Arch 包匹配构建时的 librime。系统滚动更新导致版本不匹配时，使用
`packaging/linux/PKGBUILD` 在当前系统重新构建：`makepkg -si`。

## Ubuntu 24.04

```sh
sudo apt update
sudo apt install fcitx5 fcitx5-rime python3-yaml
sudo apt install ./beam-ime-0.1.0-beta.1-ubuntu24.04-amd64.deb
```

不要将 Arch 的插件复制到 Ubuntu。插件需要与本系统 librime 的 C++ ABI 匹配。

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

先运行 `beamctl disable`，再用 `pacman -R beam-ime` 或 `apt remove beam-ime` 卸载程序。
随后重新部署 Rime。模型、用户词库和备份保留；被用户改过的资源文件也会保留。
用户配置在首次修改前保存为 `default.custom.yaml.beam-backup`。

`beamctl doctor` 提供不含输入文本的诊断。日志：`journalctl --user -u beam-ime.service`。
