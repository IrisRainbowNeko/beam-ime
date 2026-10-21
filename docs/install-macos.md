# macOS 安装（预览版）

macOS 版基于鼠须管 Squirrel 1.1.2，需要 Apple 芯片（M1 或更新）和 macOS 13 或更新版本。
推理使用 Metal；支持整句候选和译英，本地个性化学习暂不支持。

下载 `Beam-<版本>-macos-arm64.pkg`（轻量版）或 `-offline.pkg`（离线版）。
轻量版首次启动时在后台下载模型（约 610 MiB）和译英 adapter（约 154 MiB）；离线版已包含两者。

1. 预览版未做 Apple 公证。双击安装包被拦截时，在 Finder 中右键安装包选择「打开」，
   或到「系统设置 → 隐私与安全性」点击「仍要打开」。可先用 `shasum -a 256` 核对 SHA256SUMS。
2. 按提示输入管理员密码。未安装鼠须管时会一并安装官方 1.1.2；这种情况下安装结束后需要注销并重新登录。
3. 安装器会启用鼠须管输入法。菜单栏输入法列表里没有「鼠须管」时，到「系统设置 → 键盘 → 输入法 → 编辑 → +」
   在「简体中文」下添加「鼠须管」；仍找不到就注销后重新登录。
4. 切换到鼠须管，按 ``Ctrl+` `` 或 `F4` 打开方案选单，选择「Beam 雾凇」。
   模型下载完成前显示普通雾凇候选；`beamctl doctor` 的 `downloading` 显示下载进度。

已安装其他版本鼠须管时：低于 1.1.2 会升级到 1.1.2；高于 1.1.2 时安装器会拒绝，请等待 Beam 更新。

## 安装做了什么

- Beam 程序装在 `/Library/Application Support/Beam`。
- 插件 `librime-beam.dylib` 放进 `Squirrel.app/Contents/Frameworks/rime-plugins`，
  `Contents/MacOS/Squirrel` 换成支持图片皮肤的同版本编译（见下文「图片皮肤」），librime 等其余文件不变。
  改动会破坏鼠须管原有签名，安装器会用本机 ad-hoc 签名重签鼠须管，并保留原有 entitlements；
  原版鼠须管先备份到 `/Library/Application Support/Beam/backup`。
- 为当前登录用户把雾凇和 Beam 方案装进 `~/Library/Rime`，在 `default.custom.yaml` 的方案列表中加入 `beam_ice`
  （先备份为 `default.custom.yaml.beam-backup`），并注册 LaunchAgent `io.github.irisrainbowneko.beam-ime`。
- 模型在 `~/Library/Application Support/beam-ime/models`，日志在 `~/Library/Logs/beam-ime`，
  安装日志在 `/Library/Logs/Beam/install.log`。

同一台 Mac 的其他用户各自在终端运行一次：

```bash
"/Library/Application Support/Beam/bin/beamctl" setup
```

## 配置与排错

```bash
"/Library/Application Support/Beam/bin/beamctl" doctor
```

输出中 `plugin` 和 `squirrelSignatureValid` 应为 `true`，模型下载完成后 `modelInstalled` 为 `true`，
`service` 显示后台服务状态。下载进度和错误见 `~/Library/Logs/beam-ime/agent.log`。
日志中出现 GPU 推理崩溃后，服务会自动改用 CPU 重启。强制使用 CPU：

```bash
"/Library/Application Support/Beam/bin/beamctl" setup --cpu
```

## 图片皮肤

Beam 安装的鼠须管程序由同版本 1.1.2 源码加一个小补丁编译，配色方案可以用一张图片做候选窗背景
（类似 Fcitx5 的图片主题）。在 `~/Library/Rime/squirrel.custom.yaml` 中：

```yaml
patch:
  style/color_scheme: my_skin
  preset_color_schemes/my_skin:
    name: 我的皮肤
    back_image: skins/panel.png          # 相对路径在 ~/Library/Rime 下
    back_image_slice: {left: 116, right: 220, top: 0, bottom: 0}    # 九宫格：四角保持原尺寸，边和中间拉伸
    back_image_padding: {left: 69, right: 195, top: 63, bottom: 36} # 窗口边缘到候选区的距离，只显示图片
    back_color: 0x00FFFFFF               # 透明底色，露出图片
    border_color: 0x00FFFFFF
    candidate_text_color: 0xDB2E61
```

`back_image_slice` 和 `back_image_padding` 的单位是图片的点（72 dpi 的图片即像素）。从 Fcitx5 主题转换时，
前者对应 `[InputPanel/Background/Margin]`，后者对应 `[InputPanel/ContentMargin]`。改完后在鼠须管菜单选「重新部署」。

## 鼠须管自动更新

鼠须管自带的「检查更新」会用新版本替换整个 `Squirrel.app`，Beam 插件随之消失，方案退回普通雾凇。
请不要在鼠须管菜单里升级；如已升级，等待支持该版本的 Beam，或重新安装鼠须管 1.1.2 后再运行 Beam 安装包。

## 卸载

```bash
sudo "/Library/Application Support/Beam/bin/beamctl" uninstall
```

卸载会停止服务、恢复 Beam 写入且未被你修改过的 Rime 文件、去掉方案列表中的 `beam_ice`，
用备份恢复原版签名的鼠须管，并删除 `/Library/Application Support/Beam`。鼠须管本身、用户词库和
`~/Library/Application Support/beam-ime` 中的模型会保留，可手动删除。
