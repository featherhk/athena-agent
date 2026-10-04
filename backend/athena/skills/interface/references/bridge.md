# 前端工作面与桥接

组件是独立 HTML；脚本用标准 DOM、Canvas、SVG 或 CSS 即可。不要改仓库或要求用户刷新。一次成功注册会持久化版本与刷新事件，浏览器应直接接收；没有浏览器回报时说明尚未验证，先排查实际状态。

注册后最多读取一次渲染回执；仍为 unobserved 时，说明等待页面连接即可。不要反复轮询、重新注册或为等待回执启动模型循环。

## 选择工作面

- `inline` 是聊天内内容，`workspace` 是主要工作区，`banner` 是提醒。
- 四角 `top-start/top-end/bottom-start/bottom-end` 是浮动小工具；`sidebar-start/sidebar-end` 会占据侧栏空间，适合导航、待办或辅助信息，避免遮住聊天。start/end 跟随阅读方向。
- `overlay` 是自由画布，HTML/SVG/Canvas 可在整个聊天工作面的任意位置绘制，不限于四角。默认透明、鼠标穿透；需要点击、拖动、触摸或键盘交互时用 `Athena.interact` 声明实际交互区域，空白处继续滚动和选字。使用布局锚点跟随内容，不靠固定角落冒充跨气泡动画。
- `fullscreen` 提供沉浸工作面，保留外层输入和退出控件。用它做播放器、画板等；不是浏览器或系统全屏权限。
- `presence` 替换默认 Athena 视觉标志，宿主保持状态文字与控制。它可以是自绘形状、Canvas 或动画；不要假设一定是球体。

所有工作面仍属于当前会话。替换或移除组件会销毁 iframe；重要状态保存到文件、工具或普通消息，不能只放内存。恢复版本也会重新挂载。

## 收起、移动和尺寸

宿主提供收起、布局菜单，以及浮窗标题拖动和角落缩放。收起保留源码、文件和登记；菜单的“工作界面”可重新打开。关闭整个工作区会回到聊天。普通卡片自动适应内容高度；`Athena.resize(height)` 可接管高度。全工作区、动画层和标志的区域尺寸由宿主决定。

同一页面内收起、改位置或切换会话时，宿主暂存普通原生表单的输入和勾选，重新打开同版本时恢复；密码、验证码、支付凭据和文件选择不暂存。它是有界浏览器内存，不是数据库文档：刷新页面、登出、Reset 或新源码版本不保证保留，Canvas/自定义 JS 状态也不在其中。重要编辑仍须主动保存到文件。

先用 `runtime registry action:read/list kind:component` 读取 `version`、`view_revision` 和 `view`，再直接执行相同操作，无需重写 HTML：

```json
{"op":"registry","args":{"kind":"component","action":"layout","id":"notes","version":1,"view_revision":0,"view":{"hidden":false,"placement":"top-end","width":400,"height":300,"x":1,"y":0}}}
```

`hidden` 收起/展开；`placement` 覆盖登记时的位置；`width/height` 是 CSS 像素（240–1600 / 160–1600）；`x/y` 是可用区域内的相对位置（0–1，物理左/上为0）。传 null 清除某项覆盖。窗口变化会约束实际尺寸，不能遮挡独立顶部控制和输入区。自动浮窗避开正文和其他组件，空间不足时回到聊天流；用户明确指定 x/y 后可自由覆盖正文，手机上也保留明确位置。键盘弹出或帮助表单令空间小于最小可用尺寸时才回到聊天流。工作区、标志和动画层使用宿主区域，侧栏宽度由页面布局决定。

布局单独持久化、审计，不增加源码版本；重新登记内容也不会把用户收起的组件弹出来。布局更新返回的是已保存状态，视觉效果仍需浏览器回报。旧版本或旧 view_revision 会拒绝，读取最新状态后决定是否重试，不可盲目覆盖另一窗口的调整。分叉复制当时布局，之后彼此独立；Reset 停用组件并清除布局覆盖，保留原件。

打开布局管理列表用 `runtime app action:open target:surfaces`。它也能收起不接收鼠标的动画层或自定义标志。

## 统一视觉与主题

宿主自动提供 CSS 变量：`--canvas --surface --wash --tint --ink --muted --accent --on-accent --font`。主题改变时变量实时更新，不需要重载组件。基础按钮、表单、链接继承它们；自定义 CSS 可有意覆盖。避免写死黑色背景、重复外层卡片、过多边框和大标题。

