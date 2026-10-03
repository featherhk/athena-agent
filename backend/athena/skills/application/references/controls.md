# 应用动作与确认

能通过按钮做的基础操作，也应通过运行时实际执行。`runtime {"op":"app","args":{"action":"list"}}` 返回当前动作目录及现有等价操作，不要凭记忆编造能力。

| 需求 | 操作 |
| --- | --- |
| 查看当前交互表面 | app `action: list` 列出 clients；`action: snapshot, client_id` 获取有界语义状态与 revision |
| 打开/关闭菜单、历史、文件、账户、外观、开发面板、密码表单、工作区或组件管理 | app `action: open/close, target: menu/history/files/account/appearance/developer/password/workspace/surfaces` |
| 切换到已有对话 | app `action: navigate, session_id` |
| 预览或下载已有文件 | app `action: preview/download, file_id` |
| 关闭文件预览 | app `action: close, target: preview` |
| 把当前会话文件放入输入附件 | app `action: attach, file_id` |
| 从输入附件移除文件，保留文件原件 | app `action: detach, file_id` |
| 请用户选择本地文件 | app `action: attach`；浏览器可能需要用户点选控件 |
| 退出当前接收页面的登录 | app `action: sign_out` |
| 停止当前会话 / 恢复默认并保留聊天文件 | app `action: stop/reset`，由引擎执行 |
| 新建、改名、fork | 使用已有 `runtime sessions`；新建/fork 后需要显示目标时再 app navigate |
| 改语言、字体、颜色、显示参数或开发者模式 | 使用已有 `runtime app_settings`，按当前 schema 更新 |
| 改账户资料或头像、暂停任务 | 使用已有 account / cancel_task |
| 收起/展开、移动、调整组件尺寸 | registry `kind:component, action:layout`；先读取 version/view_revision，详见 interface skill 的 references/bridge.md |

没有实际文件 ID 时，不要伪造下载地址。先在文件区产生输出，再用 app list/read（不带 id）发现当前会话文件，返回 `files` 中的真实 id；无需打开浏览器触发登记。切换页面后也可在原任务中用 `app list/read, session_id` 查询同账号目标会话的文件，默认 `scope:session`，结果以 `files_scope` / `files_session_id` 标明文件范围；使用 `scope:account` 列出账号全部已登记文件（与文件面板一致，不扫描所有会话目录），此时不能同时传 `session_id` 或 request `id`；请求回执仍按原任务会话列出，request `id` 不能同时搭配任何显式 `scope` 或 `session_id`。默认返回50条，可用 limit（1–200）与 offset 翻页。当前 download 是已有文件导出，不代表支持任意会话归档格式。

## 观察、选择客户端与核验

`app list` 返回每个客户端的 client_id、当前 session_id、foreground、revision、observed_at、expires_at 与 stale。`app snapshot` 按 client_id 返回页面、文件预览（loading/ready/failed）、工作区、组件位置和可见性、等待事项、固定错误码、是否有未发送改动；不读取草稿、密码、表单值、文件正文、DOM 或剪贴板。观察是客户端报告，不等于截图或真实内容正确性验证；过期状态不能作为当前界面事实。

视图动作可带 `client_id` 和 `expected_revision`。省略 client_id 只会选择当前会话唯一的新鲜前台客户端。没有可观察客户端返回 frontend_unavailable；多个返回 frontend_ambiguous。此时没有发送任何操作，不要说已经完成，也不要猜第一个页面就是用户想要的页面。选择明确的客户端后可操作其当前显示的同账号其他会话；导航后的后续预览/打开仍使用同一个 client_id。附件增删必须在接收页面所显示的当前会话执行。

互相依赖的界面操作按回执顺序执行：前一步确认后重新读取 snapshot 的 revision，再发下一步；不要并发提交依赖同一旧 revision 的操作。

例如读取 `app snapshot, client_id` 得 revision 7 后，调用 `app open, target:history, client_id, expected_revision:7`。前台页面在执行前已变化会返回 frontend_conflict；重新观察后决定下一步，不盲目重复。旧版客户端没有上报状态时诚实返回不可观察，需要客户端升级。

UI 操作返回 `status: requested, completed: false` 和 `request.id`，仅表示请求已持久化。用 app `action: read, id` 查回报：pending 是等待页面，dispatched 是某个页面已领取、结果尚未知，applied 是页面确认执行并提交匹配语义观察，failed/dismissed 是失败或取消。不要把发送请求说成已经切换、已经下载完成或已经修改密码。

回执 receipt 中 observed_revision 对应核验的观察。preview 的 applied 只说明指定预览面板已打开，file_ready 单独说明内容是否加载完成，加载失败可能仍有可见面板；需要时再 snapshot 查 ready/failed。download 的回执仅为 download_requested，不能声称文件已保存到用户设备。

请求在创建时绑定具体客户端，客户端鉴权也绑定其登录会话，不存在多个页面抢先领取。崩溃后 dispatched 不会自动重复下载或重放其他可能已有副作用的操作；先核对实际情况，再决定是否新建请求。停止或 Reset 取消相应尚未完成的 UI 请求，但不能撤回已经发生的浏览器动作。

打开密码表单只是把固定输入交给用户，不是更改密码成功。密码、登录凭据不传入模型工具。打开文件选择器也不等于文件已经选中或上传，缺用户操作时使用 user_help 配合可见的选择控件。

Reset 取消尚未完成的动作并撤销旧的客户端观察身份，保留聊天与文件。页面重新注册后才有新鲜状态；重置前迟到的保活不能让旧界面重新变成可信状态。
