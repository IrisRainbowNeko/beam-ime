# 测试记录

版本：0.1.0-beta.1。测试随发行工程持续补充，下面区分实际运行与尚未覆盖的环境。

## 本机自动验证

- C++ 核心：按键规范化、UTF-8、混输对齐、协议错误类型、服务不可用兜底。
- Python：模型文件校验与原子替换、配置保留、文件安装升级与回滚、prompt 契约。
- 真实模型 CPU 集成：错误请求后服务继续运行，生成候选，上下文切换。
- PowerShell：重复安装、恢复原文件、保留用户修改、复制失败回滚、安装脚本语法。
- Arch 构建：独立 C++ daemon、动态 CPU / Vulkan 后端及 librime 插件。
- 隔离 Rime 用户目录：方案部署、候选、上屏，以及停止 daemon 后的雾凇输入。
- Windows Wine：中文与空格程序/模型路径，不提供 Vulkan DLL 的 CPU 启动与生成。
- Windows Unicode GUI 宿主：合并 Rime DLL 的日志初始化，覆盖 glog 的 `__argv` 修复。
- GitHub Actions：Ubuntu 24.04 完整编译、CTest 和 `.deb` 生成；Windows runner 的安装恢复测试。

真实模型用本次发布的 GGUF，使用隔离 socket，不替换桌面正在运行的输入法。
这些检查验证工程行为，不是大规模准确率评测。

## 示例测量

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
Windows 的交叉构建、PowerShell 测试和 Wine 检查不能等同于真实 Windows 11 桌面验证。
Beta 用户可按安装教程反馈真实桌面的候选、上屏和宿主兼容问题。
