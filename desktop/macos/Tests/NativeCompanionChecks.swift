import AppKit
import Carbon
@preconcurrency import WebKit

@MainActor
final class PointerProbeView: NSView {
    var clicks = 0
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { clicks += 1 }
}

@MainActor
final class NativeCompanionChecks: NSObject, NSApplicationDelegate {
    private var host: AppDelegate!
    private var window: NSWindow!
    private var web: WKWebView!
    private var passed: [String] = []
    private var hotkeyStatus = "not-tested"
    private var pointerStatus = "not-tested"
    private var injected = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--fixture-origin"), index + 1 < arguments.count,
              let url = URL(string: arguments[index + 1]) else { exit(2) }
        // An existing preference from a previous launch must not make an explicit launch invisible.
        UserDefaults.standard.set(true, forKey: "AthenaCompanionHasLaunched")
        host = AppDelegate(); (window, web) = host.configureNativeFixture(url)
        if let index = arguments.firstIndex(of: "--download-destination"), index + 1 < arguments.count {
            host.fixtureDownloadDestination = URL(fileURLWithPath: arguments[index + 1])
        }
        if let index = arguments.firstIndex(of: "--integration-snapshot"), index + 1 < arguments.count {
            Task { await integration(arguments[index + 1]) }
        } else if let index = arguments.firstIndex(of: "--preview-snapshot"), index + 1 < arguments.count {
            Task { await preview(arguments[index + 1]) }
        } else { Task { await check(url) } }
    }
    private func require(_ condition: @autoclosure () -> Bool, _ description: String) throws {
        guard condition() else { throw NSError(domain: "macOS companion acceptance", code: 1, userInfo: [NSLocalizedDescriptionKey: description]) }
    }
    private func wait(_ condition: @escaping @MainActor () async -> Bool) async throws {
        for _ in 0..<200 { if await condition() { return }; try await Task.sleep(for: .milliseconds(50)) }
        throw NSError(domain: "macOS companion acceptance", code: 2, userInfo: [NSLocalizedDescriptionKey: "Timed out waiting for AppKit/WebKit after " + (passed.last ?? "start")])
    }
    private func js(_ source: String, in target: WKWebView? = nil) async -> Any? {
        await withCheckedContinuation { continuation in
            (target ?? web).evaluateJavaScript(source) { value, _ in continuation.resume(returning: value) }
        }
    }
    private func send(_ body: [String: Any], in target: WKWebView? = nil) async {
        let data = try! JSONSerialization.data(withJSONObject: body)
        _ = await js("webkit.messageHandlers.athenaDesktop.postMessage(\(String(data: data, encoding: .utf8)!))", in: target)
    }
    private func invoke(_ name: String) throws {
        guard let item = host.fixtureStatusMenu?.items.first(where: { $0.action.map(NSStringFromSelector) == name }) else {
            throw NSError(domain: "macOS companion acceptance", code: 3, userInfo: [NSLocalizedDescriptionKey: "Missing status menu " + name])
        }
        NSApp.sendAction(item.action!, to: item.target, from: item)
    }
    private func components() -> [[String: Any]] { host.fixtureScene.observation()["components"] as? [[String: Any]] ?? [] }
    private func check(_ origin: URL) async {
        do {
            try await wait { !self.web.isLoading && !self.web.isHidden && self.web.url == DesktopCompanion.home(origin) }
            let marker = await js("window.__ATHENA_DESKTOP__.version") as? Int
            try require(marker == 1 && window is NSPanel && !window.isOpaque && !window.styleMask.contains(.titled), "Companion was not a trusted transparent panel")
            passed.append("transparent-panel-native-marker-no-titlebar")
            try require(window.isVisible, "Explicit launch hid the companion after a previous launch")
            passed.append("explicit-launch-is-visible-after-a-previous-launch")
            let constrained = DesktopCompanion.frame(mode: .expanded, visibleFrame: NSRect(x: -400, y: -300, width: 320, height: 240))
            try require(NSRect(x: -400, y: -300, width: 320, height: 240).contains(constrained), "Panel exceeded small secondary screen")
            try require(!DesktopCompanion.trusts(frameIsMain: false, frameURL: origin, origin: origin, currentURL: origin), "Subframe trusted")
            try require(!DesktopCompanion.trusts(frameIsMain: true, frameURL: URL(string: "https://example.com"), origin: origin, currentURL: origin), "Remote origin trusted")
            passed.append("screen-clamping-and-origin-policy")
            let shortcut = CompanionShortcut {}
            if shortcut.register() {
                let collision = CompanionShortcut {}
                try require(!collision.register(), "Shortcut collision was ignored")
                collision.unregister(); shortcut.unregister(); hotkeyStatus = "registered-conflict-detected-released"
            } else { hotkeyStatus = "existing-system-conflict-detected-\(shortcut.registrationStatus)"; shortcut.unregister() }
            passed.append("global-hotkey-registration-or-explicit-conflict")
            try checkHotkeyDelivery()
            let compactFrame = window.frame
            await send(["action": "resize", "mode": "expanded"])
            try await wait { self.window.frame.height > compactFrame.height }
            try require(window.frame.width == compactFrame.width && window.frame.minX == compactFrame.minX
                && window.frame.minY == compactFrame.minY, "Expanding moved or widened the input's native surface")
            await send(["action": "resize", "mode": "expanded", "height": 360])
            try await wait { self.window.frame.width == 560 && self.window.frame.height == 360 }
            let resized = host.fixtureFrameChanges
            _ = await js("for(let i=0;i<30;i++)webkit.messageHandlers.athenaDesktop.postMessage({action:'resize',mode:'expanded',height:360})")
            try await Task.sleep(for: .milliseconds(150))
            try require(host.fixtureFrameChanges == resized, "Unchanged frontend measurements repeatedly resized the native window")
            await send(["action": "resize", "mode": "compact"])
            try await wait { self.window.frame == compactFrame }
            passed.append("expand-collapse-preserves-input-width-bottom-anchor-and-skips-unchanged-frames")
            _ = await js("window.frameMarker=null;addEventListener('message',e=>{if(e.data && 'marker' in e.data)frameMarker=e.data.marker});let f=document.createElement('iframe');f.sandbox='allow-scripts';f.srcdoc=\"<script>parent.postMessage({marker:!!window.__ATHENA_DESKTOP__},'*');webkit.messageHandlers.athenaDesktop.postMessage({action:'hide'});<\\/script>\";document.body.append(f)")
            try await wait { await self.js("window.frameMarker === false") as? Bool == true }
            try require(window.isVisible, "Sandbox iframe hid native panel")
            passed.append("sandbox-frame-has-no-marker-or-host-control")
            _ = await js("document.getElementById('draft').value='Keep my draft';window.proof=37")
            await send(["action": "hide"])
            try await wait { !self.window.isVisible }
            try require(!DesktopHitRegions.shared.fixtureTimerActive, "Hidden companion kept a 20Hz pointer timer running")
            let backgrounds = await js("backgroundCount") as? Int
            try require(backgrounds == 1, "Hiding did not notify voice capture")
            try invoke("toggleCompanion")
            try await wait { self.window.isVisible }
            try require(DesktopHitRegions.shared.fixtureTimerActive, "Recalling companion did not restore pointer routing")
            let draft = await js("document.getElementById('draft').value") as? String
            let proof = await js("window.proof") as? Int
            try require(draft == "Keep my draft" && proof == 37, "Hide/show reloaded the document or draft")
            passed.append("hide-show-preserves-document-draft-and-sends-background")
            passed.append("all-hidden-suspends-pointer-timer-and-recall-restores-it")
            try await closeAndReopen()
            _ = await js("document.getElementById('download').click()")
            try await wait { self.host.fixtureCompletedDownloads == 1 }
            let bytes = try Data(contentsOf: host.fixtureDownloadDestination!)
            let afterDownload = await js("document.getElementById('draft').value") as? String
            try require(String(data: bytes, encoding: .utf8) == "Synthetic authenticated download\n"
                && web.url == DesktopCompanion.home(origin) && afterDownload == "Keep my draft", "Attachment download replaced draft or changed original bytes")
            try require(!DesktopCompanion.isFileContent(URL(string: "http://example.com/api/files/id/content"), origin: origin), "Remote attachment trusted")
            passed.append("inline-blank-target-download-retains-session-document-draft-and-bytes")
            try invoke("stopWork")
            try await wait { await self.js("events.some(e=>e.action==='stop')") as? Bool == true }
            try invoke("showHistory")
            try await wait { await self.js("events.some(e=>e.action==='history')") as? Bool == true }
            try invoke("showSettings")
            try await wait { await self.js("events.some(e=>e.action==='menu')") as? Bool == true }
            passed.append("native-stop-history-settings-route-to-main-document")
            try await checkMainMenu()
            var item: [String: Any] = ["id": "specimen", "version": 1, "view_revision": 1, "title": "Offline widget", "x": 1.0, "y": 1.0, "width": 320, "height": 240]
            await send(["action": "scene", "scope": "owner:session_a", "session_id": "session_a", "items": [item]])
            try await wait { self.components().first?["state"] as? String == "mounted" }
            guard let child = host.fixtureScene.fixtureWebView(id: "specimen") else { throw NSError(domain: "fixture", code: 4) }
            let rect = components().first?["rect"] as? [String: CGFloat]
            try require(rect?["width"] == 320 && rect?["height"] == 240, "Wrong native component dimensions")
            let frameChanges = host.fixtureScene.fixtureFrameChanges
            let orderChanges = host.fixtureScene.fixtureOrderChanges
            let stateReports = host.fixtureScene.fixtureStateReports
            for _ in 0..<30 {
                host.fixtureScene.reconcile(["scope": "owner:session_a", "session_id": "session_a", "items": [item]], origin: origin)
                host.fixtureScene.setPresented(true)
            }
            try require(host.fixtureScene.fixtureFrameChanges == frameChanges
                && host.fixtureScene.fixtureOrderChanges == orderChanges
                && host.fixtureScene.fixtureStateReports == stateReports,
                "Unchanged scene repeatedly resized, raised or reported its panels")
            passed.append("unchanged-scene-keeps-window-order-frame-and-bridge-quiet")
            _ = await js("window.proof=83", in: child)
            item["view_revision"] = 2; item["x"] = 0.0
            await send(["action": "scene", "scope": "owner:session_a", "session_id": "session_a", "items": [item]])
            try await wait { self.components().first?["view_revision"] as? Int == 2 }
            let childProof = await js("window.proof", in: child) as? Int
            try require(childProof == 83, "Moving component reloaded its state")
            passed.append("desktop-widget-mounted-and-layout-update-preserves-document")
            host.fixtureScene.reconcile(["scope": "owner:session_other", "session_id": "session_a", "items": []], origin: origin)
            try require(components().count == 1 && host.fixtureScene.fixtureWebView(id: "specimen") === child, "Malformed scope replaced the current scene")
            try require(DesktopSceneHost.identifier(String(repeating: "a", count: 65)) == nil, "Overlong identifier accepted")
            passed.append("invalid-scene-is-atomic-and-keeps-current-panels")
            await send(["action": "submit", "text": "Synthetic widget request", "component_id": "forged", "version": 999], in: child)
            try await wait { await self.js("events.some(e=>e.action==='submit'&&e.component_id==='specimen'&&e.version===1&&e.scope==='owner:session_a')") as? Bool == true }
            passed.append("child-interaction-identity-bound-by-native-view")
            await send(["action": "hide"])
            try await wait { !self.window.isVisible && self.components().first?["visible"] as? Bool == false }
            try require(!DesktopHitRegions.shared.fixtureTimerActive, "Hidden desktop scene kept the pointer timer awake")
            try invoke("toggleCompanion")
            try await wait { self.components().first?["visible"] as? Bool == true }
            let unchanged = await js("window.proof", in: child) as? Int
            try require(unchanged == 83, "Hiding destroyed desktop widget state")
            passed.append("hide-recall-retains-widget-state")
            item["version"] = 2
            await send(["action": "scene", "scope": "owner:session_a", "session_id": "session_a", "items": [item]])
            try await wait { self.components().first?["version"] as? Int == 2 && self.components().first?["state"] as? String == "mounted" }
            try require(host.fixtureScene.fixtureWebView(id: "specimen") !== child, "New version reused stale identity")
            await send(["action": "submit", "text": "Stale version must not act"], in: child)
            let stale = await js("events.some(e=>e.text==='Stale version must not act')") as? Bool
            try require(stale == false, "Old document acted as new version")
            passed.append("version-replacement-invalidates-stale-native-bridge")
            await send(["action": "hitRegions", "regions": []])
            try await wait { self.window.ignoresMouseEvents }
            let bounds = NSRect(x: 0, y: 0, width: 560, height: 260)
            try require(DesktopHitRegions.accepts(point: NSPoint(x: 20, y: 20), regions: [NSRect(x: 0, y: 0, width: 40, height: 40)], bounds: bounds), "Visible control rejected pointer")
            try require(!DesktopHitRegions.accepts(point: NSPoint(x: 50, y: 50), regions: [NSRect(x: 0, y: 0, width: 40, height: 40)], bounds: bounds), "Transparent hole captured pointer")
            passed.append("transparent-hit-map-routes-native-window-and-control-geometry")
            try await pointerRouting()
            try invoke("offerRecovery")
            try await wait { self.window.attachedSheet != nil }
            await send(["action": "hide"])
            try require(window.isVisible, "Shortcut hid a native modal sheet")
            window.endSheet(window.attachedSheet!, returnCode: .alertSecondButtonReturn)
            try await wait { self.window.attachedSheet == nil }
            try require(components().count == 1, "Cancelling recovery removed widgets")
            passed.append("native-sheet-stays-reachable-and-cancel-preserves-scene")
            await send(["action": "scene", "scope": "", "session_id": "", "items": []])
            try await wait { self.components().isEmpty }
            passed.append("signout-empty-scene-removes-old-account-panels")
            // A screen-sized visual is a distinct native surface, not clipped to the companion.
            item["width"] = 16384; item["height"] = 16384
            await send(["action": "scene", "scope": "owner:session_a", "session_id": "session_a", "items": [item]])
            try await wait { self.components().first?["state"] as? String == "mounted" }
            guard let full = components().first, let screenID = full["screen_id"] as? String,
                  let screens = host.fixtureScene.observation()["screens"] as? [[String: Any]],
                  let area = screens.first(where: { $0["id"] as? String == screenID })?["visible_frame"] as? [String: CGFloat],
                  let actual = full["rect"] as? [String: CGFloat], let canvas = host.fixtureScene.fixtureWebView(id: "specimen") else {
                throw NSError(domain: "macOS companion acceptance", code: 7, userInfo: [NSLocalizedDescriptionKey: "Missing full-screen native surface"])
            }
            try require(actual["width"] == area["width"] && actual["height"] == area["height"] && actual["x"] == 0 && actual["y"] == 0,
                        "Screen-sized component was clipped to the conversation panel")
            await send(["action": "hitRegions", "regions": []], in: canvas)
            try await wait { canvas.window?.ignoresMouseEvents == true }
            passed.append("screen-sized-surface-covers-desktop-work-area-and-empty-hit-map-passes-input")
            try invoke("offerRecovery")
            try await wait { self.window.attachedSheet != nil }
            window.endSheet(window.attachedSheet!, returnCode: .alertFirstButtonReturn)
            try await wait { self.components().isEmpty && !self.web.isLoading && self.web.url?.query == "desktop=companion&recovery=1" }
            passed.append("native-reset-closes-scene-and-loads-default-without-mutation")
            finish(nil)
        } catch { finish(error) }
    }
    private func checkMainMenu() async throws {
        let menus = NSApp.mainMenu?.items.compactMap(\.submenu) ?? []
        guard let view = menus.first(where: { $0.items.contains(where: { $0.action.map(NSStringFromSelector) == "reload" }) }) else {
            throw NSError(domain: "macOS companion acceptance", code: 5, userInfo: [NSLocalizedDescriptionKey: "Missing native View menu"])
        }
        for action in ["toggleCompanion", "showHistory", "showSettings", "stopWork", "offerRecovery"] {
            try require(view.items.contains(where: { $0.action.map(NSStringFromSelector) == action }), "View menu cannot perform " + action)
        }
        guard let new = view.items.first(where: { $0.action.map(NSStringFromSelector) == "newConversation" }) else {
            throw NSError(domain: "macOS companion acceptance", code: 6, userInfo: [NSLocalizedDescriptionKey: "Missing new-conversation action"])
        }
        try require(new.keyEquivalent == "n" && new.keyEquivalentModifierMask == .command, "New conversation is not available through Command-N")
        NSApp.sendAction(new.action!, to: new.target, from: new)
        try await wait { await self.js("events.filter(e=>e.action==='new').length === 1") as? Bool == true }
        let draft = await js("document.getElementById('draft').value") as? String
        try require(draft == "Keep my draft", "Native new conversation mutated the draft before the frontend accepted it")
        passed.append("view-menu-controls-and-command-n-deliver-one-new-conversation-event")
    }
    private func checkHotkeyDelivery() throws {
        var actions = 0
        var diagnostics: [(String, [String: Int])] = []
        let shortcut = CompanionShortcut(diagnostic: { diagnostics.append(($0, $1)) }) { actions += 1 }
        try require(shortcut.fixtureInstallHandler() == noErr, "Could not install isolated shortcut event handler")
        defer { shortcut.unregister() }
        func dispatch(_ id: UInt32) throws -> OSStatus {
            var event: EventRef?
            try require(CreateEvent(nil, OSType(kEventClassKeyboard), UInt32(kEventHotKeyPressed), 0, 0, &event) == noErr && event != nil,
                        "Could not create isolated Carbon event")
            defer { ReleaseEvent(event!) }
            var identifier = EventHotKeyID(signature: 0x4154484E, id: id)
            try require(SetEventParameter(event!, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                MemoryLayout<EventHotKeyID>.size, &identifier) == noErr, "Could not set isolated shortcut identity")
            return SendEventToEventTarget(event!, GetApplicationEventTarget())
        }
        _ = try dispatch(999)
        try require(actions == 0 && shortcut.deliveryCount == 0 && diagnostics.isEmpty, "Unrelated shortcut event triggered Athena")
        let delivered = try dispatch(1)
        try require(delivered == noErr && actions == 1 && shortcut.deliveryCount == 1
            && diagnostics.last?.0 == "shortcut-delivered" && diagnostics.last?.1["count"] == 1, "Shortcut event did not reach its action or local diagnostics")
        passed.append("carbon-handler-delivers-matching-event-once-with-local-diagnostics")
    }
    private func closeAndReopen() async throws {
        // Guard the old invalid-window crash during Close followed by Reopen.
        // The production companion is borderless; enable Close only in this
        // fixture so performClose must exercise the real NSWindowDelegate path.
        let originalStyle = window.styleMask
        defer { window.styleMask = originalStyle }
        window.styleMask.formUnion([.titled, .closable])
        try require(!window.isReleasedWhenClosed && window.delegate === host,
                    "Companion must retain its window and intercept native Close")
        let originalWindow = ObjectIdentifier(window!)
        let originalWeb = ObjectIdentifier(web!)
        let backgrounds = await js("backgroundCount") as? Int ?? -1
        for cycle in 1...5 {
            window.performClose(nil)
            try require(!window.isVisible, "performClose did not reach the companion close handler")
            try require(!DesktopHitRegions.shared.fixtureTimerActive,
                        "Native Close left pointer tracking running")
            try await wait { await self.js("backgroundCount") as? Int == backgrounds + cycle }
            let reopened = host.applicationShouldHandleReopen(NSApp, hasVisibleWindows: false)
            try await wait { self.window.isVisible }
            let draft = await js("document.getElementById('draft').value") as? String
            let proof = await js("window.proof") as? Int
            try require(reopened && ObjectIdentifier(window!) == originalWindow
                        && ObjectIdentifier(web!) == originalWeb && web.window === window
                        && window.contentView?.subviews.contains(where: { $0 === web }) == true
                        && draft == "Keep my draft" && proof == 37,
                        "Native Close/Reopen replaced the window, WebView or draft on cycle \(cycle)")
        }
        passed.append("native-perform-close-reopen-five-cycles-retain-window-webview-document-draft")
    }
    private func pointerRouting() async throws {
        guard CGPreflightPostEventAccess() else { pointerStatus = "not-tested-os-post-event-access-unavailable"; return }
        let probe = PointerProbeView(frame: NSRect(origin: .zero, size: window.frame.size))
        let underneath = NSPanel(contentRect: window.frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        underneath.isReleasedWhenClosed = false; underneath.level = .floating
        underneath.contentView = probe; underneath.backgroundColor = NSColor(calibratedWhite: 0.95, alpha: 1)
        underneath.orderFront(nil); window.order(.above, relativeTo: underneath.windowNumber)
        let original = NSEvent.mouseLocation
        let point = NSPoint(x: window.frame.midX, y: window.frame.minY + 30)
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        let cgPoint = CGPoint(x: point.x, y: top - point.y)
        defer {
            underneath.orderOut(nil); underneath.close()
            if injected { CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: CGPoint(x: original.x, y: top - original.y), mouseButton: .left)?.post(tap: .cghidEventTap) }
            DesktopHitRegions.shared.reset(window)
        }
        func ownedAtPoint() -> Bool {
            let number = NSWindow.windowNumber(at: point, belowWindowWithWindowNumber: 0)
            return number == window.windowNumber || number == underneath.windowNumber
        }
        guard ownedAtPoint() else { pointerStatus = "not-tested-another-window-covers-fixture"; return }
        _ = await js("window.pointerProof=0;addEventListener('mousedown',e=>{if(e.isTrusted)pointerProof++})")
        CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: cgPoint, mouseButton: .left)?.post(tap: .cghidEventTap)
        injected = true
        try await Task.sleep(for: .milliseconds(150))
        guard ownedAtPoint() else { pointerStatus = "not-tested-another-window-entered-fixture"; return }
        DesktopHitRegions.shared.update(window, payload: [])
        try require(window.ignoresMouseEvents, "Empty hit map did not release pointer routing")
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: cgPoint, mouseButton: .left)?.post(tap: .cghidEventTap)
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: cgPoint, mouseButton: .left)?.post(tap: .cghidEventTap)
        try await wait { probe.clicks == 1 }
        let first = await js("pointerProof") as? Int
        try require(first == 0, "Transparent main panel consumed the underlying click")
        DesktopHitRegions.shared.update(window, payload: [["x": 0, "y": 0, "width": window.frame.width, "height": window.frame.height]])
        window.order(.above, relativeTo: underneath.windowNumber)
        try await Task.sleep(for: .milliseconds(100))
        guard ownedAtPoint() else { pointerStatus = "not-tested-another-window-entered-fixture"; return }
        try require(!window.ignoresMouseEvents, "Interactive hit map did not recapture pointer")
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: cgPoint, mouseButton: .left)?.post(tap: .cghidEventTap)
        CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: cgPoint, mouseButton: .left)?.post(tap: .cghidEventTap)
        try await wait { await self.js("pointerProof === 1") as? Bool == true }
        try require(probe.clicks == 1, "Interactive click leaked into underlying window")
        pointerStatus = "verified-windowserver-routing-between-two-owned-fixture-windows"
        passed.append("real-pointer-passes-transparent-region-and-stays-in-interactive-region")
    }
    private func integration(_ path: String) async {
        do {
            try await wait { !self.web.isLoading && !self.web.isHidden && self.components().first?["state"] as? String == "mounted" }
            guard let child = host.fixtureScene.fixtureWebView(id: "specimen") else { throw NSError(domain: "integration", code: 1) }
            try require(host.fixtureScene.observationScope == "fixture_owner:session_a", "Native state scope lost owner/session")
            let role = await js("document.documentElement.dataset.desktop", in: child) as? String
            let transparent = await js("getComputedStyle(document.body).backgroundColor", in: child) as? String
            try require(role == "widget" && transparent == "rgba(0, 0, 0, 0)", "Production widget document is not transparent")
            passed.append("production-main-app-registers-native-scene-and-real-widget-mounts")
            _ = await js("window.fixtureReceipts=[];addEventListener('message',e=>{if(e.data?.fixtureReceipt)fixtureReceipts.push(e.data.fixtureReceipt)});document.querySelector('iframe').contentWindow.postMessage({fixture:'draft',value:'Preserved widget draft'},'*')", in: child)
            try await wait { await self.js("fixtureReceipts.at(-1)?.draft === 'Preserved widget draft'", in: child) as? Bool == true }
            let before = await js("fixtureReceipts.at(-1)", in: child) as? [String: Any]
            _ = await js("fetch('/__fixture/layout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})")
            try await wait { self.components().first?["view_revision"] as? Int == 2 }
            try require(host.fixtureScene.fixtureWebView(id: "specimen") === child, "Layout update replaced native view")
            _ = await js("document.querySelector('iframe').contentWindow.postMessage({fixture:'read'},'*')", in: child)
            try await wait { await self.js("fixtureReceipts.length >= 2", in: child) as? Bool == true }
            let after = await js("fixtureReceipts.at(-1)", in: child) as? [String: Any]
            try require(after?["draft"] as? String == "Preserved widget draft" && after?["proof"] as? Double == before?["proof"] as? Double
                && after?["start"] as? Int == 2 && after?["end"] as? Int == 6, "Production widget lost draft, document or selection")
            passed.append("production-sse-layout-update-preserves-iframe-document-draft-selection")
            _ = await js("document.querySelector('iframe').contentWindow.postMessage({fixture:'submit'},'*')", in: child)
            _ = await js("window.integrationReceipt=null;fetch('/__fixture/receipt').then(r=>r.json()).then(r=>integrationReceipt=r)")
            try await wait {
                _ = await self.js("fetch('/__fixture/receipt').then(r=>r.json()).then(r=>integrationReceipt=r)")
                return await self.js("integrationReceipt?.submissions.length === 1") as? Bool == true
            }
            let sent = await js("integrationReceipt.submissions[0]") as? [String: Any]
            try require(sent?["text"] as? String == "Synthetic desktop widget request" && sent?["client_submission_id"] is String
                && sent?["frontend_client_id"] is String, "Component submission skipped production intent or observation binding")
            passed.append("production-widget-submit-uses-parent-intent-observation-and-one-receipt")
            try await snapshot(path)
            try await snapshot(path.replacingOccurrences(of: ".png", with: "-widget.png"), target: child)
            finish(nil)
        } catch { finish(error) }
    }
    private func snapshot(_ path: String, target: WKWebView? = nil) async throws {
        let image: NSImage = try await withCheckedThrowingContinuation { continuation in
            (target ?? web).takeSnapshot(with: nil) { image, error in
                if let image { continuation.resume(returning: image) }
                else { continuation.resume(throwing: error ?? NSError(domain: "snapshot", code: 1)) }
            }
        }
        guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff), let png = bitmap.representation(using: .png, properties: [:]) else { throw NSError(domain: "snapshot", code: 2) }
        try png.write(to: URL(fileURLWithPath: path), options: .atomic)
    }
    private func preview(_ path: String) async {
        do {
            try await wait { !self.web.isLoading && !self.web.isHidden }
            try await wait { await self.js("!!document.querySelector('.desktop-companion textarea')") as? Bool == true }
            try await Task.sleep(for: .milliseconds(600))
            try await snapshot(path)
            passed.append("production-dist-in-native-companion-snapshot")
            try invoke("showSettings")
            try await wait { await self.js("!!document.querySelector('[aria-modal=true]')") as? Bool == true }
            try await Task.sleep(for: .milliseconds(300))
            try await snapshot(path.replacingOccurrences(of: ".png", with: "-settings.png"))
            passed.append("native-menu-opens-production-settings")
            finish(nil)
        } catch { finish(error) }
    }
    private func awaitValue(_ value: Any?) -> Int? { value as? Int }
    private func finish(_ error: Error?) {
        let receipt: [String: Any] = ["ok": error == nil, "passed": passed, "error": error?.localizedDescription ?? "",
            "hotkey": hotkeyStatus, "pointer_routing": pointerStatus, "real_backend_started": false, "mouse_events_injected": injected,
            "limits": ["Pointer routing is tested only between owned fixture windows when OS event-posting access is already granted. Third-party apps are not clicked.", "Stop verifies trusted event delivery; it cannot cancel a task when WebKit is unresponsive."]]
        let data = try! JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys])
        print("ATHENA_NATIVE_CHECK_RESULT " + String(data: data, encoding: .utf8)!); fflush(stdout)
        exit(error == nil ? 0 : 1)
    }
}

@main
struct NativeCompanionMain {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = NativeCompanionChecks(); app.delegate = delegate
        app.setActivationPolicy(.accessory); app.run()
    }
}
