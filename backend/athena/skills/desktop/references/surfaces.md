# 桌面组件与真实观察

先从 `app list` 找当前 Mac 客户端，再读取 `app snapshot`。`state.desktop` 是带观察时间的宿主语义状态；它不是截图、像素检查或第三方应用的任意 DOM。`supported:true,platform:"macos"` 表示此宿主提供桌面组件；`presented` 表示召回入口的呈现状态。屏幕列表是 `screens:[{id,frame,visible_frame,scale}]`。`frame/visible_frame` 是全局左上坐标、单位为 macOS point；`scale` 是屏幕缩放倍率，不要把 point 当物理像素。

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

## 验证与恢复

`registry read/list` 的 `render.scope:"document_load_only"` 只说明 HTML 文档的加载观察；它不证明原生桌面窗口已显示。`app snapshot` 中的 `desktop_registration` 是期望的组件和布局；`state.desktop.components` 才是实际宿主报告：

- `id/version/view_revision` 对应真实源码与布局；旧版本不能证明新版本显示。
- `state` 为 `loading`、`mounted`、`hidden`、`failed`；`visible` 单独说明可见性。
- `rect` 是相对该屏幕 `visible_frame` 左上的实际 point 位置与尺寸，`screen_id` 指向实际屏幕。
- `failed` 带有限的 `error_code`，不要把它复述为任务已完成。

`mounted` 是宿主加载回执，不是像素验收；`visible:true` 不证明按钮工作或文件处理成功。需要时让用户实际交互，并从工具结果验证效果。隐藏入口不能被解释为用户已经阅读回复。后台任务、状态、停止与恢复继续由宿主管理。仅注册成功但没有原生回执时，明确“已保存，尚未确认显示”。
