"""Exercise the real desktop host in an isolated, offline native fixture app."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import plistlib
import runpy
import secrets
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[3]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-ios.py'))


class Fixture(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/__fixture/quit-save': return self.send_error(404)
        data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.quit_saves.append(data.decode())
        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()

    def do_GET(self):
        self.server.requests.append(self.path)
        static = getattr(self.server, 'static_root', None)
        if static:
            path = urlparse(self.path).path
            if path.startswith('/api/'):
                data = {'user': None, 'configured': True, 'has_users': True} if path == '/api/auth/me' else {'configured': True, 'message': ''} if path == '/api/service' else {'code': 'not_found'}
                payload = json.dumps(data).encode()
                self.send_response(200 if path in ('/api/auth/me', '/api/service') else 404)
                self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(payload); return
            target = (static / (path.lstrip('/') or 'index.html')).resolve()
            if target.is_relative_to(static) and target.is_file():
                data = target.read_bytes(); self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(target)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
            self.send_error(404); return
        if urlparse(self.path).path == '/api/files/fixture_note/content':
            authenticated = f'athena_native_fixture={self.server.fixture_cookie}' in self.headers.get('Cookie', '')
            self.server.downloads.append({'authenticated': authenticated})
            data = b'Synthetic authenticated download\n'
            self.send_response(200 if authenticated else 401)
            self.send_header('Content-Type', 'text/plain; charset=utf-8'); self.send_header('Content-Disposition', 'inline; filename="note.txt"')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        widget = parse_qs(urlparse(self.path).query).get('desktop') == ['widget']
        html = '''<!doctype html><meta charset="utf-8"><title>Offline native companion acceptance</title>
        <style>html,body{margin:0;background:transparent}textarea{margin:24px;width:220px;height:70px}</style>
        <textarea id="draft">Offline fixture</textarea><a id="download" href="/api/files/fixture_note/content" target="_blank">Download synthetic notes</a><script>
        window.events=[];window.backgroundCount=0;window.proof=17;
        addEventListener('athena:desktop', e=>events.push(e.detail));
        addEventListener('athena:background', ()=>backgroundCount++);
        </script>'''
        if widget:
            html += """<script>const q=new URLSearchParams(location.search);webkit.messageHandlers.athenaDesktop.postMessage({action:'widgetHealth',state:'mounted',...(q.get('file')?{file_id:q.get('file'),content_revision:q.get('content_revision')}:{})});</script>"""
        data = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Set-Cookie', f'athena_native_fixture={self.server.fixture_cookie}; HttpOnly; SameSite=Lax; Path=/')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


WIDGET_HTML = """<!doctype html><style>body{margin:0;background:transparent;color:#29252c;font:18px Georgia}label{display:block;padding:16px;background:#f4eff9;border-radius:18px}textarea{display:block;width:250px;height:85px}</style>
<label>Native component acceptance<textarea id="draft" aria-label="Widget draft"></textarea></label>
<script>
window.fixtureProof=Math.random();
function report(){var e=document.getElementById('draft');parent.postMessage({fixtureReceipt:{draft:e.value,start:e.selectionStart,end:e.selectionEnd,proof:fixtureProof}},'*')}
addEventListener('message',function(e){if(e.source!==parent||!e.data||!e.data.fixture)return;
if(e.data.fixture==='draft'){var field=document.getElementById('draft');field.value=e.data.value;field.setSelectionRange(2,6);field.dispatchEvent(new Event('input',{bubbles:true}));}
if(e.data.fixture==='submit')Athena.submit('Synthetic desktop widget request');report()});
</script>"""


class IntegrationFixture(Fixture):
    def reply(self, data, status=200):
        payload = json.dumps(data).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        self.server.requests.append(self.path)
        if path.endswith('/events'):
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache'); self.end_headers()
            after = int(parse_qs(urlparse(self.path).query).get('after', ['0'])[0])
            try:
                self.wfile.write(b': connected\n\n'); self.wfile.flush()
                while not self.server.finished.is_set():
                    with self.server.changed:
                        self.server.changed.wait(timeout=.2)
                        events = [event for event in self.server.events if event['seq'] > after] if path.startswith('/api/sessions/') else []
                    for event in events:
                        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode()); self.wfile.flush(); after = event['seq']
            except (BrokenPipeError, ConnectionResetError): pass
            return
        if path == '/api/auth/me': return self.reply({'user': {'id': 'fixture_owner', 'username': 'fixture', 'role': 'user', 'developer_mode': False}, 'has_users': True, 'configured': True,
            'preferences': {'ui_language': 'en', 'ui_font': 'system', 'ui_palette': 'dream', 'reduced_motion': 'reduce', 'presence_size': 84, 'developer_mode': False}})
        if path == '/api/sessions': return self.reply([{'id': 'session_a', 'title': 'Offline native integration', 'pending': 0}])
        if path == '/api/sessions/session_a': return self.reply(self.server.snapshot)
        if path == '/api/service': return self.reply({'configured': True, 'message': ''})
        if path == '/api/activity': return self.reply({'owner_id': 'fixture_owner', 'items': [], 'has_more': False})
        if path == '/__fixture/receipt': return self.reply({'submissions': self.server.submissions, 'observations': self.server.observations})
        if path.startswith('/api/'): return self.reply({'code': 'not_found'}, 404)
        target = (self.server.static_root / (path.lstrip('/') or 'index.html')).resolve()
        if not target.is_relative_to(self.server.static_root) or not target.is_file(): return self.send_error(404)
        data = target.read_bytes()
        if target.name == 'index.html':
            # Only seed initial fixture state; all UI, iframe and submit paths are real production code.
            setup = b"<script>sessionStorage.setItem('athena-session:fixture_owner','session_a')</script>"
            data = data.replace(b'<head>', b'<head>' + setup, 1)
        self.send_response(200); self.send_header('Content-Type', mimetypes.guess_type(target)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self.server.requests.append('POST ' + self.path)
        if self.path == '/api/desktop/resume/claim':
            return self.reply({'writer': body['writer'], 'sequence': 0,
                               'entries': {'athena-session:fixture_owner': 'session_a'}})
        if self.path == '/api/desktop/resume/save':
            return self.reply({'writer': body['writer'], 'sequence': body['sequence']})
        if self.path == '/api/sessions/session_a/components/specimen/report':
            self.server.component_reports.append(body)
            return self.reply({'accepted': True})
        if self.path == '/api/frontend/observation':
            self.server.observations.append(body)
            return self.reply({'accepted': True})
        if self.path == '/__fixture/layout':
            with self.server.changed:
                component = self.server.snapshot['components'][0]
                component['view'].update(x=0.0, y=0.7, width=430, height=310); component['view_revision'] += 1
                self.server.snapshot['cursor'] += 1
                self.server.events.append({'seq': self.server.snapshot['cursor'], 'type': 'components_changed', 'payload': {}})
                self.server.changed.notify_all()
            return self.reply({'ok': True})
        if self.path == '/api/sessions/session_a/messages':
            self.server.submissions.append(body)
            return self.reply({'accepted': True, 'run_id': 'fixture_run', 'status': 'queued',
                'client_submission_id': body['client_submission_id'], 'submission_revision': 0, 'replayed': False})
        return self.reply({'code': 'not_found'}, 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xcode', required=True)
    parser.add_argument('--preview-dist', type=Path, help='Capture real compiled UI with a synthetic signed-out API, without a backend')
    parser.add_argument('--integration-dist', type=Path, help='Exercise real main/child UI and message submission against an isolated synthetic API')
    args = parser.parse_args()
    if args.preview_dist and args.integration_dist:
        parser.error('Choose preview or integration')
    env, _, _ = BUILD['toolchain'](args.xcode)
    output = Path(tempfile.mkdtemp(prefix='athena-macos-native-companion-'))
    bundle = output / 'AthenaNativeCompanionChecks.app'
    resources = bundle / 'Contents/Resources'
    resources.mkdir(parents=True)
    executable = bundle / 'Contents/MacOS/AthenaNativeCompanionChecks'
    executable.parent.mkdir()
    source = ROOT / 'desktop/macos'
    files = ['Athena.swift', 'UpdateCheck.swift', 'WindowRecovery.swift', 'DesktopCompanion.swift', 'DesktopSceneHost.swift',
             'DesktopHitRegions.swift', 'ComputerControl.swift', 'Tests/NativeCompanionChecks.swift']
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-target', 'arm64-apple-macos13.0', '-g', '-D', 'ATHENA_NATIVE_CHECKS',
                    '-framework', 'AppKit', '-framework', 'WebKit', *[str(source / name) for name in files], '-o', str(executable)],
                   env=env, check=True, timeout=180)
    info = {'CFBundleExecutable': executable.name, 'CFBundleIdentifier': 'com.featherhk.athena.companion-nativechecks',
            'CFBundleName': 'Athena Native Companion Checks', 'CFBundlePackageType': 'APPL', 'LSUIElement': True,
            'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0', 'LSMinimumSystemVersion': '13.0'}
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    for language, strings in json.loads((source / 'locales.json').read_text()).items():
        directory = resources / f'{language}.lproj'
        directory.mkdir()
        (directory / 'Localizable.strings').write_bytes(plistlib.dumps(strings))
    BUILD['capture'](['codesign', '--force', '--sign', '-', str(bundle)], env=env)
    with ThreadingHTTPServer(('127.0.0.1', 0), IntegrationFixture if args.integration_dist else Fixture) as server:
        server.requests = []
        server.downloads = []; server.quit_saves = []; server.fixture_cookie = secrets.token_hex(16)
        server.static_root = (args.integration_dist or args.preview_dist).resolve() if (args.integration_dist or args.preview_dist) else None
        server.finished = threading.Event(); server.changed = threading.Condition(); server.events = []
        server.submissions = []; server.observations = []; server.component_reports = []
        server.snapshot = {'messages': [], 'runs': [], 'tasks': [], 'files': [], 'cursor': 1, 'submission_revision': 0,
            'components': [{'id': 'specimen', 'title': 'Desktop specimen', 'html': WIDGET_HTML, 'version': 1, 'placement': 'desktop',
                'view_revision': 1, 'view': {'placement': 'desktop', 'x': .8, 'y': .2, 'width': 360, 'height': 260}}]}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            arguments = [str(executable), '--fixture-origin', f'http://127.0.0.1:{server.server_port}/', '--download-destination', str(output / 'download-note.txt')]
            if args.preview_dist:
                arguments += ['--preview-snapshot', str(output / 'companion.png')]
            if args.integration_dist:
                arguments += ['--integration-snapshot', str(output / 'integration.png')]
            result = subprocess.run(arguments,
                                    env=env, text=True, capture_output=True, timeout=90)
        finally:
            server.finished.set()
            with server.changed: server.changed.notify_all()
            server.shutdown()
    (output / 'console.log').write_text(result.stdout + '\n' + result.stderr)
    markers = [line.split('ATHENA_NATIVE_CHECK_RESULT ', 1)[1] for line in result.stdout.splitlines() if 'ATHENA_NATIVE_CHECK_RESULT ' in line]
    if not markers:
        raise RuntimeError(f'No native companion receipt; inspect {output}/console.log')
    receipt = json.loads(markers[-1])
    receipt.update(bundle=str(bundle), requests=server.requests, downloads=server.downloads, quit_saves=server.quit_saves)
    if not args.preview_dist and not args.integration_dist and receipt['ok'] and server.downloads != [{'authenticated': True}]:
        receipt.update(ok=False, error='Expected exactly one authenticated WebKit attachment download')
    if not args.preview_dist and not args.integration_dist and receipt['ok'] and server.quit_saves != ['Unsent quit fixture']:
        receipt.update(ok=False, error='Normal quit did not await exactly one final synthetic draft save')
    if args.integration_dist:
        receipt.update(submissions=server.submissions, observations=server.observations, component_reports=server.component_reports)
        if receipt['ok'] and (len(server.submissions) != 1 or server.submissions[0].get('text') != 'Synthetic desktop widget request'):
            receipt.update(ok=False, error='Expected exactly one component-originated submission')
        if receipt['ok'] and not any(item.get('state') == 'mounted' for item in server.component_reports):
            receipt.update(ok=False, error='Registered component did not persist a mounted report')
        if receipt['ok'] and not any(item.get('client_id') == server.submissions[0].get('frontend_client_id') and item.get('session_id') == 'session_a' and item.get('foreground') for item in server.observations):
            receipt.update(ok=False, error='Submission was not bound to a received foreground observation')
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    print(f'Acceptance receipt: {output}/receipt.json')
    if not receipt['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
