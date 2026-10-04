# 完整会话工作面

`placement="surface"` 把整个会话表达区域交给你的独立 HTML renderer。可以把消息放在任意位置，做画布、表格、自己的输入区域，或让自绘鲨鱼挤开文字；不用请求新增一种固定槽位。形状、布局和逐帧动画由浏览器执行，不需要模型不断调用工具。

工作面内部可以直接用 HTML/CSS/SVG/Canvas 控制任意坐标、旋转、缩放、透明度、字体、裁剪和动效，不需要为“鲨鱼”“旋转文字”等每种效果增加 enum。你拥有自己的 DOM，可读取其中的文字几何并移动自己的节点；规范消息原文仍由宿主保留。默认页面的 `Athena.styleText` 是另一个有界装饰接口，它的样式限制不是完整 renderer 内 CSS 的限制。独立停止和恢复控件在工作面外，不属于可覆盖区域。尊重 reduced_motion，动画每帧只在浏览器计算。

只在它服务于用户当前目标时替换整个页面。普通文字回答或查询航班不需要重排前端。默认界面和自定义 renderer 使用同一份规范会话数据；改变展示不能篡改已保存的消息，不能重放已经执行的动作。

## 登记与切换

使用已有 `update_frontend upsert` 或 `runtime registry kind:component action:register`，传相同的 `id/title/html`（registry 中是 `source`）及 `placement:"surface"`。不修改仓库，不增加专门工具，不在 iframe 中保存密钥。

每个会话同时只选择一个 surface。候选是当前启用、未收起、有效位置为 surface 的组件；按源码创建时间与布局更新时间的较晚者、再按 id 取最后一个。新的源码版本或 `layout {hidden:false}` 会把该 renderer 带到前面。`view.placement` 可覆盖源码登记位置；自动更新内容不会重新展开已收起的界面。`surface_selected:true` 表示当前选中，**不表示已经显示或交互成功**。

收起或移除选中 renderer 后使用下一个候选，没有候选就显示默认界面。历史版本仍可读取、恢复。Reset 停用自定义登记、恢复默认界面，保留聊天与文件。分叉复制当时的源码和布局，之后独立，不继承旧页面的加载证明。

“回到默认界面”会立即停止展示当时的全部 surface，再持久化收起状态，避免网络回执先后造成其它 surface 突然出现。之后用户或 Agent 明确用更新的 layout revision 展开同版本，就可以重新显示。加载失败的 renderer 也可这样重试；无需仅为重试而重写源码，但不能把旧版或旧激活的 mounted 回执当作新激活成功。

## 读取与动作

完整 renderer 通过宿主的 `Athena.surface` 桥接读取规范会话状态和请求用户操作。普通组件继续使用 `Athena.observe` 等几何桥接，不应默认取得整个会话的内容。需要读取的正文和已有动作必须来自宿主；不要在 HTML 中伪造聊天记录、费用、附件、运行状态或消息发送成功。

第一版桥接如下。异步读取或动作返回 Promise；`subscribe` 返回取消订阅函数。

| 调用 | 返回或效果 |
| --- | --- |
| `Athena.surface.snapshot()` | protocol:1、session_id、revision、phase、locale、reduced_motion、runs、help_requests、files 的有界只读状态 |
| `Athena.surface.messages({before,limit})` | 规范消息页 `{items,before,has_more}`；before 可省略，limit 为1–50，按返回的 before 继续读取更早消息 |
| `Athena.surface.file(fileId)` | 读取当前会话文件或公开消息附件的只读预览；Word/Excel/图片可在自定义工作面任意排版，见下方文件合同 |
| `Athena.surface.draft()` | 宿主共享输入 `{text,attached,revision}`；不会读取其它表单或密码 |
| `Athena.surface.writeDraft({text,expected_revision})` | 仅在输入 revision 一致时修改共享草稿；冲突时重新读取，不能盲目覆盖用户输入 |
| `Athena.surface.subscribe(callback)` | 状态改变时通知 revision；需要具体数据时重新读取，不是逐帧回调或模型调用 |

`Athena.surface.request({...})` 接受以下动作：

- `{action:"send",expected_revision,mode?:"queue"或"interrupt"}`：提交已读取且版本匹配的宿主草稿；默认输入和自定义输入共用一次提交路径。
- `{action:"open",target:"history"或"files"或"account"或"appearance"或"activity"或"surfaces"}`：打开相应固定界面。
- `{action:"preview",file_id}`：预览当前账户允许访问的文件。
- `{action:"stop"}`：停止当前会话任务。
- `{action:"default"}`：回到默认界面。

