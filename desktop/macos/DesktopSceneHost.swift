import AppKit
@preconcurrency import WebKit

/// Only the trusted document's title region and resize grip capture native gestures.
/// Table cells, links and text retain ordinary WebKit pointer/selection behavior.
@MainActor
private final class SurfaceGestureView: NSView {
    var dragRegions: [NSRect] = []
    var enabled = true
    var completed: (() -> Void)?
    private var resizeStart: (NSRect, NSPoint)?
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    private var resizeGrip: NSRect { NSRect(x: max(0, bounds.width - 20), y: max(0, bounds.height - 20), width: 20, height: 20) }
    override func hitTest(_ point: NSPoint) -> NSView? {
        guard enabled, !isHidden, bounds.contains(point), resizeGrip.contains(point) || dragRegions.contains(where: { $0.contains(point) }) else { return nil }
        return self
    }
    override func mouseDown(with event: NSEvent) {
        guard enabled, let window else { return }
        if resizeGrip.contains(convert(event.locationInWindow, from: nil)) {
            resizeStart = (window.frame, window.convertPoint(toScreen: event.locationInWindow))
        } else {
            window.performDrag(with: event)
            completed?()
        }
    }
    override func mouseDragged(with event: NSEvent) {
        guard let (start, pointer) = resizeStart, let window else { return }
        let now = window.convertPoint(toScreen: event.locationInWindow), area = window.screen?.visibleFrame ?? start
        let width = min(area.maxX - start.minX, max(min(320, area.width), start.width + now.x - pointer.x))
        let height = min(start.maxY - area.minY, max(min(240, area.height), start.height - now.y + pointer.y))
        window.setFrame(NSRect(x: start.minX, y: start.maxY - height, width: width, height: height), display: true)
    }
    override func mouseUp(with event: NSEvent) {
        guard resizeStart != nil else { return }
        mouseDragged(with: event)
        resizeStart = nil; completed?()
    }
    #if ATHENA_NATIVE_CHECKS
    func fixtureResize(by delta: NSPoint) {
        guard let window else { return }
        let from = window.convertPoint(toScreen: convert(NSPoint(x: bounds.width - 5, y: bounds.height - 5), to: nil))
        let to = NSPoint(x: from.x + delta.x, y: from.y + delta.y)
        func event(_ type: NSEvent.EventType, _ point: NSPoint) -> NSEvent {
            NSEvent.mouseEvent(with: type, location: window.convertPoint(fromScreen: point), modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil,
                eventNumber: 1, clickCount: 1, pressure: type == .leftMouseUp ? 0 : 1)!
        }
        mouseDown(with: event(.leftMouseDown, from))
        mouseDragged(with: event(.leftMouseDragged, to))
        mouseUp(with: event(.leftMouseUp, to))
    }
    #endif
}

