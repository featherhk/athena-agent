# 应用动作与确认

能通过按钮做的基础操作，也应通过运行时实际执行。`runtime {"op":"app","args":{"action":"list"}}` 返回当前动作目录及现有等价操作，不要凭记忆编造能力。

| 需求 | 操作 |
| --- | --- |
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

没有实际文件 ID 时，不要伪造下载地址。先在文件区产生输出，再用 app list/read（不带 id）发现当前会话文件，返回 `files` 中的真实 id；无需打开浏览器触发登记。默认返回50条，可用 limit（1–200）与 offset 翻页。当前 download 是已有文件导出，不代表支持任意会话归档格式。

UI 操作返回 `status: requested, completed: false` 和 `request.id`，仅表示请求已持久化。用 app `action: read, id` 查回报：pending 是等待页面，dispatched 是某个页面已领取、结果尚未知，applied 是页面确认执行，failed/dismissed 是失败或取消。不要把发送请求说成已经切换、已经下载完成或已经修改密码。

一个请求只由一个页面领取。崩溃后 dispatched 不会自动重复下载或重放其他可能已有副作用的操作；先核对实际情况，再决定是否新建请求。停止或 Reset 取消相应尚未完成的 UI 请求，但不能撤回已经发生的浏览器动作。

打开密码表单只是把固定输入交给用户，不是更改密码成功。密码、登录凭据不传入模型工具。打开文件选择器也不等于文件已经选中或上传，缺用户操作时使用 user_help 配合可见的选择控件。
