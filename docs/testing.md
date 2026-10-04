# 测试记录

版本：0.1.0-beta.2。测试随发行工程持续补充，下面区分实际运行与尚未覆盖的环境。

## 本机自动验证

- C++ 核心：按键规范化、UTF-8、混输对齐、协议错误类型、服务不可用兜底。
- Python：模型文件校验与原子替换、配置保留、文件安装升级与回滚、prompt 契约。
- 真实模型 CPU 集成：错误请求后服务继续运行，生成候选，上下文切换。
- PowerShell 7 与 Windows PowerShell 5.1：重复安装、恢复原文件、中文/空格/方括号路径、保留用户修改、复制失败回滚、安装脚本语法。
- Windows 安装脚本：官方小狼毫的空版本文本、数值版本和架构检查、空配置初始化、失败安装卸载，以及宿主改变时保留恢复备份。
- Arch 构建：独立 C++ daemon、动态 CPU / Vulkan 后端及 librime 插件。
- Fedora 44 隔离环境：原生构建、RPM 安装、`lib64` 下的 CPU daemon、真实模型回归，以及安装后 Rime 插件的候选、上屏和停服务后的词典兜底。
- 隔离 Rime 用户目录：方案部署、候选、上屏，以及停止 daemon 后的雾凇输入。
- Windows Wine：中文与空格程序/模型路径，不提供 Vulkan DLL 的 CPU 启动与生成。
- Windows Unicode GUI 宿主：合并 Rime DLL 的日志初始化，覆盖 glog 的 `__argv` 修复。
- GitHub Actions：Ubuntu 24.04 完整编译、CTest 和 `.deb` 生成；Windows runner 的安装恢复测试。

## Windows 11 安装修复验证

在 Windows 11 Pro x86_64 虚拟机（系统版本 10.0.26100，4 vCPU / 4 GiB）中，
使用独立磁盘覆盖层和测试用户，开启 UAC 后完成：

- 复现已发布 Beta 2 的安装失败、卸载失败；官方小狼毫的文本版本为空，数值版本为 0.17.4.0。
- 修复脚本完成失败安装的卸载，未写入小狼毫时不请求系统恢复。
- 修复后的离线 EXE 在中文与空格目录安装、重复安装，兼容首次安装的空 `default.custom.yaml`。
- 保留中文与空格路径的自定义模型和 CPU 设置，安装后诊断确认模型已由 CPU 后端加载。
- 卸载后原 `rime.dll` 的 SHA-256 与安装前一致，保留模型、用户词库和安装后修改，移除 Beam 进程及卸载登记。

模型服务直接请求 `nhsj` 得到 `你换手机`；单次 CPU 生成约 1.7 秒。
该虚拟机上默认 400 ms 的 Rime 等待预算触发了词典兜底，已验证其候选和上屏。
这次验证覆盖安装链路，不作为真实 GPU 性能或所有桌面应用输入体验的结论。

真实模型用本次发布的 GGUF，使用隔离 socket，不替换桌面正在运行的输入法。
这些检查验证工程行为，不是大规模准确率评测。

## 首发版本示例测量

2026-10-02，AMD Ryzen 7 8845H，Arch Linux，CPU 后端，8 推理线程，Q8_0 发布模型。
无上下文、每项一次调用；构建任务同时运行，数值仅用于说明量级，不是稳定性或准确率基准。

| 按键 | 首候选 | 贪心生成 | 附加搜索 |
|---|---|---:|---:|
| `nihao` | 你好 | 98 ms | 107 ms |
| `nhsj` | 你换手机 | 155 ms | 119 ms |
| `yonglinux` | 用Linux | 179 ms | 111 ms |

复跑隔离验证：

```sh
cmake --build build/release --target rime_smoke
python tools/smoke_rime.py --model models/beam-0.6b-q8_0.gguf
python tools/smoke_windows.py --model models/beam-0.6b-q8_0.gguf
```

前者需要 bubblewrap 和系统 Rime 数据，后者需要 Wine 及已交叉编译的 Windows 文件。

## 平台边界

Ubuntu 24.04 的编译和包生成由 GitHub Actions 在对应系统完成。
Fedora 44 的 RPM、CPU 推理和 Rime 测试在隔离环境中完成，尚未验证真实 GNOME / Plasma 桌面的交互流程。
Windows 的安装及恢复已在 Windows 11 虚拟机验证；GPU 驱动和日常桌面应用的完整输入流程仍需对应环境验证。
Beta 用户可按安装教程反馈真实桌面的候选、上屏和宿主兼容问题。