宿主已经显示组件标题并提供外层间距；侧栏、工作区内容直接从导航或正文开始，不重复标题、圆角容器和整块内边距。默认延续清晰衬线字体、细线和克制的色面；用户有明确风格要求时可以改变，状态与恢复控件仍由宿主保留。

`Athena.theme` 包含 `{tokens,font_family,scheme}`，可供 Canvas 取色。Canvas 应从 `Athena.observe` 的 `layout.theme` 响应主题变化。自定义字体只能使用本机已安装字体或 CSS 通用字体族，当前不允许加载在线字体。

## 观察语义布局

```js
const unsubscribe = Athena.observe(layout => {
  // layout.viewport = {width,height}：当前 iframe 的 CSS 像素尺寸
  // layout.messages = [{id,role,rect:{x,y,width,height}}]
  // rect 相对当前 iframe，裁剪到画布及聊天滚动区的可见部分，最多128条
  // layout.reduced_motion, layout.locale, layout.theme
  // layout.anchors = {presence?, conversation?, workspace?, composer?, controls?}
  // 语义区域的 rect 与气泡使用同一坐标系；不可见区域缺省，区域也可能在画布外
  // layout.phase = idle | thinking | executing | responding | waiting | stopped
});
```

滚动、窗口缩放、内容或主题改变时宿主更新布局；没有持续发送消息或模型调用。接口只提供几何与标识，不提供父页面 DOM、账号信息或任意查询。尊重 reduced_motion，关闭无必要的持续运动。`unsubscribe()` 停止订阅。

### 跟随具体文字

给行程的截止时间画线、在某句话旁放动作时，用引用定位，而不是估算整条消息的位置：

```js
Athena.track([{id:'deadline',message_id:'实际消息ID',quote:'周五晚上八点'}]);
Athena.observe(layout => {
  const target = layout.text_targets.find(item => item.id === 'deadline');
  // 先清除上次画面；文字滚走、删除或歧义时，也要撤销旧命中区域。
  Athena.interact([]);
  if (target?.status !== 'visible') return;
  for (const rect of target.rects) {
    // 用这些片段在自己的 HTML/SVG/Canvas 上绘制，坐标仍相对当前 iframe。
    // 跨加粗、链接或换行会有多个片段，不要把它们合并成覆盖整段的矩形。
  }
});
```

`track` 替换该组件的文字目标，null 清空；它需配合 `observe` 接收布局。每项为 `{id,message_id,quote,occurrence?}`，最多32项，每段引用最多512个 UTF-16 单位，合计8192。id 唯一，只用字母、数字、下划线或连字符（最长64）。只匹配当前会话已渲染的用户/助手正文，不含思考、草稿、表单或文件预览。空白归一化，其他字符精确匹配；Markdown 标记不用写入引用。

`status` 为 visible、offscreen、missing、ambiguous 或 limited，只有 visible 带 `rects`。重复引用默认 ambiguous，需用从0开始的 occurrence 指定；序号在完整正文中确定，滚动不会悄悄改成另一个同名位置。超出有界文本扫描或片段数时返回 limited，不猜测。滚动、换行、字体、主题及布局变化自动更新，不需要模型轮询。隐藏、替换、切换会话或 Reset 清除订阅；这是几何定位，不证明文字未被其他视觉元素遮挡。

不要让动作挡住相邻正文；在手机上空间不足时，只画标记，把实际操作放到 inline/workspace。执行提醒等操作仍需真实后台任务，画出一个按钮并不等于完成提醒。

跨气泡动画的最小 Canvas 示例（用 overlay）：

```html
<style>body{margin:0;padding:0;overflow:hidden}canvas{position:fixed;inset:0}</style>
<canvas id="scene"></canvas>
<script>
const canvas=document.querySelector('#scene'), context=canvas.getContext('2d');
let layout=null, pending=0;
const schedule=()=>{if(!pending)pending=requestAnimationFrame(draw);};
Athena.observe(next=>{ layout=next; canvas.width=next.viewport.width; canvas.height=next.viewport.height; schedule(); });
function draw(time){
  pending=0;
  if(layout){
    context.clearRect(0,0,canvas.width,canvas.height);
    const anchors=layout.messages.map(message=>({x:message.rect.x+message.rect.width/2,y:message.rect.y}));
    if(anchors.length){
      const step=layout.reduced_motion?0:time/1600, index=Math.floor(step)%anchors.length, t=step%1;
      const a=anchors[index], b=anchors[(index+1)%anchors.length];
      const x=a.x+(b.x-a.x)*t, y=a.y+(b.y-a.y)*t-Math.sin(t*Math.PI)*70;
      context.fillStyle=layout.theme.tokens['--accent'];
      context.beginPath(); context.ellipse(x,y-9,5,9,-Math.PI/5,0,Math.PI*2); context.fill();
    }
  }
  if(!layout?.reduced_motion)schedule();
}
schedule();
</script>
```

