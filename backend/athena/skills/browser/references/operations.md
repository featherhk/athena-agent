# 受管浏览器操作

所有请求使用 `{"op":"browser","args":{...}}`，以下字段放在 args 内。

| action | 字段与效果 |
| --- | --- |
| status | 能力状态，不启动浏览器 |
| list | 当前会话登记的标签页及 open/closed 状态，不自动重开关闭的页面 |
| history | 当前会话的操作记录、状态和错误码；可选 limit（1–100）、offset，按 next_offset 分页。关闭浏览器后仍可查询 |
| open | url：完整 http(s) 地址。省略 tab_id 新建页；提供当前 tab_id 在该页导航 |
| snapshot | tab_id；可选 ref（同时提供最新 observation_id）缩小观察范围 |
| click / check / uncheck | tab_id、observation_id、ref |
| fill | 上述三个字段加 text，替换普通输入框内容 |
| select | 上述三个字段加 value，选择原生下拉框值 |
| press | tab_id、observation_id、key，例如 Enter、Tab、Control+a |
| scroll | tab_id、observation_id、direction（up/down/left/right）、可选 pixels（1–3000） |
| back / forward / reload / close | tab_id、observation_id；close 关闭该页，保留登录态 |
| screenshot | tab_id；当前视口截图进入会话文件区，返回 file 对象 |
| download | tab_id、observation_id、ref、name；点击该下载元素，把不超过 20 MB 的文件放入会话文件区 |
| handoff | tab_id、questions、可选 reason；questions 与 collaboration 技能一致，持久化等待人工登录或完成步骤 |

例如：

```json
{"op":"browser","args":{"action":"open","url":"https://example.com"}}
```

返回 `ok:true` 及观察：tab_id、observation_id、url、text、artifact_id。text 中 `[ref=e2]` 对应请求的 `ref:"@e2"`。所有 ref 都仅对当前观察有效；切换会话页面、导航或其他操作后使用新观察。原始观察可用 runtime read_artifact 分页恢复；恢复旧文本不能使旧 ref 重新有效。大页面优先针对元素观察，避免不停获取相同长页面。

每个外部动作先持久化操作编号，再执行一次。失败可能返回 `ok:false,effect_known:false`，表示动作可能已生效，禁止自动重发，尤其是付款、发送、订单和提交表单。先 snapshot 检查确认页/记录；无法判断则说明并向用户求助。恢复服务不会自动重播这类操作。停止会尝试关闭操作中的标签页，但已发生的外部效果不可撤销；停止是否实际完成也需要核实。

关闭服务或浏览器后，标签页记录会变为 closed；需显式 open 创建新页，登录态仍可复用。fork 只继承聊天，不接管父会话标签页或重复浏览器动作。同一账号的浏览器操作串行，各账号使用不同 profile。浏览器与模型都在本机执行节点上工作，关闭网页视图不等于停止执行；电脑睡眠时不能保证网页任务继续。

只需要普通表单字段时可以 fill；需要登录则 handoff。例如 questions 为 `[{"id":"login","question":"请在刚打开的浏览器里登录，完成后回复我。"}]`。不要让用户把密码、验证码或 cookie 发进聊天。网站出现脚本弹窗时默认不会自动接受或取消，交给用户处理后再观察。当前不提供文件上传、自动弹窗归属或视觉点击；用人工协助完成这些步骤。