/// Agent-authored pages live in separate panels. Their main documents relay a small,
/// identity-bound protocol; generated sandbox frames never gain native capabilities.
@MainActor
final class DesktopSceneHost: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    private final class Item {
        let id: String
        var version: Int
        var viewRevision: Int
        var spec: [String: Any]
        let panel: CompanionPanel
        let web: WKWebView
        var gestures: SurfaceGestureView?
        var restoreFrame: NSRect?
        var reportedMaximized: Bool?
        var pendingLayout: (id: String, revision: Int, previous: [String: Any])?
        var expiresAt: Date?
        var expiryTask: Task<Void, Never>?
        var expired = false
        var state = "loading"
        var error: String?
        var loadToken = UUID()
        init(id: String, version: Int, viewRevision: Int, spec: [String: Any], panel: CompanionPanel, web: WKWebView) {
            self.id = id; self.version = version; self.viewRevision = viewRevision
            self.spec = spec; self.panel = panel; self.web = web
        }
    }
    private var items: [String: Item] = [:]
    private var scope: String?
    var observationScope: String { scope ?? "" }
    private var sessionID: String?
    private var origin: URL?
    private var presented = false
    private var publishedState: [String: Any]?
    private var publishedScope: String?
    var onState: (([String: Any]) -> Void)?
    var onInteraction: (([String: Any]) -> Void)?

    static func identifier(_ value: Any?) -> String? {
        guard let text = value as? String, !text.isEmpty, text.count <= 64,
              text.range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil else { return nil }
        return text
    }
    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue.rounded() == number.doubleValue,
              number.doubleValue >= 0, number.doubleValue <= Double(Int32.max) else { return nil }
        return number.intValue
    }
    private static func number(_ value: Any?, fallback: Double) -> CGFloat {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite else { return fallback }
        return number.doubleValue
    }
    private static func expiration(_ value: Any?) -> Date? {
        guard let text = value as? String, text.count <= 64 else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text)
    }
    private static func screenID(_ screen: NSScreen) -> String {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.stringValue ?? "0"
    }
    private func screen(_ spec: [String: Any]) -> NSScreen? {
        NSScreen.screens.first { Self.screenID($0) == spec["screen_id"] as? String } ?? NSScreen.main ?? NSScreen.screens.first
    }
    private func frame(_ spec: [String: Any], screen: NSScreen) -> NSRect {
        let area = screen.visibleFrame
        let file = spec["file_id"] != nil
        let width = min(area.width, max(file ? 320 : 32, min(16384, Self.number(spec["width"], fallback: file ? 880 : 320))))
        let height = min(area.height, max(file ? 240 : 32, min(16384, Self.number(spec["height"], fallback: file ? 620 : 240))))
        let x = min(1, max(0, Self.number(spec["x"], fallback: 0.75)))
        let y = min(1, max(0, Self.number(spec["y"], fallback: 0.2)))
        return NSRect(x: area.minX + x * (area.width - width), y: area.maxY - height - y * (area.height - height), width: width, height: height)
    }

    func reconcile(_ body: [String: Any], origin: URL) {
        if body["scope"] as? String == "", body["session_id"] as? String == "", let specs = body["items"] as? [Any], specs.isEmpty {
            closeAll(); return
        }
        guard let session = Self.identifier(body["session_id"]), let requestedScope = body["scope"] as? String,
              requestedScope.count <= 129, requestedScope.split(separator: ":", omittingEmptySubsequences: false).count == 2,
              requestedScope.split(separator: ":", omittingEmptySubsequences: false).allSatisfy({ Self.identifier(String($0)) != nil }),
              requestedScope.hasSuffix(":" + session), let specs = body["items"] as? [[String: Any]], specs.count <= 32 else { return }
        // Validate the entire scene before replacing a working one.
        var parsed: [(String, Int, Int, [String: Any])] = []
        var seen = Set<String>()
        for spec in specs {
            guard let id = Self.identifier(spec["id"]), let version = Self.integer(spec["version"]), version > 0,
                  let revision = Self.integer(spec["view_revision"]), seen.insert(id).inserted else { return }
            if spec["file_id"] != nil {
                guard Self.identifier(spec["file_id"]) != nil, let content = spec["content_revision"] as? String,
                      !content.isEmpty, content.count <= 128 else { return }
            } else if spec["content_revision"] != nil { return }
            if let expiry = spec["expires_at"], !(expiry is NSNull), Self.expiration(expiry) == nil { return }
            parsed.append((id, version, revision, spec))
        }
        if self.scope != requestedScope || self.origin != origin { closeAll() }
        self.scope = requestedScope; sessionID = session; self.origin = origin
        for id in Array(items.keys) where !seen.contains(id) { remove(id) }
        for (id, version, revision, spec) in parsed {
            if let item = items[id] {
                if item.version != version || item.spec["file_id"] as? String != spec["file_id"] as? String
                    || item.spec["content_revision"] as? String != spec["content_revision"] as? String {
                    // Never rebind an old document to a new component version. Late script or
                    // navigation callbacks from its old WebKit view must have no bound identity.
                    remove(id)
                    create(id: id, version: version, revision: revision, spec: spec)
                } else {
                    if let pending = item.pendingLayout, revision <= pending.revision {
                        // An unrelated state report can reconcile the pre-drag scene before
                        // persistence finishes. Do not snap the work surface back meanwhile.
                        var next = spec
                        for key in ["x", "y", "width", "height", "screen_id", "hidden"] { next[key] = item.spec[key] }
                        item.spec = next
                    } else {
                        item.viewRevision = revision; item.spec = spec
                        item.pendingLayout = nil; item.gestures?.enabled = !item.expired
                    }
                    item.panel.title = (spec["title"] as? String).map { String($0.prefix(200)) } ?? "Athena"
                    scheduleExpiration(item)
                    position(item)
                }
            } else { create(id: id, version: version, revision: revision, spec: spec) }
        }
        publish()
    }

    private func create(id: String, version: Int, revision: Int, spec: [String: Any]) {
        guard let screen = screen(spec) else { return }
        let panel = CompanionPanel(contentRect: frame(spec, screen: screen), styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        panel.title = (spec["title"] as? String).map { String($0.prefix(200)) } ?? "Athena"
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false
        panel.isFloatingPanel = true; panel.hidesOnDeactivate = false
        // Setting isFloatingPanel changes level, so apply the content hierarchy afterwards.
        panel.level = spec["file_id"] == nil ? .floating : NSWindow.Level(rawValue: NSWindow.Level.normal.rawValue + 1)
        panel.becomesKeyOnlyIfNeeded = true; panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(self, name: "athenaDesktop")
        configuration.userContentController.addUserScript(WKUserScript(source: DesktopCompanion.bootstrap, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let web = WKWebView(frame: panel.contentView?.bounds ?? .zero, configuration: configuration)
        web.setValue(false, forKey: "drawsBackground"); web.underPageBackgroundColor = .clear
        web.autoresizingMask = [.width, .height]; web.navigationDelegate = self; web.uiDelegate = self
        let item = Item(id: id, version: version, viewRevision: revision, spec: spec, panel: panel, web: web)
        if spec["file_id"] != nil {
            let container = NSView(frame: web.frame)
            container.addSubview(web)
            let gestures = SurfaceGestureView(frame: container.bounds)
            gestures.autoresizingMask = [.width, .height]
            gestures.completed = { [weak self, weak item] in
                guard let self, let item, self.items[item.id] === item else { return }
                self.commitFrame(item)
            }
            container.addSubview(gestures); item.gestures = gestures; panel.contentView = container
        } else { panel.contentView = web }
        DesktopHitRegions.shared.register(panel)
        items[id] = item
        scheduleExpiration(item)
        position(item)
        if !item.expired { load(item) }
    }

    private func position(_ item: Item) {
        if !item.expired, let expiry = item.expiresAt, expiry <= Date() { expire(item); return }
        var changed = false
        if let screen = screen(item.spec) {
            let target = frame(item.spec, screen: screen)
            if item.panel.frame != target {
                item.panel.setFrame(target, display: false)
                changed = true
                #if ATHENA_NATIVE_CHECKS
                fixtureFrameChanges += 1
                #endif
            }
        }
        let visible = presented && !item.expired && item.spec["hidden"] as? Bool != true
        if item.panel.isVisible != visible {
            if visible { item.panel.orderFront(nil) } else { item.panel.orderOut(nil) }
            changed = true
            #if ATHENA_NATIVE_CHECKS
            fixtureOrderChanges += 1
            #endif
        }
        if changed { DesktopHitRegions.shared.visibilityChanged() }
        reportSurfaceState(item)
    }

    private func load(_ item: Item) {
        guard !item.expired, let origin, let scope, let sessionID, var url = URLComponents(url: origin, resolvingAgainstBaseURL: false) else { return }
        #if ATHENA_NATIVE_CHECKS
        fixtureDocumentLoads += 1
        #endif
        item.state = "loading"; item.error = nil; item.loadToken = UUID()
        url.path = "/"
        url.queryItems = [URLQueryItem(name: "desktop", value: "widget"), URLQueryItem(name: "session", value: sessionID),
            URLQueryItem(name: "component", value: item.id), URLQueryItem(name: "version", value: String(item.version)), URLQueryItem(name: "scope", value: scope)]
        if let file = item.spec["file_id"] as? String, let revision = item.spec["content_revision"] as? String {
            url.queryItems?.append(contentsOf: [URLQueryItem(name: "file", value: file), URLQueryItem(name: "content_revision", value: revision)])
        }
        guard let target = url.url else { fail(item, code: "panel_failed"); return }
        DesktopHitRegions.shared.reset(item.panel)
        item.web.load(URLRequest(url: target))
        let token = item.loadToken
        Task { @MainActor [weak self, weak item] in
            try? await Task.sleep(for: .seconds(30))
            guard let self, let item, self.items[item.id] === item, !item.expired, item.loadToken == token, item.state == "loading" else { return }
            self.fail(item, code: "load_timeout")
        }
    }

    private func scheduleExpiration(_ item: Item) {
        guard !item.expired else { return }
        let expiry = Self.expiration(item.spec["expires_at"])
        guard item.expiresAt != expiry else { return }
        item.expiryTask?.cancel(); item.expiryTask = nil
        item.expiresAt = expiry
        guard let expiry else { return }
        if expiry <= Date() { expire(item); return }
        let version = item.version
        item.expiryTask = Task { @MainActor [weak self, weak item] in
            do {
                while expiry > Date() { try await Task.sleep(for: .seconds(max(0, expiry.timeIntervalSinceNow))) }
            }
            catch { return }
            guard let self, let item, self.items[item.id] === item, item.version == version,
                  item.expiresAt == expiry, !item.expired else { return }
            self.expire(item)
        }
    }
    private func expire(_ item: Item) {
        guard items[item.id] === item, !item.expired else { return }
        item.expired = true; item.state = "hidden"; item.error = nil
        item.expiryTask?.cancel(); item.expiryTask = nil
        item.pendingLayout = nil; item.gestures?.enabled = false; item.loadToken = UUID()
        // Expiry is enforced by the native host even while the parent document
        // sleeps. Unload the generated page so its timers/media cannot restart.
        item.web.configuration.userContentController.removeScriptMessageHandler(forName: "athenaDesktop")
        item.web.stopLoading(); item.web.navigationDelegate = nil; item.web.uiDelegate = nil
        item.web.loadHTMLString("", baseURL: nil)
        position(item)
        interaction(item, action: "componentEnded", values: [:])
        publish()
    }

    func setPresented(_ value: Bool) {
        guard presented != value else { return }
        presented = value
        for item in items.values { position(item) }
        publish()
    }
    func refreshDocuments(_ body: [String: Any]) {
        guard body["scope"] as? String == scope, let scope else { return }
        for item in items.values where !item.expired {
            let detail: [String: Any] = ["scope": scope, "component_id": item.id, "version": item.version]
            guard let bytes = try? JSONSerialization.data(withJSONObject: detail), let json = String(data: bytes, encoding: .utf8) else { continue }
            item.web.evaluateJavaScript("dispatchEvent(new CustomEvent('athena:widget-refresh',{detail:\(json)}))", completionHandler: nil)
        }
    }
    /// Only a user-requested file open raises its work surface. Reconciliation,
    /// background delivery and geometry changes remain non-activating.
    func focus(_ body: [String: Any]) {
        guard presented, body["scope"] as? String == scope, let id = Self.identifier(body["component_id"]),
              let item = items[id], Self.integer(body["version"]) == item.version,
              let file = item.spec["file_id"] as? String, body["file_id"] as? String == file,
              body["content_revision"] as? String == item.spec["content_revision"] as? String,
              item.state == "mounted", item.spec["hidden"] as? Bool != true, item.panel.isVisible else { return }
        item.panel.makeKeyAndOrderFront(nil)
        item.panel.makeFirstResponder(item.web)
    }
    func screensChanged() { for item in items.values { position(item) }; publish() }
    func closeAll() {
        for id in Array(items.keys) { remove(id) }
        scope = nil; sessionID = nil; origin = nil
        publish()
    }
    private func remove(_ id: String) {
        guard let item = items.removeValue(forKey: id) else { return }
        item.expiryTask?.cancel(); item.expiryTask = nil
        item.web.configuration.userContentController.removeScriptMessageHandler(forName: "athenaDesktop")
        item.web.stopLoading(); item.web.navigationDelegate = nil; item.web.uiDelegate = nil
        DesktopHitRegions.shared.unregister(item.panel)
        item.panel.orderOut(nil); item.panel.close()
    }
    private func bound(_ web: WKWebView?) -> Item? { items.values.first { $0.web === web } }
    private func fail(_ item: Item, code: String) { guard !item.expired else { return }; item.state = "failed"; item.error = code; publish() }
    private func publish() {
        let state = observation()
        // State reports can prompt frontend reconciliation; only actual native changes
        // should cross the WebKit bridge, or the two renderers can keep waking each other.
        if publishedScope == scope, let publishedState, NSDictionary(dictionary: publishedState).isEqual(to: state) { return }
        publishedState = state
        publishedScope = scope
        #if ATHENA_NATIVE_CHECKS
        fixtureStateReports += 1
        #endif
        onState?(state)
    }

    func observation() -> [String: Any] {
        let top = NSScreen.screens.first?.frame.maxY ?? 0
        func rect(_ frame: NSRect, top: CGFloat) -> [String: CGFloat] {
            ["x": frame.minX, "y": top - frame.maxY, "width": frame.width, "height": frame.height]
        }
        let screens = NSScreen.screens.map { screen -> [String: Any] in
            ["id": Self.screenID(screen), "frame": rect(screen.frame, top: top),
             "visible_frame": rect(screen.visibleFrame, top: top), "scale": screen.backingScaleFactor]
        }
        let components = items.values.sorted { $0.id < $1.id }.map { item -> [String: Any] in
            let screen = screen(item.spec)
            let area = screen?.visibleFrame ?? .zero
            var result: [String: Any] = ["id": item.id, "version": item.version, "view_revision": item.viewRevision,
                "state": item.expired || item.spec["hidden"] as? Bool == true ? "hidden" : item.state, "visible": item.panel.isVisible,
                "rect": ["x": item.panel.frame.minX - area.minX, "y": area.maxY - item.panel.frame.maxY,
                         "width": item.panel.frame.width, "height": item.panel.frame.height],
                "screen_id": screen.map(Self.screenID) ?? "0"]
            if item.state == "failed", item.spec["hidden"] as? Bool != true { result["error_code"] = item.error ?? "panel_failed" }
            if let file = item.spec["file_id"], let revision = item.spec["content_revision"] {
                result["file_id"] = file; result["content_revision"] = revision
            }
            return result
        }
        return ["platform": "macos", "supported": true, "presented": presented, "screens": screens, "components": components]
    }

    private func layout(_ item: Item, frame: NSRect, screen: NSScreen) -> [String: Any] {
        let area = screen.visibleFrame
        return ["x": area.width > frame.width ? min(1, max(0, (frame.minX - area.minX) / (area.width - frame.width))) : 0,
                "y": area.height > frame.height ? min(1, max(0, (area.maxY - frame.maxY) / (area.height - frame.height))) : 0,
                "width": frame.width, "height": frame.height, "screen_id": Self.screenID(screen)]
    }
    private func commitFrame(_ item: Item) {
        guard let screen = item.panel.screen ?? screen(item.spec) else { return }
        proposeLayout(item, values: layout(item, frame: item.panel.frame, screen: screen))
    }
    private func proposeLayout(_ item: Item, values: [String: Any]) {
        guard !item.expired, item.pendingLayout == nil else { return }
        let id = UUID().uuidString
        item.pendingLayout = (id, item.viewRevision, item.spec)
        item.spec.merge(values) { _, new in new }
        item.gestures?.enabled = false
        position(item)
        // Persistence is owned by the authenticated parent. A local close is a proposal,
        // not a hidden flag that the next scene refresh can silently undo.
        interaction(item, action: "layout", values: ["interaction_id": id, "layout": values])
        publish()
    }
    func completeInteraction(_ body: [String: Any]) {
        guard body["scope"] as? String == scope, let id = Self.identifier(body["component_id"]), let item = items[id],
              Self.integer(body["version"]) == item.version, let pending = item.pendingLayout,
              body["interaction_id"] as? String == pending.id, let ok = body["ok"] as? Bool else { return }
        if ok {
            guard let revision = Self.integer(body["view_revision"]), revision > pending.revision,
                  let values = body["layout"] as? [String: Any] else { return }
            item.viewRevision = revision
            for key in ["x", "y", "width", "height", "screen_id", "hidden"] {
                if let value = values[key] { item.spec[key] = value }
            }
        } else { item.spec = pending.previous }
        item.pendingLayout = nil; item.gestures?.enabled = true
        item.web.evaluateJavaScript("dispatchEvent(new CustomEvent('athena:surface-layout',{detail:{ok:\(ok ? "true" : "false")}}))", completionHandler: nil)
        position(item); publish()
    }
    private func reportSurfaceState(_ item: Item, force: Bool = false) {
        guard item.spec["file_id"] != nil, let screen = item.panel.screen ?? screen(item.spec) else { return }
        let maximized = item.panel.frame == screen.visibleFrame
        guard force || item.reportedMaximized != maximized else { return }
        item.reportedMaximized = maximized
        let value = maximized ? "true" : "false"
        item.web.evaluateJavaScript("document.documentElement.dataset.surfaceMaximized='\(value)';dispatchEvent(new CustomEvent('athena:surface',{detail:{maximized:\(value)}}))", completionHandler: nil)
    }

    private func updateDragRegions(_ item: Item, payload: Any?) {
        guard let gestures = item.gestures, let objects = payload as? [[String: Any]], objects.count <= 16 else { return }
        var regions: [NSRect] = []
        for object in objects {
            var values: [CGFloat] = []
            for key in ["x", "y", "width", "height"] {
                guard let number = object[key] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
                      number.doubleValue.isFinite, number.doubleValue >= 0, number.doubleValue <= 16384 else { return }
                values.append(number.doubleValue)
            }
            regions.append(NSRect(x: values[0], y: values[1], width: values[2], height: values[3]))
        }
        gestures.dragRegions = regions
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "athenaDesktop", let item = bound(message.webView), !item.expired,
              DesktopCompanion.trusts(frameIsMain: message.frameInfo.isMainFrame, frameURL: message.frameInfo.request.url,
                  origin: origin, currentURL: item.web.url),
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "hitRegions": DesktopHitRegions.shared.update(item.panel, payload: body["regions"])
        case "surfaceDragRegions": updateDragRegions(item, payload: body["regions"])
        case "widgetHealth":
            guard let state = body["state"] as? String, ["loading", "mounted", "failed"].contains(state) else { return }
            if let file = item.spec["file_id"] as? String {
                guard body["file_id"] as? String == file,
                      body["content_revision"] as? String == item.spec["content_revision"] as? String else { return }
            }
            item.state = state
            if state == "failed" {
                let code = body["error_code"] as? String ?? "script_error"
                item.error = ["native_unavailable", "panel_failed", "navigation_failed", "script_error", "unhandled_rejection", "blocked_resource", "empty_document", "load_timeout", "preview_unavailable"].contains(code) ? code : "script_error"
            } else { item.error = nil }
            reportSurfaceState(item, force: true)
            publish()
        case "submit":
            guard let text = body["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.utf8.count <= 32000 else { return }
            interaction(item, action: action, values: ["text": text])
        case "navigate":
            guard let session = Self.identifier(body["session_id"]) else { return }
            interaction(item, action: action, values: ["session_id": session])
        case "hideWidget", "closeWidget": proposeLayout(item, values: ["hidden": true])
        case "maximizeWidget":
            guard item.spec["file_id"] != nil, item.pendingLayout == nil, let screen = item.panel.screen ?? screen(item.spec) else { return }
            let target: NSRect
            if item.panel.frame == screen.visibleFrame {
                target = item.restoreFrame ?? frame(["file_id": item.spec["file_id"] as Any, "x": 0.3, "y": 0.2], screen: screen)
            } else { item.restoreFrame = item.panel.frame; target = screen.visibleFrame }
            proposeLayout(item, values: layout(item, frame: target, screen: screen))
        case "downloadFile":
            guard let file = item.spec["file_id"] as? String, let revision = item.spec["content_revision"] as? String else { return }
            interaction(item, action: "download", values: ["file_id": file, "content_revision": revision])
        default: break
        }
    }
    private func interaction(_ item: Item, action: String, values: [String: Any]) {
        guard let scope else { return }
        var identity: [String: Any] = ["action": action, "scope": scope, "component_id": item.id, "version": item.version, "view_revision": item.viewRevision]
        if let file = item.spec["file_id"], let revision = item.spec["content_revision"] { identity["file_id"] = file; identity["content_revision"] = revision }
        onInteraction?(identity.merging(values) { _, new in new })
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if DesktopCompanion.trusts(frameIsMain: true, frameURL: action.request.url, origin: origin, currentURL: origin) { decisionHandler(.allow) }
        else {
            if action.navigationType == .linkActivated, let url = action.request.url, ["http", "https", "mailto"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { if let item = bound(webView) { fail(item, code: "navigation_failed") } }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { if let item = bound(webView) { fail(item, code: "navigation_failed") } }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { if let item = bound(webView) { fail(item, code: "script_error") } }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.navigationType == .linkActivated, let url = action.request.url, ["http", "https", "mailto"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }

    #if ATHENA_NATIVE_CHECKS
    private(set) var fixtureFrameChanges = 0
    private(set) var fixtureOrderChanges = 0
    private(set) var fixtureStateReports = 0
    private(set) var fixtureDocumentLoads = 0
    func fixtureWebView(id: String) -> WKWebView? { items[id]?.web }
    func fixtureGestureHit(id: String, point: NSPoint) -> Bool { items[id]?.gestures?.hitTest(point) != nil }
    func fixtureCommitFrame(id: String, frame: NSRect) {
        guard let item = items[id] else { return }
        item.panel.setFrame(frame, display: true); commitFrame(item)
    }
    func fixtureResize(id: String, by delta: NSPoint) { items[id]?.gestures?.fixtureResize(by: delta) }
    #endif
}
