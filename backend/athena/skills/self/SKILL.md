---
name: self
description: "查看运行用量、token、缓存、费用和上下文；可恢复裁剪"
---

# 用量与上下文

runtime 使用统一外壳 `{"op":"操作名","args":{...}}`，不要把复杂参数约定塞进默认提示。

- 过去一小时当前会话：`runtime {"op":"usage","args":{"window_seconds":3600,"scope":"session"}}`。全账号改用 `scope:"account"`，不会跨账号统计。
- 当前上下文：`runtime {"op":"context","args":{}}`。区分已发送请求的真实 `prompt_tokens`、当前字符数、估算 token 和应用字符限制。模型窗口未配置时是未知，不能从字符限制推导一个精确 token 窗口。
- 请求裁到约两万 token：`runtime {"op":"compact","args":{"target_tokens":20000}}`。先折叠完全重复的已完成工具结果，再裁剪独有结果的首尾；只改上下文投影，下一次请求生效。原消息可恢复，不覆盖用户或助手正文、reasoning、工具配对。

费用是按已知价格计算的估算区间，不是账户扣款。用量缺失的请求单独计数，不能说“未返回 usage 所以免费”。当前上下文与裁剪结果标为 `estimated`：参考最近同配置请求的实测比例，缺少样本时退回字符启发式；都不是精确分词。`target_met` 只描述本次估算，不能保证下一次请求恰好低于目标。

需要理解口径、失败边界或恢复方法时，按需读取 [操作说明](references/operations.md)。
