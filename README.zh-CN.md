# Athena

[English](README.md) · 简体中文 · [日本語](README.ja.md)

Athena 是一个从对话开始的个人助手。说出想办的事，进度、结果和需要的交互就会随任务出现。日常使用不应要求用户理解模型、API 密钥或 Agent 框架。

当前是基于 Python 3.14T、SQLite、TypeScript 和 React 的 **macOS / 网页本机原型**。它是允许逐步定制能力的通用助手，界面不以编程为中心。[产品方向](docs/product.md) 区分了已有实现和仍需验证的设想。

当前预览还包括独立的账户/模型网关，支持零额度注册、经过审计的美元测试额度，以及适用于 Apple Silicon 的自包含 macOS 测试包。网关已通过真实模型供应商流式响应和结算检查；[网页预览](https://chat.athena.featherhk.com/)和 [Mac 下载页](https://app.athena.featherhk.com/)已在腾讯云通过 HTTPS 发布。DMG 使用临时签名，尚未公证。语音轮次交互、打断和供应商适配器已实现，但真实音频通道和麦克风通话尚未验证。详见[发布就绪情况](docs/requirements-status.md)、[云端运维](docs/cloud-deployment.md)、[桌面分发](docs/distribution.md)和[语音](docs/voice.md)。

## 已有能力

- 用户账户支持显示名和头像；改密码使用固定的敏感信息输入表单，密码不进入模型消息或工具。模型凭据由服务端统一管理。
- 九种界面语言：阿拉伯语、英语、西班牙语、法语、日语、韩语、葡萄牙语、简体中文和繁体中文。自动匹配浏览器偏好，不支持时回退英语；语言选择器按 BCP 47 标签排序，使用语言原名，不使用旗帜。支持阿拉伯语从右到左布局。界面语言、字体、配色和减少动效设置独立于回复偏好保存。
- 流式聊天支持安全的 Markdown 渲染，供应商实际返回的 reasoning 单独折叠显示，并区分思考、回复、执行状态。持久化事件支持断线后恢复更新，关闭浏览器不会停止任务。
- 消息附件和生成文件与会话关联。用户可下载自己的文件，或查看 Markdown、隔离的 HTML、图片，以及有范围限制的 XLSX 单元格和缓存值预览。
- macOS 受管浏览器通过同一个 runtime 工具使用独立持久登录态、会话标签页、完整页面观察、表单、下载和截图；结果丢失时不自动重发动作。验证与限制见[浏览器执行](docs/browser-execution.md)。
- 消息排队、打断、停止和会话分叉。分叉继承固定历史边界，后台任务复制后保持暂停。
- 通过 `runtime` 并行委派有边界的子任务：独立上下文与文件、持久化等待和恢复、结果引用与分支取消。子任务的求助和输出文件回到主对话，会话费用包含子任务。分叉保留历史回执，不重复启动子任务。详见[分工机制](docs/delegation.md)。
- 三个 OpenAI 格式核心工具：`exec`、`update_frontend` 和 `runtime`。runtime 使用简短的 `{op, args}` 外壳。Skill 是含 `SKILL.md` metadata 的目录包，可附带脚本、参考资料、模板及其他资源；正文与资源按需加载。
- 工具、组件、技能和策略提供方以版本形式保存在源码仓库外。恢复历史注册会创建新版本。前端贡献支持对话内、工作区、侧栏、固定、覆盖层、全屏和状态展示等界面，并支持实时更新和渲染回执；固定外壳保留输入、停止、导航和重置。
- 已接入三类策略扩展点：上下文压缩、记忆召回和命令准备。插件在有边界的子进程中执行，错误、超时或无效输出会回退默认行为。服务 ID 开放注册，但没有接入消费方的注册会标记为等待，不会假称已经生效。
- 定时、周期和脚本条件任务。检查条件无需调用模型；匹配后向原会话加入待执行工作。
- 用户、项目、会话层级的显式记忆，可恢复的工具输出裁剪，以及用量、缓存、估算费用与上下文自检。主动压缩可面向估算 token 预算，保留原消息、reasoning 和工具调用配对。usage 缺失或模型窗口未知时保持未知。
- 可选开发者浮窗与运维端注册的 Chat Completions 模型配置。Athena 可在下一次请求切换配置，保持产品身份并生成可审计的上下文版本。Reset 停止工作、恢复默认设置、停用扩展并暂停任务，保留聊天、文件、记忆和历史版本。

[验收说明](docs/acceptance.md) 列出可观察结果；[插件架构](docs/plugin-architecture.md) 区分当前实现边界与 service host 路线。

## 本机启动

Athena 也能通过 `runtime` 查找和读取同账号的其他会话、新建或重命名会话，以及分叉空闲会话。已完成的运行可以从当前模型上下文中排除，也可以恢复；原始聊天记录保持完整。浏览器导航和其他外壳操作使用持久化声明与确认；仅返回会话 ID 并不会触发导航。验收基线见 [自然语言命令验收](docs/command-acceptance.md)。

以下说明面向开发与运维。最终用户注册 Athena 账户后即可发消息，无需配置模型供应商。

需要 macOS、`uv >= 0.9`、Node.js `>= 24` 和 npm。受管浏览器还需要安装 Google Chrome 或 Chromium。

```sh
uv python install 3.14t
uv sync --locked
npm ci
uv run --directory backend python -m athena.doctor
npm run build
npm start
```

打开 [Athena](http://127.0.0.1:4317)。首次启动会初始化本机数据库。随后在另一个终端通过隐藏输入配置服务凭据：

```sh
uv run --directory backend python -m athena.admin provider-key
uv run --directory backend python -m athena.admin status
```

也可从本机文件读取凭据，不打印文件内容：

```sh
uv run --directory backend python -m athena.admin provider-key --file /path/to/private/key.txt
```

默认配置使用 DeepSeek 官方 API 的 `deepseek-flash`（DeepSeek V4.1 Flash）。服务端 `DEEPSEEK_API_KEY` 环境变量优先于保存的默认凭据。保存的凭据留在本机数据库内，不返回客户端；尚未接入系统钥匙串。修改凭据无需重启。

开发前端时使用 `npm run dev`：Vite 监听 5173 端口，将 API 请求代理到 4317 端口的后端。

启动时要求 free-threaded Python 3.14，并检查依赖没有开启 GIL。项目通过本地 uv 配置使用中科大 PyPI 镜像，不修改全局包管理器设置。

## 接入其他模型

后端不绑定 DeepSeek。运维人员注册不可变的模型配置，Agent 只能选择已注册配置，不能自行提供任意地址或凭据。

```sh
uv run --directory backend python -m athena.admin register-model \
  --id second-model \
  --provider 'Provider name' \
  --model 'api-model-id' \
  --base-model 'Underlying model name' \
  --api-base 'https://provider.example/v1'

uv run --directory backend python -m athena.admin provider-key --profile second-model
```

请将示例值替换为供应商文档中的实际配置。`register-model --help` 列出了指令角色、输出 token 参数、reasoning 保留、生成设置和上下文限制等选项。变更定义时使用新的配置 ID，保证运行中的请求和审计记录含义稳定。

当前适配器面向流式 **Chat Completions** API。原生 Responses、Anthropic Messages 等协议需要独立适配器；协议兼容不代表所有供应商和模型都已验证。切换模型保留会话记录，在下一次请求生效，并视作提示缓存边界；实际缓存行为由供应商决定。

## 运行时与数据

同一会话串行执行，不同会话可以并发。事件循环状态只有一个所有者。SQLite 连接在专用线程中使用，通过 WAL 和事务完成状态转移，不依赖 GIL 实现同步。

运行数据保存在仓库外：

| 系统 | 默认数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/Athena` |
| Windows | `%LOCALAPPDATA%/Athena` |
| Linux | `$XDG_DATA_HOME/athena`，默认 `~/.local/share/athena` |

可通过 `ATHENA_DATA_DIR` 覆盖路径，但必须位于源码仓库外。目录中包含 `athena.sqlite3`、完整输出、用户文件、扩展版本，以及私有的浏览器 profile 存储。备份时先停止服务，再备份整个数据目录。[事件无损归档](docs/event-storage.md)会在维护前创建数据库备份；定期备份与加密尚未实现。

消息、模型请求、工具结果、条件检查、唤醒、模型切换和裁剪记录均可审计。事件先落库再广播。后端重启后，未结束的工作标记为中断，不自动重放可能有副作用的操作。客户端断线与后端崩溃具有不同的恢复保证。

后台任务需要本机服务和电脑处于运行状态。恢复后会合并错过的周期。条件检查单次最多运行 30 秒，间隔至少 60 秒。邮件发送等外部动作仍需具体执行器提供幂等机制或结果核对。

`.gitignore`、仓库检查、Git 钩子和 CI 防止误提交运行数据与常见凭据格式。不要将真实聊天、cookie、截图、数据库、私人文件或模型请求录制作为测试样例。JSON 仍是合法的源码与配置格式。

## 当前边界

macOS 命令执行器使用 Seatbelt 禁止写入应用源码，并限制对 Athena 运行数据的访问。注册代码保存在用户文件区。这不等于面向恶意租户的完整沙箱：本机用户仍可能访问其他主机资源。包括托管 Linux 预览在内，未提供保护适配器的平台会停用命令执行。本机运行时仅监听回环地址。托管部署使用独立网关和明确的 HTTPS 来源；它不是通用的公共执行沙箱。

当前 Engine 和 Store 仍直接实现 Agent 循环与持久化，尚未成为 service host 下的可替换提供方。已有策略扩展点不代表模型传输、调度器、认证或整个前端都可替换；注册一个没有消费方的服务 ID 无法补齐接入。应用核心更新仍需要受控重启。

尚待实现或验证：浏览器视觉输入、上传与弹窗流程、桌面控制、操作系统通知、生产级执行隔离、签名并公证的分发包、移动端和 Windows 原生客户端、真实语音通话，以及更全面的任务质量评测。中央账户和预留/结算账本已经存在；收款、账户恢复和生产级对账仍不在范围内。XLSX 预览是有范围限制的读取器，不是电子表格计算引擎。按原型的产品取舍，当前没有逐动作审批流程。

## 验证

```sh
npm run check
node scripts/check-repository.mjs --staged
```

自动测试使用模拟模型响应，不消耗 API 额度。验收可观察的状态变化：账号与文件归属、设置和回复偏好的区分、流式与打断、恢复和分叉、注册版本及恢复、技能资源、策略回退、用量和压缩、语言匹配与仓库数据检查。同一命令也运行 TypeScript 检查和生产构建。已使用 Safari 和内置浏览器验收聊天排版、协助回复、组件热移除、主题变化和窄屏布局。详见[设计语言](docs/design-language.md)与[验收记录](docs/acceptance.md)；Chrome 仍因客户端拦截而未完成验收。

可选的真实模型验收使用临时账户和数据库，不读取已有用户对话：

```sh
uv run --directory backend python ../scripts/evaluate_requirements.py --key-file /path/to/operator-key
```

此检查会消耗 API 额度，不进入 CI；输出结果与用量，失败时附简短的合成用例诊断，不打印凭据或供应商请求录制，结束后删除临时数据。可重复传入 `--case NAME` 选择用例。真实 DeepSeek 联调已通过界面语言、外观、持续回复偏好、显示名、新工具注册并执行、用量与上下文查询、跨会话读取、可恢复压缩、排除已完成回合的验收。压缩通过表示实际缩短投影且保留原文，不保证精确达到 token 目标。这不代表广泛任务成功率、浏览器控制或其他供应商已通过验证。
