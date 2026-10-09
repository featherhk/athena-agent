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
            try require(window.frame.width == 180 && window.frame.height == 170, "Launch did not begin with the small avatar surface")
            try await wait { await self.js("events.some(e=>e.action==='present')&&!events.some(e=>e.action==='show')") as? Bool == true }
            try invoke("toggleCompanion")
            try await wait { await self.js("events.filter(e=>e.action==='show').length===1") as? Bool == true }
            try require(window.isVisible, "Shortcut hid the collapsed owl instead of opening the input")
            await send(["action": "resize", "mode": "compact"])
            try await wait { self.window.frame.width == 560 && self.window.frame.height == 260 }
            passed.append("avatar-launch-presents-without-expanding-and-shortcut-opens-input-in-one-step")
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
            await send(["action": "resize", "mode": "compact", "height": 304])
            try await wait { self.window.frame.height == 304 }
            try require(window.frame.minX == compactFrame.minX && window.frame.minY == compactFrame.minY && window.frame.width == compactFrame.width,
                        "Multiline compact input moved its width or bottom anchor")
            await send(["action": "hide"])
            try await wait { !self.window.isVisible }
            try invoke("toggleCompanion")
            try await wait { self.window.isVisible }
            try require(window.frame.height == 304, "Recall discarded the measured multiline input height")
            await send(["action": "resize", "mode": "compact"])
            try await wait { self.window.frame == compactFrame }
            // This controlled hide was separate from the draft/voice continuity check below.
            _ = await js("backgroundCount=0")
            passed.append("compact-multiline-height-and-recall-retain-input-bottom-anchor")
            try await checkCompanionGestures()
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
            try await checkFileSurface(origin)
            try await checkFilePointerCancellation(origin)
            try await checkTransientSurfaces(origin)
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
    private func checkCompanionGestures() async throws {
        await send(["action": "companionDragRegions", "regions": [["x": 230, "y": 32, "width": 100, "height": 100]]])
        try await wait { self.host.fixtureCompanionGestureHit(NSPoint(x: 270, y: 65)) }
        try require(!host.fixtureCompanionGestureHit(NSPoint(x: 270, y: 220)), "Owl drag layer intercepted the text composer")
        passed.append("owl-parent-hit-routing-translates-top-left-regions-and-passes-composer-input")
        let origin = window.frame
        let pointer = NSPoint(x: origin.midX, y: origin.midY)
        host.fixtureCompanionGesture(from: pointer, to: NSPoint(x: pointer.x + 2, y: pointer.y))
        try await wait { await self.js("events.filter(e=>e.action==='avatar').length===1") as? Bool == true }
        try require(window.frame == origin, "A short owl click moved the window")
        host.fixtureCompanionGesture(from: pointer, to: NSPoint(x: pointer.x + 60, y: pointer.y + 32))
        let moved = window.frame
        try require(moved.origin != origin.origin && host.fixtureCompanionPlacement?["screen_id"] as? String != nil,
                    "Deliberate owl drag did not move and persist the companion")
        let saved = UserDefaults.standard.dictionary(forKey: DesktopCompanion.placementKey)
        try require(DesktopCompanion.anchor(saved, area: window.screen!.visibleFrame) != nil, "Owl location was not saved outside runtime state")
        let clicks = await js("events.filter(e=>e.action==='avatar').length") as? Int
        try require(clicks == 1, "Dragging the owl also toggled it")
        await send(["action": "resize", "mode": "avatar", "height": 170])
        try await wait { self.window.frame.width == 180 && self.window.frame.height == 170 }
        try require(abs(window.frame.midX - moved.midX) < 0.001 && abs(window.frame.minY - moved.minY) < 0.001,
                    "Collapsing avatar lost its chosen desktop anchor")
        await send(["action": "hide"])
        try await wait { !self.window.isVisible }
        try invoke("toggleCompanion")
        try await wait { self.window.isVisible }
        try require(window.frame.width == 180 && abs(window.frame.midX - moved.midX) < 0.001,
                    "Recall expanded or recentered the avatar")
        await send(["action": "resize", "mode": "compact"])
        try await wait { self.window.frame.width == 560 }
        try require(abs(window.frame.midX - moved.midX) < 0.001 && abs(window.frame.minY - moved.minY) < 0.001,
                    "Reopening composer moved the persistent owl anchor")
        host.fixtureCompanionGesture(from: pointer, to: NSPoint(x: pointer.x + 100000, y: pointer.y + 100000))
        try require(window.screen!.visibleFrame.contains(window.frame), "Owl drag could strand controls outside the display")
        let current = window.frame
        host.fixtureCompanionGesture(from: pointer, to: NSPoint(x: pointer.x + origin.minX - current.minX, y: pointer.y + origin.minY - current.minY))
        _ = await js("backgroundCount=0")
        passed.append("owl-click-drag-threshold-persistent-location-avatar-collapse-and-recall")
    }
    private func checkFileSurface(_ origin: URL) async throws {
        var item: [String: Any] = ["id": "file_surface", "version": 1, "view_revision": 1, "title": "Synthetic ledger.xlsx",
            "file_id": "file_ledger", "content_revision": "sha256-v1", "x": 0.0, "y": 0.0, "width": 860, "height": 540]
        let scene = host.fixtureScene
        func reconcile() { scene.reconcile(["scope": "owner:session_a", "session_id": "session_a", "items": [item]], origin: origin) }
        reconcile()
        try await wait { self.components().first?["state"] as? String == "mounted" }
        guard let child = scene.fixtureWebView(id: "file_surface"), let panel = child.window else { throw NSError(domain: "fixture", code: 8) }
        try require(panel.title == "Synthetic ledger.xlsx" && !panel.styleMask.contains(.titled) && panel.level.rawValue < window.level.rawValue,
                    "File work surface became a conversation window or covered the companion")
        let focus: [String: Any] = ["action": "focusSurface", "scope": "owner:session_a", "component_id": "file_surface", "version": 1,
            "file_id": "file_ledger", "content_revision": "sha256-v1"]
        window.makeKeyAndOrderFront(nil)
        let staleFocusRequests: [[String: Any]] = [["scope": "owner:stale_session"], ["version": 2], ["file_id": "wrong"], ["content_revision": "old"]]
        for invalid in staleFocusRequests {
            await send(focus.merging(invalid) { _, new in new })
            try require(!panel.isKeyWindow, "Stale file request stole keyboard focus")
        }
        await send(focus)
        try await wait { panel.isKeyWindow }
        try require(panel.firstResponder === child, "Exact file focus did not select its trusted WebKit view")
        window.makeKeyAndOrderFront(nil)
        passed.append("explicit-file-focus-requires-current-scope-file-version-and-mounted-surface")
        try require(child.url?.query?.contains("file=file_ledger") == true && components().first?["file_id"] as? String == "file_ledger"
                    && components().first?["content_revision"] as? String == "sha256-v1", "File content identity missing from child and observation")
        _ = await js("window.refreshes=[];window.stableDocument=61;addEventListener('athena:widget-refresh',event=>refreshes.push(event.detail))", in: child)
        await send(["action": "sceneRefresh", "scope": "owner:wrong_session"])
        await send(["action": "sceneRefresh", "scope": "owner:session_a"])
        try await wait { await self.js("refreshes.length===1&&refreshes[0].scope==='owner:session_a'&&refreshes[0].component_id==='file_surface'&&refreshes[0].version===1&&stableDocument===61", in: child) as? Bool == true }
        await send(["action": "surfaceDragRegions", "regions": [["x": 24, "y": 12, "width": 560, "height": 32]]], in: child)
        try await wait { scene.fixtureGestureHit(id: "file_surface", point: NSPoint(x: 50, y: 24)) }
        try require(!scene.fixtureGestureHit(id: "file_surface", point: NSPoint(x: 50, y: 200))
                    && !scene.fixtureGestureHit(id: "file_surface", point: NSPoint(x: 800, y: 24))
                    && scene.fixtureGestureHit(id: "file_surface", point: NSPoint(x: panel.frame.width - 5, y: panel.frame.height - 5)),
                    "Native drag regions obstructed cells/header controls or omitted resize grip")
        for point in [NSPoint(x: 50, y: 200), NSPoint(x: 800, y: 24),
                      NSPoint(x: 50, y: panel.frame.height - 24), NSPoint(x: panel.frame.width - 5, y: 5)] {
            try require(scene.fixtureGestureTarget(id: "file_surface", point: point) == "web",
                        "Parent hit routing captured table text/header buttons or mirrored title/grip vertically")
        }
        passed.append("file-parent-hit-routing-selects-title-and-bottom-grip-without-capturing-web-content")
        passed.append("file-work-surface-has-bound-identity-title-and-dedicated-native-gesture-regions")
        passed.append("parent-refresh-is-scoped-and-preserves-native-document")
        await send(["action": "widgetHealth", "state": "failed", "file_id": "wrong", "content_revision": "sha256-v1", "error_code": "preview_unavailable"], in: child)
        try require(components().first?["state"] as? String == "mounted", "Unbound file health changed mounted state")
        await send(["action": "widgetHealth", "state": "failed", "file_id": "file_ledger", "content_revision": "sha256-v1", "error_code": "preview_unavailable"], in: child)
        try await wait { self.components().first?["error_code"] as? String == "preview_unavailable" }
        await send(["action": "widgetHealth", "state": "loading", "file_id": "file_ledger", "content_revision": "sha256-v1"], in: child)
        try await wait { self.components().first?["state"] as? String == "loading" }
        await send(["action": "widgetHealth", "state": "mounted", "file_id": "file_ledger", "content_revision": "sha256-v1"], in: child)
        try await wait { self.components().first?["state"] as? String == "mounted" }
        passed.append("file-health-requires-current-content-and-retry-can-return-to-loading")
        let original = panel.frame
        let moved = original.offsetBy(dx: 31, dy: -27)
        let pointer = NSPoint(x: original.minX + 50, y: original.maxY - 24)
        for delta in [CGFloat(0), 2] {
            try require(scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDown, screenPoint: pointer), "Header pointer did not enter the native gesture through its parent")
            let end = NSPoint(x: pointer.x + delta, y: pointer.y)
            scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDragged, screenPoint: end)
            scene.fixturePointerEvent(id: "file_surface", type: .leftMouseUp, screenPoint: end)
        }
        let noOpEvents = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length") as? Int
        try require(panel.frame == original && noOpEvents == 0, "Click or pointer jitter changed file geometry or committed a layout")
        passed.append("file-title-click-and-subthreshold-jitter-do-not-persist-layout")
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDown, screenPoint: pointer)
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDragged, screenPoint: NSPoint(x: pointer.x + 15, y: pointer.y - 12))
        reconcile()
        try require(panel.frame == original.offsetBy(dx: 15, dy: -12), "An unchanged scene report snapped back the in-flight title drag")
        let endpoint = NSPoint(x: pointer.x + 31, y: pointer.y - 27)
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseUp, screenPoint: endpoint)
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseUp, screenPoint: endpoint)
        try await wait { await self.js("events.some(e=>e.action==='layout'&&e.component_id==='file_surface')") as? Bool == true }
        guard var event = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').at(-1)") as? [String: Any],
              let values = event["layout"] as? [String: Any] else { throw NSError(domain: "fixture", code: 9) }
        reconcile()
        try require(panel.frame == moved, "Pending drag snapped back before persistence acknowledgement")
        let pendingPointer = NSPoint(x: moved.minX + 50, y: moved.maxY - 24)
        try require(!scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDown, screenPoint: pendingPointer), "Pending persistence allowed a new drag")
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDragged, screenPoint: NSPoint(x: pendingPointer.x + 100, y: pendingPointer.y))
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseUp, screenPoint: pendingPointer)
        let oneDragEvent = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length") as? Int
        try require(panel.frame == moved && oneDragEvent == 1, "Pointer up duplicated the layout or pending persistence accepted a stale drag")
        if let area = panel.screen?.visibleFrame {
            let x = values["x"] as? Double ?? -1, y = values["y"] as? Double ?? -1
            try require(abs(x - (moved.minX - area.minX) / (area.width - moved.width)) < 0.000001
                        && abs(y - (area.maxY - moved.maxY) / (area.height - moved.height)) < 0.000001,
                        "Delivered title events persisted an inverted or offset screen position")
        }
        passed.append("file-title-drag-uses-event-screen-coordinates-and-commits-once-after-final-up")
        event["ok"] = true; event["view_revision"] = 2; event["action"] = "sceneResult"
        await send(event)
        try await wait { self.components().first?["view_revision"] as? Int == 2 }
        item.merge(values) { _, new in new }; item["view_revision"] = 2
        reconcile()
        try require(panel.frame == moved && components().first?["view_revision"] as? Int == 2, "Persisted drag did not retain canonical frame")
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDown, screenPoint: pendingPointer)
        scene.fixtureCommitFrame(id: "file_surface", frame: moved.offsetBy(dx: 23, dy: -11))
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseDragged, screenPoint: NSPoint(x: pendingPointer.x + 100, y: pendingPointer.y))
        scene.fixturePointerEvent(id: "file_surface", type: .leftMouseUp, screenPoint: pendingPointer)
        try require(panel.frame == moved.offsetBy(dx: 23, dy: -11), "A layout proposal did not cancel an in-progress title gesture")
        try await wait { await self.js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length===2") as? Bool == true }
        var rejected = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').at(-1)") as! [String: Any]
        rejected["ok"] = false; rejected["action"] = "sceneResult"; await send(rejected)
        try await wait { panel.frame == moved }
        try require(panel.frame == moved, "Failed layout persistence did not restore previous frame")
        passed.append("native-layout-retains-pending-frame-acknowledges-persistence-and-rolls-back-failure")
        let beforeResize = panel.frame
        scene.fixtureResize(id: "file_surface", by: NSPoint(x: 45, y: -29))
        try await wait { await self.js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length===3") as? Bool == true }
        try require(abs(panel.frame.width - beforeResize.width - 45) < 0.001
                    && abs(panel.frame.height - beforeResize.height - 29) < 0.001 && panel.frame.maxY == beforeResize.maxY,
                    "File resize ignored event coordinates when the global cursor did not move")
        var resized = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').at(-1)") as! [String: Any]
        resized["ok"] = false; resized["action"] = "sceneResult"; await send(resized)
        try await wait { panel.frame == beforeResize }
        passed.append("file-resize-uses-delivered-event-coordinates-without-moving-global-cursor")
        await send(["action": "maximizeWidget"], in: child)
        try await wait { await self.js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length===4") as? Bool == true }
        var maximized = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').at(-1)") as! [String: Any]
        try require(panel.frame == panel.screen?.visibleFrame, "Maximize clipped a file surface to the conversation")
        maximized["ok"] = true; maximized["view_revision"] = 3; maximized["action"] = "sceneResult"; await send(maximized)
        try await wait { self.components().first?["view_revision"] as? Int == 3 }
        item.merge(maximized["layout"] as! [String: Any]) { _, new in new }; item["view_revision"] = 3
        await send(["action": "downloadFile", "file_id": "forged", "content_revision": "forged"], in: child)
        try await wait { await self.js("events.some(e=>e.action==='download'&&e.file_id==='file_ledger'&&e.content_revision==='sha256-v1')") as? Bool == true }
        await send(["action": "closeWidget"], in: child)
        try await wait { await self.js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').length===5") as? Bool == true }
        var closed = await js("events.filter(e=>e.action==='layout'&&e.component_id==='file_surface').at(-1)") as! [String: Any]
        closed["ok"] = true; closed["view_revision"] = 4; closed["action"] = "sceneResult"; await send(closed)
        try await wait { self.components().first?["view_revision"] as? Int == 4 }
        item["hidden"] = true; item["view_revision"] = 4; reconcile()
        try require(!panel.isVisible && components().first?["state"] as? String == "hidden", "Closed surface revived after scene reconciliation")
        await send(focus)
        try require(!panel.isVisible && !panel.isKeyWindow, "Focusing a hidden file reopened a closed work surface")
        passed.append("file-maximize-download-and-close-route-identity-bound-actions-and-durable-view")
        item["hidden"] = false; item["view_revision"] = 5; item["content_revision"] = "sha256-v2"
        reconcile()
        try await wait { self.components().first?["state"] as? String == "mounted" }
        try require(scene.fixtureWebView(id: "file_surface") !== child, "Changed file bytes reused stale preview document")
        await send(["action": "downloadFile"], in: child)
        let downloads = await js("events.filter(e=>e.action==='download'&&e.file_id==='file_ledger').length") as? Int
        try require(downloads == 1, "Old content document retained file download capability")
        passed.append("file-content-revision-replaces-webview-and-revokes-old-document-actions")
        scene.closeAll()
    }
    private func checkFilePointerCancellation(_ origin: URL) async throws {
        let scene = host.fixtureScene, id = "pointer_surface"
        var item: [String: Any] = ["id": id, "version": 1, "view_revision": 1, "title": "Synthetic pointer file",
            "file_id": "file_pointer", "content_revision": "s1-pointer", "x": 0.2, "y": 0.2, "width": 500, "height": 320]
        func reconcile() { scene.reconcile(["scope": "owner:session_a", "session_id": "session_a", "items": [item]], origin: origin) }
        reconcile()
        try await wait { self.components().first?["state"] as? String == "mounted" }
        guard let child = scene.fixtureWebView(id: id), let panel = child.window, let area = panel.screen?.visibleFrame else {
            throw NSError(domain: "fixture", code: 11)
        }
        await send(["action": "surfaceDragRegions", "regions": [["x": 24, "y": 12, "width": 300, "height": 32]]], in: child)
        try await wait { scene.fixtureGestureHit(id: id, point: NSPoint(x: 50, y: 24)) }
        let original = panel.frame, pointer = NSPoint(x: panel.frame.minX + 50, y: panel.frame.maxY - 24)
        try require(!scene.fixturePointerEvent(id: id, type: .leftMouseDown, screenPoint: NSPoint(x: pointer.x, y: pointer.y - 180)),
                    "Table text started a native window gesture")
        try require(scene.fixturePointerEvent(id: id, type: .leftMouseDown, screenPoint: pointer), "Title did not accept pointer through parent routing")
        scene.fixturePointerEvent(id: id, type: .leftMouseUp, screenPoint: NSPoint(x: pointer.x + 100000, y: pointer.y - 100000))
        try await wait { await self.js("events.filter(e=>e.action==='layout'&&e.component_id==='pointer_surface').length===1") as? Bool == true }
        try require(area.contains(panel.frame) && panel.frame.maxX == area.maxX && panel.frame.minY == area.minY,
                    "Fast title drag did not clamp the entire work surface to the screen")
        var rejected = await js("events.filter(e=>e.action==='layout'&&e.component_id==='pointer_surface').at(-1)") as! [String: Any]
        rejected["ok"] = false; rejected["action"] = "sceneResult"; await send(rejected)
        try await wait { panel.frame == original }
        passed.append("file-fast-title-drag-clamps-to-visible-screen-and-cells-do-not-start-gestures")

        scene.fixturePointerEvent(id: id, type: .leftMouseDown, screenPoint: pointer)
        scene.fixturePointerEvent(id: id, type: .leftMouseDragged, screenPoint: NSPoint(x: pointer.x + 30, y: pointer.y - 20))
        scene.setPresented(false)
        scene.fixturePointerEvent(id: id, type: .leftMouseUp, screenPoint: NSPoint(x: pointer.x + 60, y: pointer.y - 40))
        scene.setPresented(true)
        try require(panel.frame == original, "Hiding did not cancel and restore an uncommitted title drag")
        scene.fixturePointerEvent(id: id, type: .leftMouseDown, screenPoint: pointer)
        scene.fixturePointerEvent(id: id, type: .leftMouseDragged, screenPoint: NSPoint(x: pointer.x + 30, y: pointer.y - 20))
        item["expires_at"] = ISO8601DateFormatter().string(from: Date().addingTimeInterval(-1))
        reconcile()
        scene.fixturePointerEvent(id: id, type: .leftMouseDragged, screenPoint: NSPoint(x: pointer.x + 90, y: pointer.y - 70))
        scene.fixturePointerEvent(id: id, type: .leftMouseUp, screenPoint: NSPoint(x: pointer.x + 90, y: pointer.y - 70))
        let layouts = await js("events.filter(e=>e.action==='layout'&&e.component_id==='pointer_surface').length") as? Int
        try require(!panel.isVisible && panel.frame == original && layouts == 1, "Expired or hidden tracking persisted a stale gesture")
        passed.append("file-hide-and-expiry-cancel-in-flight-gestures-without-stale-layout-commits")
        scene.closeAll()
    }
    private func checkTransientSurfaces(_ origin: URL) async throws {
        let scene = host.fixtureScene
        let formatter = ISO8601DateFormatter(); formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        func expiry(_ offset: TimeInterval) -> String { formatter.string(from: Date().addingTimeInterval(offset)) }
        func spec(_ id: String, _ version: Int = 1, expires: String? = nil) -> [String: Any] {
            var result: [String: Any] = ["id": id, "version": version, "view_revision": 1, "title": "Synthetic effect", "width": 180, "height": 140]
            if let expires { result["expires_at"] = expires }
            return result
        }
        func reconcile(_ specs: [[String: Any]]) { scene.reconcile(["scope": "owner:session_a", "session_id": "session_a", "items": specs], origin: origin) }
        func state(_ id: String) -> [String: Any]? { components().first { $0["id"] as? String == id } }
        let persistent: [String: Any] = ["id": "persistent_file", "version": 1, "view_revision": 1, "title": "Persistent ledger.xlsx",
            "file_id": "file_persistent", "content_revision": "s1-forever", "width": 620, "height": 400]
        let old = spec("expired_before_load", expires: expiry(-10))
        let beforeLoads = scene.fixtureDocumentLoads
        reconcile([old])
        try require(state("expired_before_load")?["state"] as? String == "hidden"
                    && state("expired_before_load")?["visible"] as? Bool == false && scene.fixtureDocumentLoads == beforeLoads,
                    "An already expired effect loaded or briefly appeared")
        try await wait { await self.js("events.some(e=>e.action==='componentEnded'&&e.component_id==='expired_before_load'&&e.version===1&&e.scope==='owner:session_a')") as? Bool == true }
        let expiredWeb = scene.fixtureWebView(id: "expired_before_load")
        var attemptedRevival = old; attemptedRevival["expires_at"] = expiry(60); attemptedRevival["hidden"] = false
        reconcile([attemptedRevival]); scene.setPresented(false); scene.setPresented(true)
        try require(state("expired_before_load")?["visible"] as? Bool == false && scene.fixtureWebView(id: "expired_before_load") === expiredWeb,
                    "The same expired version restarted during reconcile or recall")
        passed.append("already-expired-effect-never-loads-and-same-version-reconcile-cannot-revive-it")
        let timed = spec("short_effect", expires: expiry(0.9))
        reconcile([persistent, timed])
        try await wait { state("persistent_file")?["state"] as? String == "mounted" }
        let persistentWeb = scene.fixtureWebView(id: "persistent_file")
        let loads = scene.fixtureDocumentLoads
        for _ in 0..<12 { reconcile([persistent, timed]) }
        scene.setPresented(false)
        try await wait { state("short_effect")?["state"] as? String == "hidden" }
        try await wait { await self.js("events.filter(e=>e.action==='componentEnded'&&e.component_id==='short_effect'&&e.version===1).length===1") as? Bool == true }
        scene.setPresented(true)
        try require(state("short_effect")?["visible"] as? Bool == false && state("persistent_file")?["state"] as? String == "mounted"
                    && state("persistent_file")?["visible"] as? Bool == true && scene.fixtureWebView(id: "persistent_file") === persistentWeb
                    && scene.fixtureDocumentLoads == loads, "Native expiry affected a persistent file or scene refresh reloaded effects")
        reconcile([persistent, timed]); scene.setPresented(false); scene.setPresented(true)
        try require(state("short_effect")?["visible"] as? Bool == false, "An ended animation replayed on recall")
        passed.append("absolute-native-expiry-does-not-renew-on-scene-refresh-or-affect-persistent-files")
        let replaced = spec("replaced_effect", expires: expiry(0.35))
        reconcile([persistent, replaced])
        let stale = scene.fixtureWebView(id: "replaced_effect")
        let next = spec("replaced_effect", 2, expires: expiry(60))
        reconcile([persistent, next])
        try await wait { state("replaced_effect")?["state"] as? String == "mounted" }
        try await Task.sleep(for: .milliseconds(500))
        try require(state("replaced_effect")?["version"] as? Int == 2 && state("replaced_effect")?["visible"] as? Bool == true
                    && scene.fixtureWebView(id: "replaced_effect") !== stale, "A cancelled old expiry removed the replacement version")
        let endedReplacement = await js("events.some(e=>e.action==='componentEnded'&&e.component_id==='replaced_effect')") as? Bool
        try require(endedReplacement == false, "An old timer emitted an end receipt for a replacement")
        passed.append("replaced-component-version-invalidates-old-native-expiry-task")
        var invalid = next; invalid["expires_at"] = "not-a-date"
        reconcile([invalid])
        try require(state("persistent_file") != nil && state("replaced_effect")?["version"] as? Int == 2,
                    "Malformed expiry partially replaced a valid native scene")
        passed.append("invalid-expiry-rejects-the-whole-scene-without-changing-current-documents")
        scene.closeAll()
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
