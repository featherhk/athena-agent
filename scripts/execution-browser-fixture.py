"""Synthetic loopback site, run only inside the disposable browser container."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import html
import json
import threading
from urllib.parse import unquote

receipts, uploads, lock = [], [], threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        kind = "text/html; charset=utf-8"
        filename = None
        if self.path == "/receipts":
            with lock:
                proof = json.dumps({"count": len(receipts), "values": receipts, "upload_count": len(uploads)})
            body = ("<pre>" + html.escape(proof) + "</pre>").encode()
        elif self.path == "/upload-receipts.json":
            kind, filename = "application/json", "upload-receipts.json"
            with lock:
                body = json.dumps({"count": len(uploads), "values": uploads}, ensure_ascii=False).encode()
        elif self.path == "/statement.csv":
            body, kind, filename = b"item,amount\nfixture,42\n", "text/csv", "statement.csv"
        else:
            identity = "Remembered account" if "athena_fixture=1" in self.headers.get("Cookie", "") else "Guest"
            body = (f'''<!doctype html><meta charset="utf-8"><title>Athena execution fixture</title><h1>{identity}</h1>
<label>Name<input id="name"></label>
<button onclick="fetch('/submit',{{method:'POST',body:document.querySelector('#name').value}}).then(()=>document.querySelector('#result').textContent='Submitted once')">Submit</button>
<a href="/statement.csv" download>Download statement</a><p id="result">Ready</p>''' + '''
<label>Documents<input id="documents" type="file" multiple></label><p id="upload-result">No documents received</p>
<a href="/upload-receipts.json" download>Download upload receipts</a><script>
let selection = 0;
document.querySelector('#documents').addEventListener('change', async (event) => {
    const current = ++selection, names = [];
    try {
        for (const file of event.target.files) {
            const response = await fetch('/upload', {method:'POST', body:file,
                headers:{'X-Fixture-Name':encodeURIComponent(file.name), 'X-Fixture-Selection':String(current)}});
            if (!response.ok) throw new Error('Upload failed');
            names.push(file.name);
        }
        document.querySelector('#upload-result').textContent = 'Received documents: ' + names.join(', ');
    } catch (error) {
        document.querySelector('#upload-result').textContent = 'Upload failed';
    }
});
</script>''').encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        if filename:
            self.send_header("Content-Disposition", "attachment; filename=" + filename)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if self.path not in ("/submit", "/upload") or not 0 <= size <= 1024 * 1024:
            self.send_error(400)
            return
        value = self.rfile.read(size)
        with lock:
            if self.path == "/upload":
                uploads.append({"name": unquote(self.headers.get("X-Fixture-Name", "")),
                    "selection": self.headers.get("X-Fixture-Selection"), "size": len(value),
                    "sha256": hashlib.sha256(value).hexdigest(), "base64": base64.b64encode(value).decode()})
            else:
                receipts.append(value.decode())
        self.send_response(200)
        if self.path == "/submit":
            self.send_header("Set-Cookie", "athena_fixture=1; Max-Age=3600; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Content-Length", "0")
        self.end_headers()


ThreadingHTTPServer(("127.0.0.1", 8123), Handler).serve_forever()
