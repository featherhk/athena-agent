# 操作其他 Mac 应用

这是原生辅助功能接口，与 Athena 自己的桌面组件不同。通过同一个 runtime 工具使用，不需要安装脚本、读取系统权限数据库或猜测辅助进程名字。

1. `{"op":"computer","args":{"action":"status"}}` 返回原生宿主是否连接、Athena 的 `accessibility` 状态。`not_granted` 时仅能列应用；需要用户在系统设置 → 隐私与安全性 → 辅助功能开启 **Athena**。不要把 Accessibility 与 Apple Events 的“自动化”混淆，不改变权限记录或尝试绕过拒绝。用户说已允许后重新查 status。
2. `{"op":"computer","args":{"action":"list"}}` 列出应用的 `app_id`，不激活窗口。随后 `{"op":"computer","args":{"action":"snapshot","app_id":"com.apple.finder"}}` 读取指定应用的窗口、菜单和控件。应用身份必须来自实际列表，不能猜微信的进程名称。
3. 观察包含 `observation_id` 和节点 `ref`（例如 `@n8`）、role/title/description、支持的 actions。对已观察控件调用 `{"op":"computer","args":{"action":"invoke","observation_id":"…","ref":"@n8","native_action":"AXPress"}}`。只用节点实际列出的 action。文本控件 `settable:true` 才能用 `action:"set_value",text:"…"`；密码框不读取、不写入。
4. 一次操作后重新 snapshot 验证效果；旧 observation 只可使用一次，30 秒过期，不能跨对话。`action_dispatched` 仅表示应用接受了操作，不等于目标已完成。`unknown` 表示结果未确认：先查现状，不能自动重放。`history` 可查看本对话持久回执。

关闭图片时观察预览窗口和关闭按钮，不能用退出整个应用替代。排列桌面图标时操作 Finder 实际可见的整理菜单；不要移动文件内容或批量归档来替代图标排列。菜单展开后再观察，按真实菜单名称选目标。若 snapshot truncated 或目标应用没有暴露相关控件，明确说明此次不能可靠定位；不要凭空编造 ref、全屏坐标或“已完成”。

这条通路目前没有屏幕像素识别、坐标点击、全局按键和拖动。托管浏览器另走 browser skill；CLI 仍优先用于适合的文件/命令任务。`exec` 中 AppleScript 的 `-10004` 是权限/执行边界错误，仅凭它无法断言用户漏授权，也不能建议给 Electron/node 等未观察到的进程授权。不要反复重试相同脚本。
