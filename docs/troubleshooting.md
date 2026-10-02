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
