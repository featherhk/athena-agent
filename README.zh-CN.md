# Athena

[English](README.md) · 简体中文 · [日本語](README.ja.md)

Athena 是一个从对话开始的个人助手。说出想办的事，进度、结果和需要的交互就会随任务出现。日常使用不应要求用户理解模型、API 密钥或 Agent 框架。

当前是基于 Python 3.14T、SQLite、TypeScript 和 React 的 **macOS / 网页本机原型**。它是允许逐步定制能力的通用助手，界面不以编程为中心。[产品方向](docs/product.md) 区分了已有实现和仍需验证的设想。

## 已有能力

- 用户账户与服务端统一管理的模型凭据。普通用户无需配置供应商。
- 九种界面语言：阿拉伯语、英语、西班牙语、法语、日语、韩语、葡萄牙语、简体中文和繁体中文。自动匹配浏览器语言偏好，不支持时回退英语；语言选择器按 BCP 47 标签排序，使用语言原名，不使用旗帜。支持阿拉伯语从右到左布局，动效可跟随系统偏好或手动减少。
- 流式对话与持久化事件。切换会话或关闭浏览器不会停止运行中的任务；重新连接可补发已保存的更新。
- 消息排队、打断、停止和会话分叉。分叉继承固定的历史边界，后台任务复制后保持暂停。
- 三个 OpenAI 格式核心工具：`exec`、`update_frontend` 和 `runtime`。简短指令引用按需加载的 skills。
- 前端组件与 Python 后端工具通过注册生效，版本文件保存在源码仓库外。Reset 停止当前工作、停用自定义扩展并暂停后台任务，保留聊天、文件和记忆。
- 定时、周期和脚本条件任务。检查条件无需调用模型；匹配后向原会话加入待执行工作。
- 可恢复的工具输出裁剪，以及用户、项目、会话层级的显式记忆。原消息和完整输出文件仍可读取。
- 可选的开发者浮窗：API 返回的 token 使用量、缓存命中、请求状态和明确标注的估算费用。usage 缺失不会记作零成本。
- 运维端注册的 Chat Completions 模型配置。Athena 可在下一次模型请求切换到已配置的模型；稳定指令同时说明 Athena 身份与配置的基模。切换会产生可审计的上下文版本。

## 本机启动

以下说明面向开发与运维。最终用户注册 Athena 账户后即可发消息，无需配置模型供应商。

需要 macOS、`uv >= 0.9`、Node.js `>= 22.12` 和 npm。

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

可通过 `ATHENA_DATA_DIR` 覆盖路径，但必须位于源码仓库外。目录中包含 `athena.sqlite3`、完整输出、用户文件、扩展版本，以及预留的浏览器 profile 存储。备份时先停止服务，再备份整个数据目录。自动备份与加密尚未实现。

消息、模型请求、工具结果、条件检查、唤醒、模型切换和裁剪记录均可审计。事件先落库再广播。后端重启后，未结束的工作标记为中断，不自动重放可能有副作用的操作。客户端断线与后端崩溃具有不同的恢复保证。

后台任务需要本机服务和电脑处于运行状态。恢复后会合并错过的周期。条件检查单次最多运行 30 秒，间隔至少 60 秒。邮件发送等外部动作仍需具体执行器提供幂等机制或结果核对。

`.gitignore`、仓库检查、Git 钩子和 CI 防止误提交运行数据与常见凭据格式。不要将真实聊天、cookie、截图、数据库、私人文件或模型请求录制作为测试样例。JSON 仍是合法的源码与配置格式。

## 当前边界

macOS 命令执行器使用 Seatbelt 禁止写入应用源码，并限制对 Athena 运行数据的访问。注册代码保存在用户文件区。这不等于面向恶意租户的完整沙箱：本机用户仍可能访问其他主机资源。缺少保护适配器的平台会停用命令执行。原型应保持仅监听本机，不是可直接公开托管的服务。

尚待实现：内置浏览器与桌面控制、系统通知、常驻执行节点安装包、托管账户、余额与支付账本、移动端和 Windows 原生客户端、语音、子 Agent，以及更全面的任务质量评测。当前登录与费用估算不等于生产级身份和计费基础设施。按原型的产品取舍，当前没有逐动作审批流程。

## 验证

```sh
npm run check
node scripts/check-repository.mjs --staged
```

自动测试使用模拟模型响应，不消耗 API 额度。覆盖数据库事务、并发领取、工具调用流、reasoning 续传、打断、分叉、裁剪恢复、重启处理、条件唤醒、账户边界、扩展 Reset、模型切换、语言匹配与回退，以及仓库数据检查。同一检查命令也运行 TypeScript 检查和生产构建。

另已验证真实 DeepSeek 请求，以及隔离环境中的注册账户到收到回复的冒烟测试。这不代表广泛任务成功率、浏览器控制或其他供应商已经通过验证。