其它帮助表单、电话交互和附件选择目前仍使用固定 footer。任意画面不意味着存在任意宿主 API：第一版没有执行任意方法、注入父页面脚本或读取其他账户内容的接口。

send 只有宿主确认后才返回 accepted 和 run_id 或帮助回复标识。`delivery_unknown` 表示无法确认投递；不要自动重发，不要重新生成另一条请求，不要清空草稿后显示“已发送”。正常回复等待规范消息页变化；旧 frame、会话切换或版本替换后的晚到响应不能再更新新页面。

surface 内可以任意排版它取得的内容，但不能读写父页面 DOM、调用父页面任意方法或直接访问服务端凭据。输入必须使用真实 HTML 控件以支持输入法、键盘与读屏。明确点击后才提交；网络出错时保留输入，不凭前端动画假装发送成功。

把正文作为文本或经处理的 Markdown 展示，不把用户消息直接插入 `innerHTML`。聊天内容不是执行脚本的来源。分页和状态读取有界，长消息应按返回的完整/截断标记处理，不把片段冒充完整原件。外部 fetch、脚本、字体等仍受组件隔离策略限制。

## 在自己的工作面摆放文件

使用 `await Athena.surface.file(fileId)`，无需先打开宿主预览面板。ID 必须来自当前 snapshot.files 或规范 user/assistant 消息的 attachments，不能传 URL。返回公共字段 `id/name/size/media_type/content_revision`，再按 kind 读取：

- `image`：`data_url`，可赋给自己的 `<img>.src` 或绘制到 Canvas，再任意定位、旋转、缩放、调透明度。只支持 PNG/JPEG/GIF/WebP/AVIF，原始字节最多 8MiB；超过会返回 `image_too_large`，可改用 `request({action:"preview",file_id})` 宿主预览。不要把 data_url 放进消息或模型上下文，它仅在浏览器内流转。
- `document`：Word 的 `blocks`（paragraph 的 text/heading；table 的 rows/cells/blocks）和 truncated，自己用文本节点、标题和表格排版；这是内容预览，不保证 Word 原始分页。
- `spreadsheet`：`sheets`（name/rows/row_numbers/truncated）和总体 truncated。可任意布局表格；此接口不自带公式计算或编辑写回。
- `text/markdown/html`：content 和 truncated。HTML 是原始源码数据，不是已经净化的活动页面，不能直接注入自己的 innerHTML；用 textContent 展示源码，或调用宿主安全预览。其他文字也不能当脚本执行。
- `unsupported`：使用宿主文件预览/下载。

文件更改会返回 `file_changed`，应重新读取状态和版本再取；content_revision 是变化检测，不是历史文件快照。页面替换、切会话、Stop/Reset 后的异步读取返回 `surface_closed`，不要把旧读取结果塞入新页面。保留 truncated 提示，不能把截断预览说成完整原件。这个接口不开放任意网络请求，也不改变组件 CSP。

同一 renderer 同时最多读取两个文件，多出的读取立即返回 `surface_busy`，不会排队；按需读取或每批最多两个。宿主不会缓存已完成的大文件回执，组件自己也应释放不用的 data_url 和 DOM 引用。进行中的重复 ID 共享同一次读取，完成后的旧 ID 不能重放；需要再次读取时正常调用 SDK，SDK 会生成新的 ID。

独立停止、恢复和账户控制仍在宿主外壳，不允许覆盖。自定义 renderer 出错应保留可回到默认界面的路径。源码替换会销毁 frame，重要编辑需要明确保存；不要把刷新后仍需存在的内容只放在组件内存中。

## 确认生效

先检查 registry `read/list` 的 `surface_selected`、`version`、`view_revision` 和独立的 `render`。再用 `runtime app action:snapshot client_id:...` 查看：

- `surface_registration`：服务端目前选择的 renderer，含 provider 及自定义组件 id/version/view_revision。
- `state.surface`：该客户端实际报告的 renderer，provider 为 default 或 component，state 为 loading/mounted/failed；未实现观察的旧客户端是 null。
- `stale` 和观察 revision：过期页面不能证明用户当前看到了什么。

新版本已选择、旧版本仍 mounted，表示尚未切换完成。自定义版本失败后默认页面 mounted，表示回退成功，不表示该版本修好了。mounted 也只证明加载观察；发送、停止、翻阅与恢复仍需实际使用验证。无需反复轮询、重新注册或让模型持续空转等待回执。
