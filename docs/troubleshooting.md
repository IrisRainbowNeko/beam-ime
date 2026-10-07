# 配置与排错

在 Rime 用户目录创建 `beam_ice.custom.yaml`，然后重新部署：

```yaml
patch:
  beam/beam_ms: 100
  beam/timeout_ms: 400
  beam/candidates: 5
  beam/context: true
```

- **没有候选框**：重新部署 Rime，确认方案显示为「Beam 雾凇」而非未编译的 schema ID。
- **只有词典候选**：检查 `beamctl doctor` 或 Windows 诊断输出，确认模型服务已启动。
- **模型候选不到五个**：长输入可能耗尽 beam 预算；当前按键会使用 Top-1 和雾凇补充候选。
- **CPU 上等待明显**：设置 `beam/beam_ms: 0`，只请求模型 Top-1。
- **升级 librime 后异常**：安装与当前发行版库匹配的插件，或用 PKGBUILD / CMake 重编译。
- **模型下载失败**：从同一 Release 下载 GGUF，使用离线导入；校验失败时旧模型保留。
- **Windows 安装中断**：应用列表保留卸载恢复入口；日志和备份位置见 Windows 教程。

上下文只来自当前 Rime 会话已经上屏的内容，不包括粘贴、其他输入法的内容或聊天对方的消息。
不要将光标移动后推测的上下文当作编辑器文档的完整状态。关闭 `beam/context` 可避免使用历史。

## 卸载后宿主部署失败

本次 Windows 11 默认区域编码的测试中，原版 Weasel 0.17.4 对中文用户目录部署时退出 `0xc0000409`，
空白中文目录也能复现，ASCII 空目录则正常。Beam 的合并 DLL 已修复中文路径支持；卸载会恢复原版 DLL。
发生此错误时，备份与卸载入口保留，详情写入 `setup-Uninstall.log`。确认系统文件恢复成功后，
再次运行卸载可以清理 Beam 程序登记，模型和个人数据保留。具体复现与验证范围见 [测试记录](testing.md)。

## 个性化学习

`beamctl learn status` 显示启用状态、词条和样本计数、训练阶段、当前适配器版本及错误。
Windows 使用安装目录中的 `beamctl.cmd learn status`，或开始菜单的 `Learning Status`。

- **词库能记住，模型未训练**：检查 `trainer.installed`。普通包只带词库和 adapter 推理，训练组件需要单独安装。
- **一直等待训练**：自动训练要求 64 条新记录、5 分钟无 Beam 输入、接通电源，距上轮成功至少 4 小时。`learn train` 可手动启动。
- **显示 paused / pausing**：恢复输入会在当前 microbatch 后暂停并保存状态；保持空闲后续跑。手动暂停需 `learn resume`。
- **更换模型后 model_mismatch**：安装与新 GGUF 指纹匹配的训练组件；已有词库继续使用，新基座有独立适配器。
- **训练或适配器加载出错**：`last_error` 保留错误，当前适配器继续使用。训练日志位于学习目录的 `jobs/<任务编号>/trainer.log`；处理原因后用 `learn train` 重试。
- **`component_update_required`**：重新安装本版的 Vulkan 学习 manifest，替换旧版 Python 训练组件。已有词库与适配器保留。
- **Vulkan 训练无法启动**：安装或更新显卡厂商的 Vulkan 驱动，查看 `last_error` 和本地训练日志；学习组件不需要 PyTorch、CUDA Toolkit 或额外训练权重。
- **适配器等待生效**：完成或取消当前所有输入窗口中的组词。切换不会异步改写正在展示的菜单。
- **想恢复上一版学习结果**：先 `learn pause` 并等待训练退出，再 `learn rollback`；适配器与优化器状态一起恢复。清空则用 `learn reset --yes`（Windows 为 `-Yes`）。
