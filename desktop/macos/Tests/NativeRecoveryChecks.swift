import AppKit
@preconcurrency import WebKit

@MainActor
final class NativeRecoveryChecks: NSObject, NSApplicationDelegate {
    private var host: AppDelegate!
    private var window: NSWindow!
    private var web: WKWebView!
    private var passed: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--fixture-origin"), index + 1 < arguments.count,
              let url = URL(string: arguments[index + 1]), WindowRecovery().request(for: url) != nil else { exit(2) }
        host = AppDelegate()
        (window, web) = host.configureNativeFixture(url)
        Task { await check(url) }
    }

    private func require(_ condition: @autoclosure () -> Bool, _ description: String) throws {
        guard condition() else { throw NSError(domain: "macOS native acceptance", code: 1, userInfo: [NSLocalizedDescriptionKey: description]) }
    }

    private func wait(_ condition: @escaping @MainActor () async -> Bool) async throws {
        for _ in 0..<200 {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        throw NSError(domain: "macOS native acceptance", code: 2, userInfo: [NSLocalizedDescriptionKey: "Timed out waiting for AppKit/WebKit"])
    }

    private func js(_ source: String) async -> Any? {
        await withCheckedContinuation { continuation in
            web.evaluateJavaScript(source) { value, _ in continuation.resume(returning: value) }
        }
    }

    private func menu(recovery: Bool) -> NSMenuItem? {
        NSApp.mainMenu?.items.compactMap(\.submenu).flatMap(\.items).first {
            $0.keyEquivalent == "r" && $0.keyEquivalentModifierMask.contains(.shift) == recovery
        }
    }

    private func invoke(_ item: NSMenuItem) {
        NSApp.sendAction(item.action!, to: item.target, from: item)
    }

    private func check(_ home: URL) async {
        do {
            try await wait { !self.web.isLoading && self.web.url == home && !self.web.isHidden }
            try await wait { await self.js("document.getElementById('draft') !== null") as? Bool == true }
            _ = await js("document.getElementById('draft').value='Keep this synthetic draft';window.requestProof=17")
            guard let recover = menu(recovery: true), let reload = menu(recovery: false) else {
                throw NSError(domain: "macOS native acceptance", code: 3, userInfo: [NSLocalizedDescriptionKey: "Missing native recovery/reload menus"])
            }
            invoke(recover)
            try await wait { self.window.attachedSheet != nil }
            try require(web.url?.query == nil, "Recovery navigated before user confirmation")
            window.endSheet(window.attachedSheet!, returnCode: .alertSecondButtonReturn)
            try await wait { self.window.attachedSheet == nil }
            let draft = await js("document.getElementById('draft').value") as? String
            try require(draft == "Keep this synthetic draft", "Cancelling recovery discarded the draft")
            passed.append("native-recovery-cancel-preserves-draft")

            invoke(recover)
            try await wait { self.window.attachedSheet != nil }
            window.endSheet(window.attachedSheet!, returnCode: .alertFirstButtonReturn)
            try await wait { !self.web.isLoading && self.web.url?.query == "recovery=1" && !self.web.isHidden }
            passed.append("native-menu-loads-safe-get")
            invoke(reload)
            try await wait { !self.web.isLoading && self.web.url?.query == "recovery=1" }
            passed.append("command-r-preserves-safe-mode")

            // A fresh host represents a normal window before its first content-process failure.
            window.orderOut(nil)
            host = AppDelegate()
            (window, web) = host.configureNativeFixture(home)
            try await wait { !self.web.isLoading && self.web.url == home && !self.web.isHidden }
            host.webViewWebContentProcessDidTerminate(web)
            try await wait { !self.web.isLoading && self.web.url?.query == "recovery=1" }
            passed.append("first-process-failure-enters-safe-mode")
            host.webViewWebContentProcessDidTerminate(web)
            try await wait { self.window.attachedSheet != nil }
            try require(web.isHidden && !web.isLoading, "Repeated process failure continued automatic reloads")
            passed.append("second-process-failure-stops-loop")
            window.endSheet(window.attachedSheet!, returnCode: .alertFirstButtonReturn)
            try await wait { self.window.attachedSheet == nil && !self.web.isHidden && !self.web.isLoading && self.web.url?.query == "recovery=1" }
            passed.append("native-failure-retry-remains-safe")
            finish(nil)
        } catch { finish(error) }
    }

    private func finish(_ error: Error?) {
        let receipt: [String: Any] = ["ok": error == nil, "passed": passed, "error": error?.localizedDescription ?? "", "real_backend_started": false]
        let data = try! JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys])
        print("ATHENA_NATIVE_CHECK_RESULT " + String(data: data, encoding: .utf8)!)
        fflush(stdout)
        exit(error == nil ? 0 : 1)
    }
}

@main
struct NativeRecoveryMain {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = NativeRecoveryChecks()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
