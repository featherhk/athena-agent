"""Private child-process entrypoint for the macOS shell, never a public listener."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit

# Run with -I: the bundle is the only application import path, not the user's cwd.
resources = Path(__file__).resolve().parent
sys.path.insert(0, str(resources / "app" / "backend"))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from athena.config import require_free_threading
from athena.api import create_app
from athena.server import AthenaServer
import uvicorn

require_free_threading()


def configure_bundle(bundle_resources):
    """Only the signed bundle chooses the account origin; no user credential is stored here."""
    settings = json.loads((bundle_resources / "bundle-config.json").read_text())
    if not isinstance(settings, dict) or set(settings) != {"version", "cloud_url"} or settings["version"] != 1:
        raise ValueError("Unsupported desktop configuration")
    value = settings["cloud_url"]
    if not isinstance(value, str):
        raise ValueError("Invalid cloud endpoint")
    if value:
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or any(character.isspace() for character in value)):
            raise ValueError("Invalid cloud endpoint")
    # Explicitly replace a inherited value; cloud builds must not silently fall back to local auth.
    os.environ["ATHENA_CLOUD_URL"] = value.rstrip("/")
    # The native shell always owns a private loopback socket, regardless of a
    # caller's inherited development-server environment.
    os.environ["ATHENA_BIND_HOST"] = "127.0.0.1"
    os.environ["ATHENA_PUBLIC_ORIGIN"] = ""


async def main():
    configure_bundle(resources)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    os.environ["ATHENA_PORT"] = str(port)
    native = None
    if os.environ.get('ATHENA_NATIVE_HOST') == '1':
        from athena.native_host import NativeHost
        native = await NativeHost.stdio()
    config = uvicorn.Config(create_app(native_host=native), loop="asyncio", http="h11", access_log=False,
                            proxy_headers=False, log_level="warning", timeout_graceful_shutdown=5)
    server = AthenaServer(config)
    parent_pid = os.getppid()

    async def watch_parent():
        while not server.should_exit:
            await asyncio.sleep(1)
            if os.getppid() != parent_pid:
                server.should_exit = True

    watcher = asyncio.create_task(watch_parent())
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started and not task.done():
        await asyncio.sleep(0.05)
    if not server.started:
        await task
        raise RuntimeError("Local service could not start")
    # Only communicate an OS-selected port after schema migrations and lifespan startup.
    print(json.dumps({"event": "ready", "port": port}), flush=True)
    try:
        await task
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        if native:
            await native.close()


if __name__ == "__main__":
    asyncio.run(main())
