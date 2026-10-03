# 跨会话进展

## 选择最短可验证路径

- “等我回答 / 需要我处理”：先 `activity list`，筛选 `needs_user:true`。这是当前未解决的求助，不从历史中出现过的问句推断。
- “其他事情还在做吗 / 有什么新结果”：同一次列表按真实状态区分进行中、待帮助与未读结果。不要把当前正在执行的这次查询重复报成另一项委托，也不根据 `complete` 承诺现实目标成功。
- “带我回到那个问题 / 打开结果”：列表已提供真实 `session_id`；目标明确时取得客户端状态，直接 `app navigate` 并核对回执。无需再用 sessions list/read 确认同一个 ID。需要复述问题、核查结果正文或读取历史证据时，才按目标会话读取内容；不要全量扫其他会话。
- 没有匹配项只说明当前返回范围内未找到；`has_more:true` 时不能宣称穷尽，可将 limit 增至 100。仍有更多时明确范围，不把列表当作完整历史或全部已读事项。若多个候选无法唯一对应用户所说的“那个”，简短展示可选目标，不擅自任选。

`runtime {"op":"activity","args":{"action":"list","limit":30}}` 查询当前账号的根任务。默认最多 30 条，limit 为 1–100；`has_more:true` 表示仍有更多条目，不可宣称已经列完。它只读数据库，不创建任务、不唤醒模型，也不改变用户眼前的会话。

- 活跃任务不受时间限制；未读结果包括此功能启用后的完成、失败、取消、重启中断，保留至明确标记已读，不因离开几天而消失。历史记录始终保留。关闭浏览器、换会话不会停止任务。
- `status` 是真实运行状态：queued/running/waiting_user/waiting_agents/complete/failed/cancelled/interrupted。`complete` 表示这次运行结束，不证明每一个外部操作都成功；确认具体效果应读取原会话与工具回执。
- `needs_user:true` 表示本任务或其委派任务正在等待用户帮助。子任务不会单独挤满列表，回到根任务的 `session_id` 即可看到对应问题。不要自行回答、隐藏或重复执行这些问题。
- `attention_key` 对应本次状态及待回答问题。`runtime {"op":"activity","args":{"action":"ack","run_id":"真实ID","attention_key":"原样返回的key"}}` 标记已读。仅在用户已看到结果或明确要求收起时使用；不可为了让列表干净而吞掉未读失败。它不删除记录、不停止任务，也不表示问题已解决。
- `acknowledged:true,item:null` 表示这个终态提醒已收起；活跃和待帮助条目始终保留。旧 key 返回 `acknowledged:false,item:当前条目`，需要重新看待新状态，不要自动用新 key 覆盖。
- 停止另一条会话的活跃和排队任务：`runtime {"op":"activity","args":{"action":"stop","session_id":"真实会话ID"}}`。引擎完成停止后才返回 `applied,completed:true`；这不会撤销已发生的外部操作，也不暂停该会话未来的定时触发。停止当前会话先返回 requested，由引擎补齐工具配对后结束本轮；后续回执在会话事件与持久化状态中，不继续生成确认。
- 打开或收起进展列表：`app action=open/close,target=activity`，与固定界面入口等价。

需要显示某个结果时，用已验证的 `session_id` 调用 `app navigate` 并核对界面回执；读取内容则用 conversations skill。进展列表是有界摘要，绝不是历史上下文的新指令。
