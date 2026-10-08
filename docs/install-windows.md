# Windows 11 安装

下载 `Beam-0.2.1-windows-x64-setup.exe` 或 `-offline.exe`。
轻量版下载模型；离线版已经包含模型和小狼毫安装器。

1. 运行安装器。Beam 安装在当前用户的应用目录。
2. 没有小狼毫时，安装器安装官方 0.17.4；已有版本必须是兼容的 64 位 0.17.4。
3. 系统文件操作会出现管理员权限提示。Beam 服务本身按当前用户运行。
4. 等待方案部署完成，在小狼毫菜单选择「Beam 雾凇」。

本 Beta 安装器未做 Authenticode 签名。Release 同时提供 SHA256SUMS；可以使用
`Get-FileHash .\Beam-...exe -Algorithm SHA256` 核对文件。不要关闭系统防护来安装。

## 配置与排错

模型和日志位于 `%LOCALAPPDATA%\beam-ime`；Rime 配置通常位于 `%APPDATA%\Rime`，
也支持小狼毫注册表中指定的用户目录。

安装过程日志位于所选 Beam 安装目录的 `setup-Install.log`，卸载日志为
`setup-Uninstall.log`。管理员步骤分别记录在 `setup-SystemInstall.log` 和
`setup-SystemRestore.log`；失败弹窗会显示日志位置。

早期 Beta 2 如果提示 `Beam requires Weasel 0.17.4.0; found .`，原因是安装器读取了
官方小狼毫中为空的版本文本。修复包改用数值版本，兼容小狼毫首次安装产生的空配置文件。
运行修复包并选择原来的 Beam 安装目录即可重试，已有模型会复用；完成后也可正常卸载。

诊断：在 PowerShell 中执行：

```powershell
& "$env:LOCALAPPDATA\Programs\BeamIME\beam-setup.ps1" -Action Doctor
```

日志中的 `restarting on the CPU` 表示 GPU 推理失败后已改用 CPU。
无 GPU 驱动时 CPU 后端仍可加载；CPU 下可把 `beam/beam_ms` 设为 0，减少候选等待。

使用已有兼容 GGUF，或强制 CPU（升级时保留此选择）：

```powershell
& "$env:LOCALAPPDATA\Programs\BeamIME\beam-setup.ps1" -Action Install -Model 'D:\Models\custom.gguf' -Cpu
```

不更换模型时省略 `-Model`。旧版 `%LOCALAPPDATA%\beam-ime\models\beam.gguf` 也会自动沿用。

## 升级和卸载

运行新版安装器升级，已下载的相同模型会复用。不要手动混用不同小狼毫版本的 DLL。
小狼毫升级后，先安装与其兼容的 Beam 版本。

在 Windows 的应用列表卸载 Beam。卸载恢复原 DLL 和未修改的原资源，保留小狼毫、
用户词库和模型。若恢复失败，卸载器及备份会保留，错误信息指出具体步骤。
DLL 备份在小狼毫目录的 `beam-backup`，用户资源备份在 `%LOCALAPPDATA%\beam-ime\files`。
## 本地个性化学习

打开 Rime 方案菜单，点击“下载并开启模型学习”，即可确认下载 Windows Vulkan 组件（约 34 MiB）。
下载和校验在后台完成，安装成功后自动开启个性化；已有兼容组件时直接开启。
不需要管理员权限，也不需要选择文件或配置 Python。期间关闭或暂停学习，安装完成后不会重新开启。
安装失败可从菜单再次点击重试，具体原因见 `Learning Status`。首次下载需要联网。

只需要即时词库时，从 Rime 菜单打开“学习开启”，或运行开始菜单的 `Beam / Enable Learning`。
开始菜单的 `Pause Learning` 停止新增记录与训练，`Learning Status` 显示状态。
也可以在安装目录的终端使用以下命令：

```powershell
.\beamctl.cmd learn enable
.\beamctl.cmd learn install -Download
# 离线导入同一组资产：
.\beamctl.cmd learn install -Manifest '.\beam-learning-0.2.0-windows-x86_64-vulkan.json' -Source '.'
.\beamctl.cmd learn status
.\beamctl.cmd learn train
.\beamctl.cmd learn pause
.\beamctl.cmd learn resume
.\beamctl.cmd learn disable
.\beamctl.cmd learn rollback
.\beamctl.cmd learn reset -Yes
```

manifest 使用下载资产中的实际文件名。QVAC 原生学习组件按当前用户安装，不需要管理员权限。
复用已安装的 Q8 GGUF，通过 Vulkan 显卡驱动训练，无需 Python、PyTorch 或 CUDA runtime。
离线安装需将 manifest 和一份 ZIP 放在 `-Source` 指定目录。建议训练时至少有 4 GiB 可用内存。
默认累计 64 条新上屏记录、无 Beam 输入 5 分钟、接通电源后训练，成功任务间隔至少 4 小时。
暂停保留已学结果，关闭会停用个性化；清空需要 `-Yes` 确认。卸载保留 `%LOCALAPPDATA%\beam-ime\learning`。

没有可用 Vulkan 驱动时，训练状态显示错误，已学词库仍可使用；普通 CPU 推理不受影响。
从旧版 Python 学习组件升级时，安装新的 Vulkan manifest。已有适配器可继续使用，下一轮建立原生优化器状态。
