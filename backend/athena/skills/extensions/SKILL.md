---
name: extensions
description: "注册、检查、停用和恢复用户工具、组件、技能目录包与运行时策略"
---

# 扩展与恢复

不要修改 Athena 仓库、依赖、数据库、认证或 reset 外壳。先查看已有能力；确实缺少时，在外部文件区注册扩展，再验证结果。用户自己的工作文件使用 ATHENA_WORKSPACE。

统一操作为 `runtime` 的 `op: registry`；将 `kind: tool | component | skill | policy`、`action: list | read | register | restore | remove` 和其他字段放在 `args` 中。工具、技能和策略按账户共享，组件只属于当前对话。所有修改保存为新版本；停用也追加版本，历史不被改写。

- `list` 返回版本元数据，不自动载入源码；可按 `name` 或组件 `id` 过滤，用 `offset/limit` 翻页。`current` 指最新版本，只有 `enabled: true` 才当前生效；旧记录的 `active` 是其当时状态。
- `read` 传名称和可选 `version`，默认最新版本；停用后仍可读取历史。先查实际内容再修改。
- `restore` 必须明确指定历史 `version`，复制完整旧内容为一个新的启用版本，不会把版本号倒退。
- `remove` 传名称或组件 id，停用当前版本。再次停用不会恢复更早版本。

## 工具

`kind: tool, action: register` 传 `name/description/parameters/source`。`name` 为 `user_` 加小写字母、数字、下划线，例如 `user_calculate`。`parameters` 为 JSON object schema，不支持 `$ref`。`source` 为 Python 源码，从 stdin 读取 JSON 参数，向 stdout 输出 JSON 结果。只依赖标准库或已有依赖。

```python
import json, sys
args = json.load(sys.stdin)
print(json.dumps({"result": args["a"] + args["b"]}))
```

新工具会出现在下一次模型请求中；重复注册同名工具创建新版本。单账户最多启用 20 个工具，恢复也受此限制。旧的 `runtime register_tool/remove_tool` 仍可使用。注册只验证语法和参数定义；应使用无副作用输入测试，再用真实任务验证。坏版本可读取旧版本后明确恢复。

工具运行在独立进程中，默认最多 120 秒，不会导入核心后端。macOS 保护器禁止写仓库，禁止直接读写核心数据目录，允许读取本账户扩展并读写文件区；它不是对其余本机目录的完整隔离。不要通过外部程序绕过保护。

## 组件

`kind: component, action: register` 传 `id/title/source`，`source` 是隔离页面的 HTML。`placement` 支持 inline、workspace、banner、四角 top-start/top-end/bottom-start/bottom-end、sidebar-start/sidebar-end、overlay、fullscreen、presence，以及替换整个会话 renderer 的 surface；详细布局与桥接接口见 interface skill 的 references/bridge.md 和 references/surface.md。省略位置时保留原位置，新组件默认为 inline。`update_frontend upsert/remove` 继续有效，并共享同一版本历史。恢复时传组件 `id` 和历史 `version`，位置也一并恢复。组件不能修改固定控制外壳；应用字体、颜色等已有设置应使用 application skill。

注册响应中的 `render.state: unobserved` 不是显示成功。后续 `read/list` 可查看该版本的浏览器回报（mounted/failed、has_content、in_view、error_code、observed_at）；回报只证明当次加载状态，不代表数据真实、按钮可用、自动刷新成功。已有组件必须从当前会话登记状态确认，不能从历史对话中推定仍存在；详细显示与数据更新限制见 interface skill。

## 技能目录包

`kind: skill, action: register` 传 `name` 和 `files`。名称也必须以 `user_` 开头，不能覆盖内置技能。包必须含 `SKILL.md`，其 frontmatter 的 `name` 必须与注册名相同，`description` 为 1–300 字符。正文、参考资料、模板和脚本按需读取，不要把所有内容塞进长期提示。

