"""Platform boundary for agent-created processes. The application is never writable."""
import json
from pathlib import Path
import shutil
import sys


def protected_command(command, config, readable, writable):
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        raise ValueError("当前平台尚未配置源码写保护执行器，命令执行已停用。")
    def path(value):
        return json.dumps(str(Path(value).resolve()))
    # Child processes inherit the same Seatbelt restrictions, including spawned shells.
    exceptions = " ".join(f"(require-not (subpath {path(value)}))" for value in readable)
    profile = f'''(version 1)
(allow default)
(deny file-write* (subpath {path(config.repo_dir)}))
(deny file-read* (literal {path(config.repo_dir / '.env')}))
(deny file-read* (subpath {path(Path.home() / '.secrets')}))
(deny file-read* (require-all (subpath {path(config.data_dir)}) {exceptions}))
(deny file-write* (require-all (subpath {path(config.data_dir)}) {" ".join(f"(require-not (subpath {path(value)}))" for value in writable)}))
'''
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]
