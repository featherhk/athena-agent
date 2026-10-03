---
name: application
description: "操作 Athena 界面、切换会话与修改设置；区分应用、扩展与回复偏好"
---

# Athena 应用设置

前端是与用户交互的工具，不是只能装饰的聊天页面。先查看其实际状态与能力，再选择已有设置或注册扩展。现有设置不是用户需求的边界；未预先设计的需求也可以组合已有能力、编写扩展并验证结果。

操作界面（切换会话、打开面板、预览文件等）先用 `runtime {"op":"app","args":{"action":"list"}}` 查看真实目录；详细参数与回执含义见 [references/controls.md](references/controls.md)。目录中的 `clients` 是浏览器观察摘要；用 `app snapshot, client_id` 按需查看页面、工作区、组件、等待事项与错误。先观察，再操作，再核对新观察和回执。切换会话是 `app navigate`，不只是读取历史。既有按钮能完成的事，优先用对应接口实际执行。

查询其他会话仍在做什么、哪里需要用户回应或最近有什么结果，用 `runtime {"op":"activity","args":{"action":"list"}}`；已读规则见 [references/activity.md](references/activity.md)。完成普通界面操作后用用户语言简短确认，不主动列出 client_id、revision、状态字段或文件字节数；用户询问诊断细节时再给出。

- 查看：`runtime {"op":"app_settings"}` 返回 `settings` 当前值和 `schema`，后者列出字段、选项、默认值与用途。
- 修改：`runtime {"op":"app_settings","settings":{"ui_language":"zh-Hans","ui_font":"serif"}}`。只传需要修改的字段，其他设置保留。简体用 `zh-Hans`，繁体用 `zh-Hant`；语言的 `system` 跟随设备，不支持时回退英语。当前也提供字体 `ui_font`、配色 `ui_palette`、减少动效 `reduced_motion`；能力以 schema 为准。
- 配色预设是快捷方式，不是限制。`ui_palette` 也接受任意 `#RGB` / `#RRGGBB` 背景色，界面自动生成可读的文字和控件颜色。黑色用 `{"ui_palette":"#000000"}`，深蓝用 `{"ui_palette":"#101b35"}`；明确的颜色要求直接落实，不因没有同名预设而拒绝，也无需创建组件。返回规范化的十六进制颜色可确认设置已持久化；恢复默认用 `dream`。
- 顶部标识的大小用 `presence_size`（整数 32–240 像素，默认 84），例如 `{"presence_size":48}`。阅读长对话时它自动收起。任意形状、图案或动效可通过 `update_frontend` 注册 `placement="presence"` 的组件，替换表达区域；固定状态提示和停止入口保留。先读取 interface skill，并用组件实际渲染回执验收。
- 用户要求“打开开发者模式”时，设置 `{"developer_mode":true}`；关闭用 `false`。这是 Athena 自带的 token、费用、缓存率浮窗，账户设置中也能开启。不要误以为是打开浏览器开发者工具，也不要声称功能不存在。凭据仍不会进入模型或浮窗。
- 成功结果中的 `settings` 是已持久化的完整设置，客户端通过事件同步。核对返回值，必要时重新读取；这不等于已目视验证渲染。失败时不能宣称已切换。界面设置不会翻译已有聊天或文件，也不会设置回复语言。模型选择走 models skill，不能在此设置供应商、地址或凭据。

根据上下文判断用户指的是界面、回复还是外部内容。例如在设置或界面语境中，“文字设为简体”指向界面语言，应直接调用上述接口；不要只承诺以后用简体回复，也不要擅自将其当成文件转换。“以后用英语回复”是回复偏好，使用 memory skill 保存，不改变界面。明确要求翻译文件则处理指定文件。缺乏上下文且确有歧义时，只问一句“是改界面语言，还是以后回复用这门语言？”

已有设置直接用真实接口，不用 shell 改文件或创建替代设置组件。需要新交互时读取 interface skill，用 update_frontend 注册组件；需要新执行能力时读取 extensions skill，通过 runtime register_tool 注册后端工具。扩展文件留在用户文件区，验证登记版本及实际执行或可观察结果后再说明完成。若现有注册接口无法覆盖需求，明确缺少的具体接口，不能假称改好了，也不能绕过保护修改固定外壳、数据库或应用源码。

浏览器或电脑控制能力必须先查明是否可用，不能把技能说明当成已安装的执行器。

## 账号

`runtime {"op":"account","args":{"action":"read"}}` 读取自己的显示名和头像；`action=update`、`profile={display_name,avatar_file_id}` 修改（均可只提供一个）。头像必须是自己文件区中的图片ID，先通过附件取得。

修改密码用 `action=change_password` 打开固定的安全密码表单。不要在聊天、工具参数、记忆中收集或存储密码，不假称表单打开等于密码已修改。账号变化会同步到已打开的会话。
