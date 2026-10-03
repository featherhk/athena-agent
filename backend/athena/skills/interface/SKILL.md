---
name: interface
description: "创建或修改用户需要的交互组件"
---

# 按需交互

update_frontend 的 upsert 操作接受 id、title、html。重复使用 id 更新原组件；remove 接受 id 移除组件。源码版本保存在用户文件区，登记和审计保存在数据库。不要修改 React 源码，不要为了普通文字回答创建组件。

html 是独立页面的主体或完整 HTML，运行在隔离 iframe。内联 CSS 和 JavaScript 可用；外部脚本、fetch、顶层导航、访问父页面和本地文件不可用。无需 React 或外部依赖。用清晰文字和足够大的按钮适配手机，跟随用户的语言、主题与任务需要；先读取应用设置，不强制浅色或中文。少装饰，不暴露编程术语。

默认界面文案和图标不使用 emoji；需要符号时用统一的单色 SVG/Canvas 绘制。保留用户原文及其明确要求的表达内容，不过滤聊天或文件里的 emoji。

用 Athena.submit("用户点击按钮后希望发送的文字") 将一次显式用户操作交给助手。不要在加载时自动发送。普通组件自动适应内容高度，Athena.resize(高度) 可接管高度（80–1000 像素）。布局、收起和移动使用 runtime registry 的 layout 操作，见 references/bridge.md。当前版本组件浏览器内部状态在重新加载时重置，重要内容需作为正常用户消息保存；不要把持久编辑器状态只放在 iframe 中。

布局 placement 可为 inline（聊天卡片）、workspace（表格等工作区）、banner（提醒）、四角 top-start/top-end/bottom-start/bottom-end、sidebar-start/sidebar-end（为侧栏实际留出空间）、overlay（透明动画层）、fullscreen（沉浸工作区）或 presence（替换 Athena 标志）。start/end 跟随阅读方向。不能用普通聊天卡片代替用户指定的位置，也不必把动画限制在角落。fullscreen/overlay 均保留固定输入、停止和恢复外壳，不是操作系统全屏。每次注册新版本刷新对应 frame，不刷新整个应用。

界面变化服务于当前目标；普通查询不需要装饰性重排。组件默认继承当前主题变量、字体和基本按钮样式，优先使用 `var(--ink)`、`var(--surface)` 等，不叠加违和的黑盒或多层卡片。涉及自由画布、任意位置交互、逐字样式、会话导航、标志或跨气泡动画时，先读 `references/bridge.md`。Athena.observe/interact/styleText/navigate 提供语义布局、状态和交互，不允许直接读写父页面。当前 CSP 仍不允许外部资源，不能宣称已支持外部视频或在线字体。

## 验证实际效果

前端也是可观察的交互工具。`runtime {"op":"app","args":{"action":"list"}}` 提供客户端摘要；需要理解用户当前看到什么时用 `action:snapshot, client_id` 查看有界语义状态（页面、预览、工作区、组件位置/可见性、等待与错误）。这是已登录页面报告的状态，不是像素验收，也没有草稿、密码、表单值或任意 DOM。检查 stale 和 revision；多端不能假定模型所在会话就是用户眼前页面。需要改变固定视图时使用 application skill 中的 app 操作，任意任务交互通过注册组件实现；不能用状态观察绕过独立停止与恢复外壳。

注册成功仅表示版本已保存，不证明用户看见或功能正常。使用 `runtime registry` 的 component `read/list` 查看当前版本的 `render`：unobserved 是浏览器尚未回报；mounted 是已加载；has_content 表示存在内容；in_view 是该次观察是否在可视区域；failed 和 error_code 表示加载、脚本或资源失败。它们是带时间的浏览器观察，不是端到端功能验证。不要把旧版本的观察当成新版本成功，也不要将注册、计划运行或任务创建宣称为已完成更新。

缺少位置、账户授权或其他无法可靠推断的必需信息时，读取 collaboration skill，通过 user_help 等待用户补充。不要假造天气、价格等实时数据填充组件。联网数据通过后端命令或工具取得，注明来源与更新时间，再注册显示；iframe 内的 fetch 会被阻止。需要定期更新时，先读取 tasks skill，登记后台任务来获取新数据并更新同一组件 id。组件里的计时器只能更新本地展示，不能证明实时数据已刷新。失败时保留带时间的旧数据并说明未更新。