```json
{
  "op": "registry",
  "args": {
    "kind": "skill", "action": "register", "name": "user_planning",
    "files": {
      "SKILL.md": "---\nname: user_planning\ndescription: 制定一周计划\n---\n\n先读取 references/checklist.txt，再按用户时间安排。",
      "references/checklist.txt": "先确认可用时间，再列出三项优先事项。",
      "scripts/example.py": "print('ready')\n"
    }
  }
}
```

资源用相对路径，不允许绝对路径、`..`、符号链接或文件与目录冲突；最多 64 个文件、解码后共 4 MiB。文本值是 UTF-8 字符串；其他格式可用 `{encoding: "base64", content: "..."}`。版本目录不允许由工具进程直接改写，修改后重新注册。

`runtime skill` 传 `name` 按需读取已启用技能的 `SKILL.md`；用 `path` 指定包内资源，支持 `offset/limit`。`registry read` 同样支持路径与分段，可读取停用或旧版本。UTF-8 内容按字符偏移，二进制内容以 base64 返回、按原始字节偏移。脚本不会因读取而执行；确需运行时先检查源码，通过执行工具写入用户工作区并运行。恢复技能会复制整个历史目录包。

兼容简单单文件注册：可传 `source` 和 `description`，系统合成 `SKILL.md` metadata；优先使用完整 `files` 包。

## 运行时策略服务

`kind: policy, action: register` 传 `name/description/source`。服务名允许 `[a-z][a-z0-9_.-]{0,63}`，不限于预设名称。先用 `kind: policy, action: list` 读取 `consumers`，确认有哪些消费者及输入输出合同已经接入。未知服务可以保存、读取、升级、恢复，但状态为 `waiting_for_consumer`、`effective: false`；只有消费者接入才会影响产品，不能把保存代码宣称为功能已生效。

当前消费者合同如下，均从 stdin 读取 JSON，向 stdout 输出一个 JSON 值：

- `memory_recall`：输入含 `default`（默认记忆 ID 列表）和 `candidates`（含 id、scope、content 的候选项）。输出不重复的候选 ID 列表，可以选取和排序；父进程保留原始记忆文本，不允许凭空添加记忆。
- `context_compression`：输入含 `default`（默认裁剪计划）、`target_tokens` 和 `messages`（已完成工具结果的 id/content）。输出 `{message_id: clipped_content}`；只能缩短已完成的工具结果，必须保留 `message_id=...` 原文恢复标记。父进程验证计划并保留完整原文。不要修改用户消息、工具调用对应关系或先前继承的历史边界。
- `execution`：输入的 `default` 含 `command` 和 `timeout_seconds`。输出符合 exec 参数合同的对象，命令最多 30000 字符、超时 1–300 秒。这里只转换没有 stdin 的 exec 调用；父进程校验后实际执行一次，不会因转换失败重复执行。已注册工具按其请求绑定版本执行，不经过此命令转换。

策略源码使用 Python 标准库。例如下面的策略完整保留默认行为：

```python
import json, sys
payload = json.load(sys.stdin)
print(json.dumps(payload["default"]))
```

策略在独立隔离进程中运行，不导入服务器；最多 3 秒，stdout 最多 256 KiB。macOS 禁止策略进程写文件、联网、创建子进程或发送进程信号；它只能返回转换结果。供应商环境密钥不传入进程。当前其他平台使用默认行为。

JSON、父进程校验、运行时间或隔离执行失败时，使用输入的默认值，并为仍是当前版本的失败策略追加停用版本；失败代码和版本会记录，不记录原始异常正文。并发升级的新版本不会被旧版本失败误停用。排查后可明确恢复某个历史版本，或注册修正版。

Reset 会中断当前用户运行和检查，追加前后端、技能与策略的停用版本，并暂停后台任务。聊天、文件、记忆与历史版本保留。不要自动重新启用刚被 reset 停用的扩展，等用户重新提出需求。
