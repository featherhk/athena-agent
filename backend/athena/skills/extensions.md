# 扩展注册

不要修改 Athena 仓库、启动脚本、依赖、数据库、认证或 reset 逻辑。所有自定义前端和后端代码通过注册机制生效，保存为数据目录下的独立版本文件。注册信息和审计记录保存于数据库。用户自己的工作文件使用 ATHENA_WORKSPACE。

前端：用 update_frontend 的 upsert/remove。不要用 exec 改 React 源码或尝试访问父页面。文件区会展示组件的版本。固定外壳不会随扩展改变。

后端：用 runtime register_tool，提供 name、description、parameters、source。name 必须为 user_ 加小写字母/数字/下划线，例如 user_calculate。parameters 是 OpenAI 工具参数的 JSON object schema，不支持外部 $ref。source 是 Python 源码，从 stdin 读取一个 JSON 参数对象，向 stdout 输出 JSON 结果。只能依赖 Python 标准库或已有依赖。示例：

```python
import json, sys
args = json.load(sys.stdin)
print(json.dumps({"result": args["a"] + args["b"]}))
```

工具会从下一次模型请求开始出现，重复注册同名工具创建新版本；单用户最多 20 个启用工具。runtime remove_tool 传 name 停用。扩展在独立进程中运行，最多 120 秒，不会 import 到核心后端。macOS 的进程保护禁止写入仓库，禁止直接读写核心数据目录，只开放该用户的文件区读写和扩展代码读取。不要通过外部程序绕开边界。

Reset 会先中断当前用户的运行和条件检查，停用前后端注册项并暂停后台任务；聊天、文件、记忆和历史版本保留。不要自动重新启用刚被 reset 停用的扩展，等用户重新提出需求。
