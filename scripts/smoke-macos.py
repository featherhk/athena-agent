#!/usr/bin/env python3
"""Exercise a relocated macOS bundle using disposable data, without operator credentials."""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def start(resources, data, log):
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "ATHENA_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen([resources / "python/bin/python3.14t", "-I", "-B", resources / "launcher.py"],
                               env=env, cwd=resources / "app", stdout=subprocess.PIPE, stderr=log, text=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        for key, _ in selector.select(1):
            line = key.fileobj.readline()
            if not line:
                raise RuntimeError("Bundled backend exited before readiness")
            try:
                value = json.loads(line)
                if value.get("event") == "ready":
                    selector.close()
                    return process, f'http://127.0.0.1:{value["port"]}'
            except json.JSONDecodeError:
                pass
        if process.poll() is not None: break
    stop(process)
    raise RuntimeError("Bundled backend did not become ready in 30 seconds")


def stop(process):
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
            raise RuntimeError("Bundled backend did not shut down gracefully")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    args = parser.parse_args()
    app = args.app.resolve()
    if not (app / "Contents/Resources/launcher.py").is_file(): parser.error("Not an Athena bundle")
    with tempfile.TemporaryDirectory(prefix="athena-bundle-test-") as temp:
        scratch = Path(temp)
        relocated = scratch / "Application with spaces.app"
        subprocess.run(["ditto", app, relocated], check=True)
        resources = relocated / "Contents/Resources"
        cloud_mode = bool(json.loads((resources / "bundle-config.json").read_text())["cloud_url"])
        data = scratch / "private-data"
        with (scratch / "backend.log").open("w+") as log:
            for iteration in range(2):
                process, base = start(resources, data, log)
                try:
                    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                    def request(path, body=None, headers=None):
                        value = json.dumps(body).encode() if body is not None else None
                        req = urllib.request.Request(base + path, data=value, headers={"Content-Type": "application/json", **(headers or {})})
                        with opener.open(req, timeout=10) as response:
                            return json.load(response)
                    health = request("/api/health")
                    assert health["ok"] and health["gil_enabled"] is False
                    assert health["configured"] is cloud_mode
                    assert urllib.request.urlopen(base, timeout=10).status == 200
                    try:
                        request("/api/sessions")
                        raise AssertionError("Unauthenticated session access was accepted")
                    except urllib.error.HTTPError as error: assert error.code == 401
                    try:
                        request("/api/auth/me", headers={"Origin": "https://untrusted.invalid"})
                        raise AssertionError("Cross-site request was accepted")
                    except urllib.error.HTTPError as error: assert error.code == 403
                    if cloud_mode:
                        # Never create test accounts or transmit credentials to a public gateway in a bundle smoke.
                        auth = request("/api/auth/me")
                        assert auth["user"] is None and auth["has_users"] is True
                    else:
                        setup = request("/api/local/setup")
                        assert setup == {"mode": "local", "initialized": iteration > 0}
                        auth = request("/api/local/bootstrap", {})
                        assert request("/api/auth/me")["user"]["id"] == auth["user"]["id"]
                        assert request("/api/local/provider")["configured"] is False
                        try:
                            request("/api/auth/register", {"username": "bundle_test", "password": "temporary-test-only"})
                            raise AssertionError("Local identity incorrectly allowed another account")
                        except urllib.error.HTTPError as error: assert error.code == 409
                        if iteration == 0:
                            identity = auth["user"]["id"]
                        else:
                            assert identity == auth["user"]["id"], "Local identity was not durable across restart"
                finally:
                    stop(process)
            assert (data / "athena.sqlite3").is_file()
            with sqlite3.connect(data / "athena.sqlite3") as db:
                assert db.execute("SELECT COUNT(*) FROM settings WHERE (key='deepseek_api_key' OR key LIKE 'provider_key:%') AND value!=''").fetchone()[0] == 0
                if cloud_mode:
                    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                    assert db.execute("SELECT COUNT(*) FROM cloud_logins").fetchone()[0] == 0
            assert not list(resources.rglob("*.sqlite3")), "Runtime database leaked into app bundle"
    print("Bundle smoke passed: relocation, GIL-free imports, startup, auth boundary, origin checks, restart, shutdown, no bundled credential. Mode: " + ("cloud (remote login untested)" if cloud_mode else "local (BYOK identity persistence verified)"))


if __name__ == "__main__":
    main()