这个例子只说明锚点使用；按用户的风格设计最终形象。没有可见气泡时，选择自己的空态位置，不要凭空假设坐标。

## 自由画布的交互

画面由你自己的 HTML/SVG/Canvas 绘制，交互区域不局限于预设位置。`Athena.interact(regions, callback)` 提供真实的宿主按钮，支持点击、触摸、拖动、键盘和读屏。它不暴露父页面 DOM。每次调用替换当前区域列表；后续更新位置可以省略 callback。传 null 清除所有区域和回调。

```js
let pinned = false;
Athena.observe(layout => {
  const message = layout.messages.at(-1);
  if (!message) { Athena.interact([]); return; }
  // 在同一位置绘制自己的标记；这里仅演示交互声明。
  const rect = {x:Math.min(layout.viewport.width-44,message.rect.x+message.rect.width),
                y:message.rect.y,width:44,height:44};
  Athena.interact([{id:'pin',label:pinned?'取消标记':'标记这段内容',rect,pressed:pinned}], action => {
    if (action.kind !== 'activate') return;
    pinned = !pinned;
    // 更新画面和区域的 pressed；需要持久化时通过明确的用户点击提交实际请求。
  });
});
```

区域结构为 `{id,label,rect:{x,y,width,height},pressed?}`，id 唯一，label 使用用户语言说明动作，最多64个区域。坐标是相对画布的 CSS 像素，随布局、滚动或你自己的动画更新；超出画布的部分同时从画面命中和键盘焦点中裁掉。移动不要调用模型或把每一帧写进数据库。建议触摸区域至少44×44px；不要用不可见的整屏按钮拦截阅读。

回调得到 `{id,kind,point:{x,y},pointer}`；kind 为 activate/press/move/release/cancel，pointer 为 mouse/touch/pen/keyboard。拖动超过阈值后不会再触发一次点击；键盘激活的位置为区域中心。普通表单、输入法、编辑器仍应使用 inline/workspace 或浮窗中的真实 HTML 控件；不要用画布模拟密码表单。`navigate` 需要原 iframe 的直接用户激活，不能从画布回调假装直接导航；可在明确点击后用 `submit` 向 Athena 提出切换请求。

停止、恢复、输入和账户弹窗仍由独立外壳控制。隐藏、移除、重置、源码更新或会话切换会撤销该组件的所有命中区域。错误版本不能留下透明的点击拦截层。

## 文本外观

```js
Athena.observe(layout => {
  for (const message of layout.messages) {
    Athena.styleText({message_id:message.id,unit:'character',styles:[
      {fontFamily:'Georgia, serif',color:layout.theme.tokens['--accent']},
      {fontFamily:'system-ui',fontWeight:600}
    ]});
  }
});
```

颜色只接受十六进制或普通 CSS 色名；当前主题色使用 token 的实际值，不传 `var(...)` 字符串。`unit` 为 character（Unicode 字符单位）或 word；样式数组循环应用。支持 color、fontFamily、fontSize（8–72px）、fontWeight（100–900/normal/bold）、fontStyle、textDecoration、opacity、letterSpacing（-2–8px），以及有界 translate/rotate/scale transform。最多256种样式，不接受任意 CSS、URL、选择器或 HTML。

每个组件可为多个消息设置效果；`Athena.styleText(null)` 撤销该组件所有文本效果。组件移除、替换或 Reset 后宿主自动清除。文本内容、Markdown 和链接含义不应改变；过长文本的视觉效果会受宿主的性能限制。

## 会话导航与提交

先通过 `runtime sessions action:list` 取得实际会话 ID，再写入侧栏按钮。点击时直接切换，不必让模型再解释一次：

```html
<button id="open">打开指定对话</button>
<script>
document.querySelector('#open').onclick=()=>Athena.navigate('实际会话ID');
</script>
```

`navigate` 只在用户真实点击中工作，宿主验证目标会话属于当前账户。返回 true 仅表示发送请求，不代表目标已经打开；不要在加载或定时器中导航。

`Athena.submit('用户希望发送的请求')` 将一次明确点击交给助手；`Athena.resize(height)` 用于普通卡片高度（80–1000px）。overlay/fullscreen/presence 等区域高度由宿主决定。不要在加载或动画每帧时自动提交用户消息。
