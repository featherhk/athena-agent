# 统计与裁剪口径

`usage` 按请求开始时间选择指定窗口内的请求，统计供应商实际返回的输入、输出和缓存 token。窗口内尚未结束的请求可能缺少 usage；这是未知用量，不是零用量。返回的 `unreported_requests`、`cache_unreported_requests` 和 `unpriced_requests` 分别说明缺少哪种信息。账户统计根据当前会话所属账号确定范围，不能指定另一个用户。

费用字段 `cost_usd_low` 至 `cost_usd_high` 是已知请求的估算区间。定价来源与核对日期在 `pricing` 中；未配置价格、使用不同供应商端点或 usage 不完整时，不补造价格。`last_context_from_request` 独立于统计时间窗口，指出当前会话最近有实测输入 token 的请求与时间；它不是此刻上下文的精确 token 数。

`context` 的 `raw_chars` / `clipped_chars` 统计已完成消息的内容、reasoning 和工具调用字段。`estimated_payload_chars` 还包含估算的请求包装、固定前缀和最近请求的工具 schema。字符上限是应用的保护阈值，不等于供应商 token 窗口。工具注册或前缀刷新会使下次请求大小改变。

`compact` 的 `target_tokens` 必须是 1000 至 1000000 的整数。它优先缩短较早的工具结果，可进一步缩短以前的裁剪。原消息和完整 artifact 不变；裁剪标记提供 `message_id`，用 `runtime {"op":"read_message","args":{"message_id":"原消息ID","offset":0,"limit":12000}}` 分页恢复。恢复原文用于检查，不自动重新执行原命令。

如果用户或助手正文、reasoning、工具调用、固定前缀或工具定义本身超过目标，只裁工具结果可能无法达到目标。返回 `target_met:false` 与剩余限制时，说明实际缩减了多少；不要宣布已精确压到两万 token。缺少工具 schema 的历史测量时，即使可见部分很小，也不会确认达到总目标。正在生成的内容和本次检查工具自身的结果不包含在估算内。
