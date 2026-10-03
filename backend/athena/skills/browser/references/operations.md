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
| handoff | 仅 status.manual_handoff=true 时可用；tab_id、questions、可选 reason；questions 与 collaboration 技能一致，持久化等待人工在执行节点的浏览器窗口登录或完成步骤 |

例如：

```json
{"op":"browser","args":{"action":"open","url":"https://example.com"}}
```

返回 `ok:true` 及观察：tab_id、observation_id、url、text、artifact_id。text 中 `[ref=e2]` 对应请求的 `ref:"@e2"`。所有 ref 都仅对当前观察有效；切换会话页面、导航或其他操作后使用新观察。原始观察可用 runtime read_artifact 分页恢复；恢复旧文本不能使旧 ref 重新有效。大页面优先针对元素观察，避免不停获取相同长页面。

每个外部动作先持久化操作编号，再执行一次。失败可能返回 `ok:false,effect_known:false`，表示动作可能已生效，禁止自动重发，尤其是付款、发送、订单和提交表单。先 snapshot 检查确认页/记录；无法判断则说明并向用户求助。恢复服务不会自动重播这类操作。停止会尝试关闭操作中的标签页，但已发生的外部效果不可撤销；停止是否实际完成也需要核实。

关闭服务或浏览器后，标签页记录会变为 closed；需显式 open 创建新页，登录态仍可复用。fork 只继承聊天，不接管父会话标签页或重复浏览器动作。同一账号的浏览器操作串行，各账号使用不同 profile。浏览器在能力查询所示的执行节点上工作，不一定在用户当前设备上；关闭网页视图不等于停止执行。本机执行时电脑睡眠不能保证网页任务继续。

只需要普通表单字段时可以 fill。先读 status.manual_handoff 或 capabilities.browser_manual_handoff；仅为 true 时，handoff 才能把执行节点的有界面浏览器交给用户。此时可询问 `[{"id":"login","question":"请在 Athena 管理的浏览器窗口里登录，完成后回复我。"}]`，再用 snapshot 核实。

云端及无界面浏览器目前没有远程接管入口；handoff 会返回 manual_browser_handoff_unavailable，不会打开用户本机浏览器，也不会创建无法完成的登录等待。可以单独用 runtime user_help 询问用户愿意提供的非凭据文字或文件，例如请用户在自己的浏览器下载账单后上传，或改用公开数据。用户在自己的浏览器登录不会同步云端浏览器的登录态。不得索要密码、验证码、cookie、会话令牌，也不得声称已替用户打开本机窗口。

网站脚本弹窗不会自动接受或取消。当前不提供文件上传、自动弹窗归属或视觉点击；有可接管的本机窗口才可请用户在其中操作，否则说明限制并寻找可完成任务的替代路径。
