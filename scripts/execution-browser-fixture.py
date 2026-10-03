"""Synthetic loopback site, run only inside the disposable browser container."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

receipts, lock = [], threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        kind = "text/html; charset=utf-8"
        if self.path == "/receipts":
            with lock:
                body = ("<pre>" + json.dumps({"count": len(receipts), "values": receipts}) + "</pre>").encode()
        elif self.path == "/statement.csv":
            body, kind = b"item,amount\nfixture,42\n", "text/csv"
        else:
            identity = "Remembered account" if "athena_fixture=1" in self.headers.get("Cookie", "") else "Guest"
            body = f'''<!doctype html><title>Athena execution fixture</title><h1>{identity}</h1>
<label>Name<input id="name"></label>
<button onclick="fetch('/submit',{{method:'POST',body:document.querySelector('#name').value}}).then(()=>document.querySelector('#result').textContent='Submitted once')">Submit</button>
<a href="/statement.csv" download>Download statement</a><p id="result">Ready</p>'''.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        if kind == "text/csv":
            self.send_header("Content-Disposition", "attachment; filename=statement.csv")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/submit" or size > 10000:
            self.send_error(400)
            return
        value = self.rfile.read(size).decode()
        with lock:
            receipts.append(value)
        self.send_response(200)
        self.send_header("Set-Cookie", "athena_fixture=1; Max-Age=3600; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Content-Length", "0")
        self.end_headers()


ThreadingHTTPServer(("127.0.0.1", 8123), Handler).serve_forever()
