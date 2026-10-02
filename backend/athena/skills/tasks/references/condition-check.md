# 条件脚本的最小可核验示例

场景：等待用户文件区里的 `ready.txt` 出现，不需要模型定时轮询。条件脚本每次读取当前状态，只输出一个 JSON 对象：

```python
import json
import os
from pathlib import Path

target = Path(os.environ["ATHENA_WORKSPACE"]) / "ready.txt"
print(json.dumps({
    "trigger": target.is_file(),
    "context": {"checked_file": target.name, "condition": "file exists"},
}))
```

先在同一会话工作区验证：文件不存在时返回 `trigger: false`；创建一个测试文件后返回 `true`。确认后再将脚本命令登记为 `condition_command`，设置带时区的 `due_at`、至少 60 秒的 `interval_seconds`，以及条件满足时要交给 Athena 的 `prompt`。不要拿真实用户文件做破坏性测试。

脚本确认的是“当前检查时文件存在”，不保证文件之后不被删除，也不等于后续动作已完成。检查端不要执行通知、发邮件等副作用；唤醒后的执行应核对实际状态。这种区分能避免重试条件检查时重复执行外部动作。
