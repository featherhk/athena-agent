import AppKit
@preconcurrency import WebKit

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
    private static func screenID(_ screen: NSScreen) -> String {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.stringValue ?? "0"
    }
    private func screen(_ spec: [String: Any]) -> NSScreen? {
        NSScreen.screens.first { Self.screenID($0) == spec["screen_id"] as? String } ?? NSScreen.main ?? NSScreen.screens.first
    }
    private func frame(_ spec: [String: Any], screen: NSScreen) -> NSRect {
        let area = screen.visibleFrame
        let width = min(area.width, max(32, min(16384, Self.number(spec["width"], fallback: 320))))
        let height = min(area.height, max(32, min(16384, Self.number(spec["height"], fallback: 240))))
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
            parsed.append((id, version, revision, spec))
        }
        if self.scope != requestedScope || self.origin != origin { closeAll() }
        self.scope = requestedScope; sessionID = session; self.origin = origin
        for id in Array(items.keys) where !seen.contains(id) { remove(id) }
        for (id, version, revision, spec) in parsed {
            if let item = items[id] {
                if item.version != version {
                    // Never rebind an old document to a new component version. Late script or
                    // navigation callbacks from its old WebKit view must have no bound identity.
                    remove(id)
                    create(id: id, version: version, revision: revision, spec: spec)
                } else {
                    item.viewRevision = revision; item.spec = spec
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
        panel.level = .floating; panel.isFloatingPanel = true; panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true; panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(self, name: "athenaDesktop")
        configuration.userContentController.addUserScript(WKUserScript(source: DesktopCompanion.bootstrap, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let web = WKWebView(frame: panel.contentView?.bounds ?? .zero, configuration: configuration)
        web.setValue(false, forKey: "drawsBackground"); web.underPageBackgroundColor = .clear
        web.autoresizingMask = [.width, .height]; web.navigationDelegate = self; web.uiDelegate = self
        panel.contentView = web
        DesktopHitRegions.shared.register(panel)
        let item = Item(id: id, version: version, viewRevision: revision, spec: spec, panel: panel, web: web)
        items[id] = item
        position(item); load(item)
    }

    private func position(_ item: Item) {
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
        let visible = presented && item.spec["hidden"] as? Bool != true
        if item.panel.isVisible != visible {
            if visible { item.panel.orderFront(nil) } else { item.panel.orderOut(nil) }
            changed = true
            #if ATHENA_NATIVE_CHECKS
            fixtureOrderChanges += 1
            #endif
        }
        if changed { DesktopHitRegions.shared.visibilityChanged() }
    }

    private func load(_ item: Item) {
        guard let origin, let scope, let sessionID, var url = URLComponents(url: origin, resolvingAgainstBaseURL: false) else { return }
        item.state = "loading"; item.error = nil; item.loadToken = UUID()
        url.path = "/"
        url.queryItems = [URLQueryItem(name: "desktop", value: "widget"), URLQueryItem(name: "session", value: sessionID),
            URLQueryItem(name: "component", value: item.id), URLQueryItem(name: "version", value: String(item.version)), URLQueryItem(name: "scope", value: scope)]
        guard let target = url.url else { fail(item, code: "panel_failed"); return }
        DesktopHitRegions.shared.reset(item.panel)
        item.web.load(URLRequest(url: target))
        let token = item.loadToken
        Task { @MainActor [weak self, weak item] in
            try? await Task.sleep(for: .seconds(30))
            guard let self, let item, self.items[item.id] === item, item.loadToken == token, item.state == "loading" else { return }
            self.fail(item, code: "load_timeout")
        }
    }

    func setPresented(_ value: Bool) {
        guard presented != value else { return }
        presented = value
        for item in items.values { position(item) }
        publish()
    }
    func screensChanged() { for item in items.values { position(item) }; publish() }
    func closeAll() {
        for id in Array(items.keys) { remove(id) }
        scope = nil; sessionID = nil; origin = nil
        publish()
    }
    private func remove(_ id: String) {
        guard let item = items.removeValue(forKey: id) else { return }
        item.web.configuration.userContentController.removeScriptMessageHandler(forName: "athenaDesktop")
        item.web.stopLoading(); item.web.navigationDelegate = nil; item.web.uiDelegate = nil
        DesktopHitRegions.shared.unregister(item.panel)
        item.panel.orderOut(nil); item.panel.close()
    }
    private func bound(_ web: WKWebView?) -> Item? { items.values.first { $0.web === web } }
    private func fail(_ item: Item, code: String) { item.state = "failed"; item.error = code; publish() }
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
                "state": item.spec["hidden"] as? Bool == true ? "hidden" : item.state, "visible": item.panel.isVisible,
                "rect": ["x": item.panel.frame.minX - area.minX, "y": area.maxY - item.panel.frame.maxY,
                         "width": item.panel.frame.width, "height": item.panel.frame.height],
                "screen_id": screen.map(Self.screenID) ?? "0"]
            if item.state == "failed", item.spec["hidden"] as? Bool != true { result["error_code"] = item.error ?? "panel_failed" }
            return result
        }
        return ["platform": "macos", "supported": true, "presented": presented, "screens": screens, "components": components]
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "athenaDesktop", let item = bound(message.webView),
              DesktopCompanion.trusts(frameIsMain: message.frameInfo.isMainFrame, frameURL: message.frameInfo.request.url,
                  origin: origin, currentURL: item.web.url),
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "hitRegions": DesktopHitRegions.shared.update(item.panel, payload: body["regions"])
        case "widgetHealth":
            guard let state = body["state"] as? String, ["mounted", "failed"].contains(state) else { return }
            item.state = state
            if state == "failed" {
                let code = body["error_code"] as? String ?? "script_error"
                item.error = ["native_unavailable", "panel_failed", "navigation_failed", "script_error", "unhandled_rejection", "blocked_resource", "empty_document", "load_timeout"].contains(code) ? code : "script_error"
            } else { item.error = nil }
            publish()
        case "submit":
            guard let text = body["text"] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.utf8.count <= 32000 else { return }
            interaction(item, action: action, values: ["text": text])
        case "navigate":
            guard let session = Self.identifier(body["session_id"]) else { return }
            interaction(item, action: action, values: ["session_id": session])
        case "hideWidget": item.spec["hidden"] = true; position(item); publish()
        default: break
        }
    }
    private func interaction(_ item: Item, action: String, values: [String: Any]) {
        guard let scope else { return }
        onInteraction?(["action": action, "scope": scope, "component_id": item.id, "version": item.version].merging(values) { _, new in new })
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
    func fixtureWebView(id: String) -> WKWebView? { items[id]?.web }
    #endif
}
