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
