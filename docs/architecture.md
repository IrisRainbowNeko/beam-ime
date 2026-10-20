# 架构和协议

Fcitx5-Rime / Weasel 调用 `beam_processor` 和 `beam_translator`。
插件通过本机 JSON-lines 连接 `beamd`；服务以 llama.cpp 生成整串输入的候选。
普通模式同时保留雾凇 translator，模型超时或不可用时仍能输入。
译英模式只展示完整的英文正文与中文注释；超时保留按键，切回普通模式即可使用雾凇。

输入 prompt 为可选的 `上文：...`、按空格分隔的完整按键和 `结果：`。
`keys_llm_v2` 在最前面添加 `任务：输入` 或 `任务：译英`，其余输入格式相同。
译英目标为 `See you tomorrow.\t明天见`，其中 `\t` 是实际制表符。
`keys_llm_v3` 和 `keys_llm_v4` 改为 `明天见\tSee you tomorrow.`，输入格式相同。
v4 对无法独立表达的片段输出 `中文\t<WAIT>`；标记使用现有词表，不上屏。
英文与中文注释来自同一条自回归生成序列，模型、CPU/Vulkan 后端和个人适配器共用。
增量 Top-1 复用 KV 前缀，并批量验证前一次输出作为草稿；随后在预算内完成 beam search。
中英文结构对齐检查不等价于逐字拼音硬约束，不能保证每个候选读音都与输入一致。
v4 译英另外按随包拼音资源检查中文读音覆盖，并排除英文正文中的汉字。

## 本机协议 v1

Linux 使用用户私有 Unix socket，默认 `$XDG_RUNTIME_DIR/beam-ime/beamd.sock`；
连接检查同一 UID。Windows 使用 loopback TCP，端口和每次启动生成的 token 写在
`%LOCALAPPDATA%\beam-ime\beamd.endpoint`，每个请求附 `token`。

```json
{"id":1,"op":"health"}
{"id":2,"op":"query","keys":"nihao","context":"","max":5,"beam_ms":100}
{"id":3,"op":"query","keys":"mingtianjian","mode":"translate_en","max":5,"beam_ms":100}
```

`id` 为非负整数。成功响应包含相同 id、`ok:true`，查询还包含 `candidates`、
`greedy_ms`、`beam_ms`、`beam_complete`、同序 `comments` 和 `mode`。
缺省模式为 `ime`，其注释为空字符串；译英正文在 `candidates`、中文在 `comments`，候选上屏只取正文。
译英响应另有 `translation_status: ready | need_more_input`。最高排名有效结果要求等待时，
返回空候选；Rime 显示“继续输入”并保留按键，空格不会提交原始拼音。
格式错误、超时和需要继续输入分别显示。`ready` 下的空数组表示没有有效候选。
GGUF 可通过 `beam.decoding_strategy` 选择 `beam`、`greedy_first` 或 `field`；后两项仅影响译英，
由固定公开验证集选定。字段评分复用本次搜索的 token 概率，不增加第二次语义推理。
译英 adapter 还可选 `diverse`：beam 只搜到中文字段末尾，保留多个不同的中文读法，再批量 greedy 写各自的英文，
不足时用普通 beam 的英文补位；读法检查允许一处手误（`Pronunciations::spells`）。当前发布的译英 adapter 使用 `diverse`。
health 包含 `version`、`protocol`、`model`、`backend`、`prompt_version` 和 `modes`。
GGUF 的 `beam.prompt_version` 决定任务能力；缺失时按 `keys_llm_v1` 普通输入处理。
旧模型的译英请求返回 `unsupported_mode`，未知模式返回 `invalid_mode`。
失败响应为 `ok:false` 和 `error`，如 `bad_request`、`bad_token`、`invalid_keys`、`model_error`。

每行最多 64 KiB；最多 40 个按键字母，上下文传输上限 4096 字节，模型实际只取最近 64 字。
服务串行处理，客户端同批提交多条查询时只回答最新查询。插件在 400 ms 内未收到结果即断开，
避免旧结果错配。默认不记录文本；`--debug-text` 显式启用含输入文本的调试日志。
模式进入前端缓存及组词通知，切换后取消未上屏选词并用原始按键重建候选。
引擎切换模式时清除 KV、首 token logits 与草稿；前端不异步修改已显示的菜单。
译英输出上限为 `min(192, 4 * 按键字母数 + 32)`，结构不完整或达到上限仍未结束的首选不展示。

## 模型 manifest v1

`models/default.json` 是模型版本的唯一发行信息，包含文件名、大小、SHA-256、下载地址、
基座、prompt 版本、`modes` 和量化。Linux 和 Windows 安装器均先写临时文件，完成校验后再替换目标。
自定义兼容模型可通过 Linux setup 的 `--model` 或手动调整 Windows 启动参数使用。
## 本地学习接口

整体流程见 [学习系统流程图](images/learning-system.drawio.png)，也提供 [可编辑版本](images/learning-system.drawio)。

JSON-lines 协议仍为 v1，`query.candidates` 保持字符串数组。新响应附带同序 `sources`
（`model` 或 `personal`）、`personalization_revision`、`learning_enabled` 和 `learning_paused`。
`health.learning` 与 `query.learning` 返回状态、样本计数、训练进度、组件安装状态与适配器元数据，不返回训练文本。

- `composition`：`session`、`composition`、`state`（`begin/activity/end/cancel`）。连接断开会结束其活跃组词。
  插件同时发送 `mode`，模式切换建立新的组词标识。
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
译英只按已有中文注释增加个人排序分数，再将顺序映射回完整双语候选；不插入未翻译中文词条。
译英上屏仅发送组词结束及活动通知，不把英文正文或生成的中文注释写入中文学习样本。
