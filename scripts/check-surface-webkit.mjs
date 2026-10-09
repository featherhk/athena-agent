// macOS-only, no network, no real account or existing browser profile.
import { build } from 'esbuild';
import { readFileSync, writeFileSync, readdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
if (process.platform !== 'darwin') throw new Error('This check requires macOS WebKit.');
const preview = process.argv.includes('--preview'), modal = process.argv.includes('--modal'), companion = process.argv.includes('--companion');
if (process.argv.slice(2).some(value => !['--preview', '--modal', '--companion'].includes(value)) || [preview, modal, companion].filter(Boolean).length > 1) throw new Error('Usage: check-surface-webkit.mjs [--preview | --modal | --companion]');
const root = resolve(import.meta.dirname, '..'), output = mkdtempSync(join(tmpdir(), preview ? 'athena-spreadsheet-webkit-' : modal ? 'athena-surface-modal-webkit-' : companion ? 'athena-companion-webkit-' : 'athena-surface-webkit-'));
const catalogs = preview ? `({'./locales/en.json':en,'./locales/ar.json':${readFileSync(join(root, 'src/locales/ar.json'), 'utf8')}})` : "({'./locales/en.json':en})";
const result = await build({ entryPoints: [join(root, preview ? 'tests/fixtures/spreadsheet-webkit-probe.tsx' : modal ? 'tests/fixtures/surface-modal-webkit-probe.tsx' : companion ? 'tests/fixtures/companion-webkit-probe.tsx' : 'tests/fixtures/surface-webkit-probe.tsx')], bundle: true, write: false,
  format: 'iife', minify: true, define: { 'process.env.NODE_ENV': '"production"' }, loader: { '.css': 'empty' },
  plugins: [{ name: 'offline-catalog', setup(build) { build.onLoad({ filter: /\/i18n.tsx$/ }, () => ({
    contents: readFileSync(join(root, 'src/i18n.tsx'), 'utf8').replace("import.meta.glob('./locales/*.json', { eager: true, import: 'default' })", catalogs), loader: 'tsx',
  })); } }],
});
const css = readdirSync(join(root, 'src')).filter(name => name.endsWith('.css')).map(name => readFileSync(join(root, 'src', name), 'utf8')).join('\n');
writeFileSync(join(output, 'probe.html'), `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'unsafe-inline';frame-src about:"><style>${css}</style><div id="root"></div><script>${result.outputFiles[0].text.replaceAll('</script', '<\\/script')}</script>`);
writeFileSync(join(output, 'Probe.swift'), `import AppKit
import WebKit
${companion ? `final class OfflineDocument: NSObject, WKURLSchemeHandler {
 func webView(_ webView:WKWebView,start task:WKURLSchemeTask) {
  let bytes = try! Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1]))
  task.didReceive(URLResponse(url:task.request.url!,mimeType:"text/html",expectedContentLength:bytes.count,textEncodingName:"utf-8"))
  task.didReceive(bytes); task.didFinish()
 }
 func webView(_ webView:WKWebView,stop task:WKURLSchemeTask) {}
}` : ''}
@MainActor final class Probe: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
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
  ${companion ? `config.userContentController.add(self,name:"athenaDesktop")
  config.setURLSchemeHandler(OfflineDocument(),forURLScheme:"athena-probe")
  config.userContentController.addUserScript(WKUserScript(source:"Object.defineProperty(window, '__ATHENA_DESKTOP__', {value:Object.freeze({version:1}),writable:false,configurable:false});",injectionTime:.atDocumentStart,forMainFrameOnly:true))` : ''}
  web = WKWebView(frame:NSRect(x:0,y:0,width:1080,height:780),configuration:config)
  window = NSWindow(contentRect:NSRect(x:0,y:0,width:1080,height:780),styleMask:[.titled,.closable],backing:.buffered,defer:false)
  window.contentView = web
  window.title = "Athena · Offline ${preview ? 'Spreadsheet' : modal ? 'Surface Modal' : companion ? 'Companion' : 'Surface'} Check"
  // Real WebKit visibility is required for IntersectionObserver and animation/timer delivery.
  // A hidden window plus a JavaScript document.hidden override is not an end-to-end fixture.
  window.center(); window.makeKeyAndOrderFront(nil); window.makeFirstResponder(web); NSApp.activate(ignoringOtherApps:true)
  ${companion ? 'web.load(URLRequest(url:URL(string:"athena-probe://offline/?desktop=companion")!))' : 'web.loadHTMLString(try! String(contentsOfFile:CommandLine.arguments[1],encoding:.utf8),baseURL:nil)'}
  Task { try? await Task.sleep(for:.seconds(${companion ? 55 : 20})); finish(["error":"native_timeout","last_progress":lastProgress]) }
 }
 func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) {
  if message.name == "athenaDesktop" {
   guard message.frameInfo.isMainFrame, JSONSerialization.isValidJSONObject(message.body), let data = try? JSONSerialization.data(withJSONObject:message.body,options:[.sortedKeys]), let encoded = String(data:data,encoding:.utf8) else { return }
   // The production App bridge runs; native scene effects are synthetic here.
   // Separate AppKit tests establish actual panel lifecycle and native trust.
   web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('probe-native-message',{detail:" + encoded + "}))",completionHandler:nil)
   return
  }
  // Synthetic native events are confined to this offline nonpersistent test window.
  if let body = message.body as? [String:Any], let point = body["nativeClick"] as? [String:Double], let x = point["x"], let y = point["y"] {
   window.makeKeyAndOrderFront(nil); window.makeFirstResponder(web); NSApp.activate(ignoringOtherApps:true)
   // Activation can settle asynchronously when another local test/app was
   // focused. Deliver the fixture click after that turn of the AppKit loop.
   DispatchQueue.main.asyncAfter(deadline:.now() + 0.06) {
    for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
     if let event = NSEvent.mouseEvent(with: type, location: NSPoint(x:x,y:self.web.bounds.height-y), modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: self.window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1) { self.window.sendEvent(event) }
    }
   }
   return
  }
  if let body = message.body as? [String:Any], let key = body["nativeKey"] as? String {
   let chars = key == "Enter" ? "\\r" : key == "Tab" ? "\\t" : key
   let code: UInt16 = key == "Enter" ? 36 : key == "Tab" ? 48 : 6
   let modifiers: NSEvent.ModifierFlags = body["shift"] as? Bool == true ? [.shift] : []
   if let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: modifiers, timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil, characters: chars, charactersIgnoringModifiers: chars, isARepeat: false, keyCode: code) { window.sendEvent(event) }
   return
  }
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
const run = spawnSync(binary, [join(output, 'probe.html'), join(output, 'surface.png')], { encoding: 'utf8', timeout: companion ? 60000 : 25000 });
writeFileSync(join(output, 'receipt.json'), run.stdout || run.stderr);
writeFileSync(join(output, 'progress.log'), run.stderr);
console.log(output);
let receipt;
try { receipt = JSON.parse(run.stdout); }
catch { throw new Error(`Offline WebKit fixture exited without a JSON receipt. See ${output}: ${run.error?.message || run.stderr || run.stdout}`); }
if (run.status !== 0 || receipt.error || receipt.react_error || Object.values(receipt).some(value => value?.pass === false)) throw new Error(JSON.stringify(receipt));
console.log(`${Object.values(receipt).filter(value => value?.pass === true).length} offline WebKit checks passed`);
