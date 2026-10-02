# 前端工作面与桥接

组件是独立 HTML；脚本用标准 DOM、Canvas、SVG 或 CSS 即可。不要改仓库或要求用户刷新。一次成功注册会持久化版本与刷新事件，浏览器应直接接收；没有浏览器回报时说明尚未验证，先排查实际状态。

注册后最多读取一次渲染回执；仍为 unobserved 时，说明等待页面连接即可。不要反复轮询、重新注册或为等待回执启动模型循环。

## 选择工作面

- `inline` 是聊天内内容，`workspace` 是主要工作区，`banner` 是提醒。
- 四角 `top-start/top-end/bottom-start/bottom-end` 是浮动小工具；`sidebar-start/sidebar-end` 会占据侧栏空间，适合导航、待办或辅助信息，避免遮住聊天。start/end 跟随阅读方向。
- `overlay` 覆盖聊天工作面，默认透明、鼠标穿透，可做小鸭跳过气泡、飘落爱心、粒子等动态效果。动画不能靠固定角落冒充；使用布局锚点计算位置。
- `fullscreen` 提供沉浸工作面，保留外层输入和退出控件。用它做播放器、画板等；不是浏览器或系统全屏权限。
- `presence` 替换默认 Athena 视觉标志，宿主保持状态文字与控制。它可以是自绘形状、Canvas 或动画；不要假设一定是球体。

所有工作面仍属于当前会话。替换或移除组件会销毁 iframe；重要状态保存到文件、工具或普通消息，不能只放内存。恢复版本也会重新挂载。

## 统一视觉与主题

宿主自动提供 CSS 变量：`--canvas --surface --wash --tint --ink --muted --accent --on-accent --font`。主题改变时变量实时更新，不需要重载组件。基础按钮、表单、链接继承它们；自定义 CSS 可有意覆盖。避免写死黑色背景、重复外层卡片、过多边框和大标题。

宿主已经显示组件标题并提供外层间距；侧栏、工作区内容直接从导航或正文开始，不重复标题、圆角容器和整块内边距。默认延续清晰衬线字体、细线和克制的色面；用户有明确风格要求时可以改变，状态与恢复控件仍由宿主保留。

`Athena.theme` 包含 `{tokens,font_family,scheme}`，可供 Canvas 取色。Canvas 应从 `Athena.observe` 的 `layout.theme` 响应主题变化。自定义字体只能使用本机已安装字体或 CSS 通用字体族，当前不允许加载在线字体。

## 观察语义布局

```js
const unsubscribe = Athena.observe(layout => {
  // layout.viewport = {width,height}：当前 iframe 的 CSS 像素尺寸
  // layout.messages = [{id,role,rect:{x,y,width,height}}]
  // rect 相对当前 iframe，只有在其范围内可见的 user/assistant 气泡，最多128条
  // layout.reduced_motion, layout.locale, layout.theme
});
```

滚动、窗口缩放、内容或主题改变时宿主更新布局；没有持续发送消息或模型调用。接口只提供几何与标识，不提供父页面 DOM、账号信息或任意查询。尊重 reduced_motion，关闭无必要的持续运动。`unsubscribe()` 停止订阅。

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
      context.font='32px system-ui'; context.textAlign='center'; context.fillText('🦆',x,y);
    }
  }
  if(!layout?.reduced_motion)schedule();
}
schedule();
</script>
```

这个例子只说明锚点使用；按用户的风格设计最终形象。没有可见气泡时，选择自己的空态位置，不要凭空假设坐标。

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
