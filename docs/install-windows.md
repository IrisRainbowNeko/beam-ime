# Windows 11 安装

下载 `Beam-0.1.0-beta.1-windows-x64-setup.exe` 或 `-offline.exe`。
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
