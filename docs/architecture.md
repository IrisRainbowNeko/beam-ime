# 架构和协议

Fcitx5-Rime / Weasel 调用 `beam_processor` 和 `beam_translator`。
插件通过本机 JSON-lines 连接 `beamd`；服务以 llama.cpp 生成整串输入的候选。
Rime 同时保留雾凇 translator，模型超时或不可用时仍能输入。

输入 prompt 为可选的 `上文：...`、按空格分隔的完整按键和 `结果：`。
增量 Top-1 复用 KV 前缀，并批量验证前一次输出作为草稿；随后在预算内完成 beam search。
中英文结构对齐检查不等价于逐字拼音硬约束，不能保证每个候选读音都与输入一致。

## 本机协议 v1

Linux 使用用户私有 Unix socket，默认 `$XDG_RUNTIME_DIR/beam-ime/beamd.sock`；
连接检查同一 UID。Windows 使用 loopback TCP，端口和每次启动生成的 token 写在
`%LOCALAPPDATA%\beam-ime\beamd.endpoint`，每个请求附 `token`。

```json
{"id":1,"op":"health"}
{"id":2,"op":"query","keys":"nihao","context":"","max":5,"beam_ms":100}
```

`id` 为非负整数。成功响应包含相同 id、`ok:true`，查询还包含 `candidates`、
`greedy_ms`、`beam_ms`、`beam_complete`。health 包含 `version`、`protocol`、`model` 和 `backend`。
失败响应为 `ok:false` 和 `error`，如 `bad_request`、`bad_token`、`invalid_keys`、`model_error`。

每行最多 64 KiB；最多 40 个按键字母，上下文传输上限 4096 字节，模型实际只取最近 64 字。
服务串行处理，客户端同批提交多条查询时只回答最新查询。插件在 400 ms 内未收到结果即断开，
避免旧结果错配。默认不记录文本；`--debug-text` 显式启用含输入文本的调试日志。

## 模型 manifest v1

`models/default.json` 是模型版本的唯一发行信息，包含文件名、大小、SHA-256、下载地址、
基座、prompt 版本和量化。Linux 和 Windows 安装器均先写临时文件，完成校验后再替换目标。
自定义兼容模型可通过 Linux setup 的 `--model` 或手动调整 Windows 启动参数使用。
## 本地学习接口

整体流程见 [学习系统流程图](images/learning-system.drawio.png)，也提供 [可编辑版本](images/learning-system.drawio)。

JSON-lines 协议仍为 v1，`query.candidates` 保持字符串数组。新响应附带同序 `sources`
（`model` 或 `personal`）、`personalization_revision`、`learning_enabled` 和 `learning_paused`。
`health.learning` 与 `query.learning` 返回状态、样本计数、训练进度、组件安装状态与适配器元数据，不返回训练文本。

- `composition`：`session`、`composition`、`state`（`begin/activity/end/cancel`）。连接断开会结束其活跃组词。
- `feedback`：`session`、`composition`、`event_id`、`keys`、`text`，可附 `context`、`first` 和 `parts:[{keys,text,first}]`。
  只在实际提交时发送。事件 ID 去重，关闭或暂停时不采集，取消的片段不产生反馈。
- `learning`：`action` 为 `enable/disable/pause/resume/status/train/rollback/reset/install/setup`；
  `reset` 需要 `confirm:true`，`install` 提供本机已安装组件的 `manifest` 路径。
  `setup` 需要 `confirm:true`，后台调用用户态安装器下载固定版本组件并自动开启；
  `installation.state` 为 `not_installed/installing/ready/error`。重复请求合并，关闭、暂停或清空会取消安装后的自动开启。

上屏反馈与查询共用后台顺序连接，UI 线程不做网络写入或数据库操作。daemon 是 SQLite 唯一写入者。
训练进程读取固定任务快照，使用文件发布进度和产物；输入活动请求 microbatch 边界暂停。
适配器仅在活跃组词全部结束后切换，并清空 KV、推测草稿和前端组词缓存。
每个基座保存当前及上一套适配器和优化器状态，模型升级按基座指纹隔离。

个人词库的 SQLite 记录在启动时建立音节前缀索引，查询只匹配相关拼音路径；原始按键有独立的精确索引。
候选保持非负原序分数，叠加频次、近期性和上下文相似度，同码纠正仅作用于对应按键。
最终过滤器保留原有 Rime 候选对象与片段范围，首屏为个人词和普通词留位，后续候选继续翻页。
