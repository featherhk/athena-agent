# 桌面组件与真实观察

先用 `runtime {"op":"app","args":{"action":"list"}}`，从返回的 `clients` 找当前 Mac 客户端；再用 `runtime {"op":"app","args":{"action":"snapshot","client_id":"实际返回的客户端ID"}}`。这是 `snapshot` 操作，不是 `observe`。`state.desktop` 是带观察时间的宿主语义状态；它不是截图、像素检查或第三方应用的任意 DOM。`supported:true,platform:"macos"` 表示此宿主提供桌面组件；`presented` 表示召回入口的呈现状态。屏幕列表是 `screens:[{id,frame,visible_frame,scale}]`。`frame/visible_frame` 是全局左上坐标、单位为 macOS point；`scale` 是屏幕缩放倍率，不要把 point 当物理像素。

## 注册与布局

仍用现有万能工具，不新建工具协议：

```json
{"op":"registry","args":{"kind":"component","action":"register","id":"collector","title":"文件整理","placement":"desktop","source":"<main><button onclick=\"Athena.submit('整理我选定的文件')\">整理文件</button></main>"}}
```

HTML 使用现有隔离组件约定，CSS/SVG/Canvas 可以定义形象和交互，不受几个固定样式限制。内联脚本可用，外部脚本、任意 fetch、父页面 DOM、密钥和原生系统接口不可直接访问。`Athena.submit` 只在用户明确交互后使用，不在加载时自动执行。读取 interface skill 的桥接说明了解已提供的交互能力。

布局用刚返回的实际 `version/view_revision`：

```json
{"op":"registry","args":{"kind":"component","action":"layout","id":"collector","version":1,"view_revision":0,"view":{"x":1,"y":1,"width":320,"height":220,"screen_id":"1","hidden":false}}}
```

`screen_id` 必须来自这台宿主的屏幕观察，不要照抄示例 ID；省略时由宿主选屏。`x/y` 是目标屏幕可移动区域内的比例（0–1）：0 贴左/上，1 贴右/下，即可用区起点加上比例乘以“可用区尺寸减实际组件尺寸”。`width/height` 为 32–16384 points，宿主按屏幕可用区约束实际尺寸。默认值与真实位置以回执为准，不宣称请求坐标必然等于最终坐标。屏幕移除后可能落到其他屏幕，读取回执中的实际 `screen_id`。

`hidden:true` 收起，`hidden:false` 展开；`null` 清除某项覆盖。变更源码保留布局和收起状态。`screen_id` 只适用于 desktop；改回网页位置时必须清除它，并清除或调整不满足网页尺寸范围的宽高。源码注册的位置也受现有布局覆盖影响，先检查有效 placement。布局有版本冲突时先重新读，不盲目重发。

## 真实文件的桌面交付

表格、文档、图片等已有文件使用 `runtime app action:preview,file_id` 交给宿主。Mac companion 把它呈现在独立桌面组件中，猫头鹰与输入仍是轻量入口，不把完整表格嵌回回复框。网页宿主保持页面内预览。自动交付只首次打开；用户收起、移除或 Reset 后不会因为刷新再次弹出，显式 preview 可重开。

需要指定布局时，先 list/read 现有组件，优先修改它的 layout。新文件组件也可用 `runtime registry kind:component,action:register,id,title,file_id,placement:desktop`；`file_id` 与 HTML `source` 二选一。它指向文件原件，不是一个另存的 HTML 表格。`athena_file_` 前缀是默认交付的保留 ID，可以 layout/remove/restore，不能 register 覆盖。文件组件的 `file` 使用规范文件描述，内容变化由 `content_revision` 标识；版本恢复、fork、Reset 保留文件来源，文件本身不删除。同账户跨会话选择文件时应说明来源，不把其它会话文件误当当前任务结果。

文件显示使用独立回执：`state.desktop.components[]` 的 `file_id/content_revision` 必须与 `desktop_registration` 对应，`state:mounted` 表示文件预览已经准备好；`failed,error_code:preview_unavailable` 表示预览失败。`registry render.scope:file_preview_only` 提示查这些回执，HTML 页面导航完成不证明文档已解析。预览不等于编辑器或公式重算引擎，不承诺尚未实现的原件编辑功能。

## 整片桌面上的效果

烟花、飘雪等没有窗口边界的表达用 `placement:"desktop"`，不要用 `inline`、`workspace` 或网页 `fullscreen`。先读当前 Mac 的 `screens`，将组件布局的 `screen_id` 设为实际屏幕，`x:0,y:0,width:visible_frame.width,height:visible_frame.height`；每个屏幕需要独立组件时才分别注册。当前宿主约束到 `visible_frame`，不覆盖菜单栏和 Dock；不能声称覆盖了未观察到的屏幕或系统保留区域。

页面是透明画布：`html,body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:transparent }`，Canvas/SVG 铺满自己的视口。不加背景、标题栏、边框、卡片和说明面板。纯视觉效果调用 `Athena.interact([])` 声明没有交互区域；需要按钮或可抓取形象时只声明实际区域，不用整个屏幕的透明矩形抢走点击。该桥接名称是 `interact`；没有 `interactions` 方法。它请求宿主指针路由，不能把脚本执行成功当成已验证其他应用可点击。

动画由组件本地执行，模型不逐帧更新。用单个 `requestAnimationFrame` 循环及经过的时间更新位置；限定持续时间、粒子数与绘制分辨率，预绘重复形状，避免每粒子每帧重复生成复杂路径或全屏滤镜。默认短暂播放后清空并停止调度；遵循 `prefers-reduced-motion`，页面隐藏时暂停并重置时间基准，切回不补跑后台积累的帧。普通动画不需要 `Athena.observe`、反复注册、轮询回执或后台模型任务。

检查期望的 screen_id、尺寸与实际 `state.desktop.components[].rect` 是否一致，版本是否 mounted 且 visible。这些证据支持说明已在桌面启动效果，不能把小框内的动画说成“桌面全屏烟花”。透明度、流畅度及跨应用点击仍须实际观察才能宣称已验证；没有像素观察不等于任务失败，也不应惯例地让用户帮忙验收。

## 验证与恢复

`registry read/list` 的 `render.scope:"document_load_only"` 只说明 HTML 文档的加载观察；它不证明原生桌面窗口已显示。`app snapshot` 中的 `desktop_registration` 是期望的组件和布局；`state.desktop.components` 才是实际宿主报告：

- `id/version/view_revision` 对应真实源码与布局；旧版本不能证明新版本显示。
- `state` 为 `loading`、`mounted`、`hidden`、`failed`；`visible` 单独说明可见性。
- `rect` 是相对该屏幕 `visible_frame` 左上的实际 point 位置与尺寸，`screen_id` 指向实际屏幕。
- `failed` 带有限的 `error_code`，不要把它复述为任务已完成。

`mounted` 是宿主加载回执，不是像素验收；`visible:true` 不证明按钮工作或文件处理成功。真实操作以相应工具结果验证；仅在缺少完成任务所必需的输入或操作时请用户协助。隐藏入口不能被解释为用户已经阅读回复。后台任务、状态、停止与恢复继续由宿主管理。仅注册成功但没有原生回执时，说明“已保存，还在等待桌面显示”。

对用户只说结果及影响使用的限制。例如当前宿主已报告整片可用桌面的效果启动，可说“桌面烟花已启动，约十秒后自动结束。”不附带屏幕 ID、尺寸、回执字段或“请确认像素效果”，不声称自己看到了画面。失败则简短说明实际状态和下一步；工程细节只在用户排障或明确要求时展开。
