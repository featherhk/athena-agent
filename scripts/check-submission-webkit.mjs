// macOS-only, no network, no real account or existing browser profile.
import { build } from 'esbuild';
import { readFileSync, writeFileSync, readdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
if (process.platform !== 'darwin') throw new Error('This check requires macOS WebKit.');
const root = resolve(import.meta.dirname, '..'), output = mkdtempSync(join(tmpdir(), 'athena-submission-webkit-'));
const result = await build({ entryPoints: [join(root, 'tests/fixtures/submission-webkit-probe.tsx')], bundle: true, write: false,
  format: 'iife', minify: true, define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.css': 'empty' },
  plugins: [{ name: 'offline-catalog', setup(build) { build.onLoad({ filter: /\/i18n.tsx$/ }, () => ({
    contents: readFileSync(join(root, 'src/i18n.tsx'), 'utf8').replace("import.meta.glob('./locales/*.json', { eager: true, import: 'default' })", "({'./locales/en.json':en})"), loader: 'tsx',
  })); } }],
});
const css = readdirSync(join(root, 'src')).filter(name => name.endsWith('.css')).map(name => readFileSync(join(root, 'src', name), 'utf8')).join('\n');
writeFileSync(join(output, 'probe.html'), `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';frame-src about:"><style>${css}</style><div id="root"></div><script>${result.outputFiles[0].text.replaceAll('</script', '<\\/script')}</script>`);
writeFileSync(join(output, 'Probe.swift'), `import AppKit
import WebKit
@MainActor final class Probe: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
 var web: WKWebView!; var window: NSWindow!
 var lastProgress = "launch"; var finished = false
 func finish(_ value: Any) {
  guard !finished else { return }; finished = true
  let data = try! JSONSerialization.data(withJSONObject:value,options:[.sortedKeys,.prettyPrinted])
  print(String(data:data,encoding:.utf8)!); NSApp.terminate(nil)
 }
 func applicationDidFinishLaunching(_ notification: Notification) {
  let config = WKWebViewConfiguration(); config.websiteDataStore = .nonPersistent()
  config.userContentController.add(self,name:"probe")
  web = WKWebView(frame:NSRect(x:0,y:0,width:1080,height:780),configuration:config)
  web.navigationDelegate = self
  window = NSWindow(contentRect:NSRect(x:0,y:0,width:1080,height:780),styleMask:[.titled,.closable],backing:.buffered,defer:false)
  window.contentView = web
  window.title = "Athena · Offline Submission Check"
  window.level = .floating
  // Real WebKit visibility is required for IntersectionObserver and animation/timer delivery.
  // A hidden window plus a JavaScript document.hidden override is not an end-to-end fixture.
  window.center(); window.makeKeyAndOrderFront(nil); window.orderFrontRegardless(); NSApp.activate(ignoringOtherApps:true)
  load([:])
  Task { try? await Task.sleep(for:.seconds(35)); finish(["error":"native_timeout","last_progress":lastProgress]) }
 }
 func load(_ bootstrap: [String:Any]) {
  let data = try! JSONSerialization.data(withJSONObject:bootstrap,options:[.sortedKeys])
  web.configuration.userContentController.removeAllUserScripts()
  web.configuration.userContentController.addUserScript(WKUserScript(source:"window.__submissionFixture = " + String(data:data,encoding:.utf8)!,injectionTime:.atDocumentStart,forMainFrameOnly:true))
  web.loadHTMLString(try! String(contentsOfFile:CommandLine.arguments[1],encoding:.utf8),baseURL:nil)
 }
 func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
  // A fresh document must be genuinely visible before the App may bind sends.
  // Re-present only this disposable fixture window; do not spoof document.hidden.
  window.makeKeyAndOrderFront(nil); window.orderFrontRegardless(); NSApp.activate(ignoringOtherApps:true)
 }
 func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) {
  if let body = message.body as? [String:Any], let bootstrap = body["reload"] as? [String:Any] { load(bootstrap); return }
  if let body = message.body as? [String:Any], let progress = body["progress"] as? String { lastProgress = progress; FileHandle.standardError.write(Data((progress + "\\n").utf8)); return }
  if let body = message.body as? [String:Any], let size = body["resize"] as? [String:Double], let width = size["width"], let height = size["height"] {
   window.setContentSize(NSSize(width:width,height:height)); return
  }
  if let body = message.body as? [String:Any], body["capture"] != nil {
   web.takeSnapshot(with:nil) { image, _ in
    var detail: [String:Any] = ["error":"offline_snapshot_failed"]
    if let tiff = image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data:tiff), let png = bitmap.representation(using:.png,properties:[:]) {
     let name = body["capture"] as? String ?? "surface"
     let destination = URL(fileURLWithPath:CommandLine.arguments[2]).deletingLastPathComponent().appendingPathComponent(name + ".png")
     do { try png.write(to:destination); detail = [:] } catch {}
    }
    let encoded = try! JSONSerialization.data(withJSONObject:detail)
    self.web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('probe-captured', {detail: " + String(data:encoded,encoding:.utf8)! + "}))",completionHandler:nil)
   }; return
  }
  finish(message.body)
 }
}
@main struct Main { @MainActor static func main(){let app=NSApplication.shared;let delegate=Probe();app.delegate=delegate;app.setActivationPolicy(.accessory);app.run()} }
`);
const binary = join(output, 'probe');
const compilation = spawnSync('xcrun', ['swiftc', '-parse-as-library', join(output, 'Probe.swift'), '-o', binary], { encoding: 'utf8', timeout: 90000 });
if (compilation.status !== 0) throw new Error(compilation.stderr || compilation.error?.message);
const run = spawnSync(binary, [join(output, 'probe.html'), join(output, 'surface.png')], { encoding: 'utf8', timeout: 40000 });
writeFileSync(join(output, 'receipt.json'), run.stdout || run.stderr);
writeFileSync(join(output, 'progress.log'), run.stderr);
console.log(output);
let receipt;
try { receipt = JSON.parse(run.stdout); }
catch { throw new Error(`Offline WebKit fixture exited without a JSON receipt. See ${output}: ${run.error?.message || run.stderr || run.stdout}`); }
if (run.status !== 0 || receipt.error || receipt.react_error || Object.values(receipt).some(value => value?.pass === false)) throw new Error(JSON.stringify(receipt));
console.log(`${Object.keys(receipt).length} offline submission WebKit checks passed`);
