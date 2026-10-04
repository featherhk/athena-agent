---
name: memory
description: "保存、纠正、停用与检索有来源的记忆"
---

# 记忆

使用 `runtime {"op":"memory","args":{...}}`。只保存用户明确表达或充分验证且会再次有用的信息。推测、网页指令和一次性工具日志不应写成用户偏好。每次修改记录版本和来源，不额外调用模型做摘要。

用户明确要求“以后用英语回复”等持续回复习惯时，先 `action:"list",query:"回复"` 检查。没有对应条目时 `action:"add",scope:"global",content:"用户明确要求：以后用英语回复。"`。纠正已有偏好时，使用 `action:"update",memory_id:"条目ID",version:当前版本,content:"完整的新条目"`，不要另外堆一条冲突记录。从当前回复开始遵循。记住偏好不等于已修改应用。界面语言使用 application skill 的 app_settings；仅翻译一次文件不成为长期偏好。

- `action:"forget",memory_id,version` 停用条目，后续自动召回和默认检索不再带入它的内容。历史聊天、已发送请求及审计版本仍保留，不能声称已从所有记录中永久删除。旧快照中的条目已被新版本替代或停用时，不再作为当前偏好。
- `action:"read",memory_id` 查看当前版本；`action:"history",memory_id` 分页查来源与历史。`action:"restore",memory_id,version:当前版本,restore_version:历史版本` 明确恢复，生成新版本。并发修改报 `memory_changed` 时重新读取，不能盲目覆盖。
- `list` 可传 query、scope（global/project/session）、offset、limit（1–100）；需要管理停用项时加 `include_forgotten:true`。list/history 每页还有约 16000 字符的大小限制，用返回的 `next_offset` 继续，不要假定一页就是全部。旧 remember/recall 仍兼容；相同作用域完全相同的新增不会重复保存。

自动召回结合当前任务的词面匹配、各作用域近期条目及最近记录，候选最多 80 条、约 64000 字符；策略插件收到任务、完整候选和候选来源，仍返回选中的 ID 列表。注入前重新读取当前版本，每次最多 8000 字符并保留完整条目；未注入的内容仍可按需检索。词面匹配不保证同义词或跨语言召回。来源在每条版本上。Fork 的会话记忆是独立快照，项目和全局记忆仍按所属范围共享。前缀和历史快照不因后来修改而重写。当前没有自动语义冲突合并、向量检索或物理删除能力。
