"""Platform boundary for agent-created processes. The application is never writable."""
import json
import os
from pathlib import Path
import shutil
import sys


def command_environment(workspace):
    """Use Athena's interpreter and libraries without inheriting operator secrets."""
    allowed = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "COMSPEC")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    # Keep a venv executable's lexical path: resolving its symlink would select
    # the base interpreter and lose the dependencies installed in that venv.
    python = Path(sys.executable).absolute()
    env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", os.defpath)
    env["ATHENA_PYTHON"] = str(python)
    env["ATHENA_WORKSPACE"] = str(workspace)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def protected_command(command, config, readable, writable, *, timeout=300):
    if sys.platform == "linux" and config.execution_socket:
        # The shim is trusted host code. Agent code runs in a separate container,
        # which never mounts this socket, the application, or its database.
        owners = {Path(value).resolve().relative_to(config.data_dir).parts[1] for value in readable}
        if len(owners) != 1:
            raise ValueError("Execution requires one account scope.")
        return [sys.executable, str(Path(__file__).with_name("execution_client.py")),
                config.execution_socket, owners.pop(), str(timeout),
                "command" if writable else "policy", command]
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        raise ValueError("当前平台尚未配置源码写保护执行器，命令执行已停用。")
    def path(value):
        return json.dumps(str(Path(value).resolve()))
    # Child processes inherit the same Seatbelt restrictions, including spawned shells.
    exceptions = " ".join(f"(require-not (subpath {path(value)}))" for value in readable)
    # Shell cd/getcwd need metadata on each ancestor of an allowed directory.
    # Permit only those literal directories, not their contents or siblings.
    data = config.data_dir.resolve()
    ancestors = {parent for value in readable for parent in Path(value).resolve().parents
                 if parent == data or parent.is_relative_to(data)}
    traversal = " ".join(f"(literal {path(value)})" for value in sorted(ancestors))
    profile = f'''(version 1)
(allow default)
(deny file-write* (subpath {path(config.core_dir)}))
(deny file-read* (literal {path(config.repo_dir / '.env')}))
(deny file-read* (subpath {path(Path.home() / '.secrets')}))
(deny file-read* (require-all (subpath {path(config.data_dir)}) {exceptions}))
(deny file-write* (require-all (subpath {path(config.data_dir)}) {" ".join(f"(require-not (subpath {path(value)}))" for value in writable)}))
'''
    if traversal:
        profile += f"(allow file-read-metadata {traversal})\n"
    return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command]
