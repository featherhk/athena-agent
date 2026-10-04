import UIKit
@preconcurrency import WebKit

// Compiled as a separate Simulator application. A short-lived fixture server
// serves only synthetic documents; no Athena service or user account is accessed.
@main
final class NativeCheckApp: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "Native checks", sessionRole: session.role)
        configuration.delegateClass = NativeCheckScene.self
        return configuration
    }
}

@MainActor
final class NativeCheckScene: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private let home: URL = {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--fixture-origin"), index + 1 < arguments.count,
              let url = URL(string: arguments[index + 1]), url.scheme == "http", url.host == "127.0.0.1",
              url.port != nil, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path == "/" else { fatalError("A dedicated loopback fixture origin is required") }
        return url
    }()
    private var passed: [String] = [] {
        didSet { print("Native check passed: " + passed.last!); fflush(stdout) }
    }

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let configuration = WKWebViewConfiguration()
        let controller = AthenaController(policy: HostPolicy(home: home), configuration: configuration)
        let window = UIWindow(windowScene: scene)
        window.rootViewController = controller
        self.window = window
        window.makeKeyAndVisible()
        Task { await check(controller) }
    }

    private func require(_ condition: @autoclosure () -> Bool, _ description: String) throws {
        guard condition() else { throw NSError(domain: "Native acceptance", code: 1, userInfo: [NSLocalizedDescriptionKey: description]) }
    }

    private func wait(_ condition: @escaping @MainActor () async -> Bool) async throws {
        for _ in 0..<160 {
            if await condition() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        throw NSError(domain: "Native acceptance", code: 2, userInfo: [NSLocalizedDescriptionKey: "Timed out waiting for native/WebKit state"])
    }

    private func js(_ source: String, _ web: WKWebView) async -> Any? {
        // JavaScript statements may return undefined. The SDK's async Any bridge
        // force-unwraps that nil result; the callback correctly preserves it.
        await withCheckedContinuation { continuation in
            web.evaluateJavaScript(source) { value, _ in continuation.resume(returning: value) }
        }
    }

    private func check(_ controller: AthenaController) async {
        do {
            guard let web = controller.view.subviews.compactMap({ $0 as? WKWebView }).first else {
                throw NSError(domain: "Native acceptance", code: 3, userInfo: [NSLocalizedDescriptionKey: "No native WebKit view"])
            }
            try await wait { self.window?.windowScene?.activationState == .foregroundActive && !web.isLoading && web.url == self.home && !controller.view.subviews.compactMap({ $0 as? UIActivityIndicatorView }).contains(where: { $0.isAnimating }) }
            try await wait { await self.js("document.getElementById('draft') !== null", web) as? Bool == true }
            _ = await js("document.getElementById('draft').value='A draft that must survive'; window.requestProof=17", web)
            controller.view.layoutIfNeeded()
            try require(abs(web.frame.minY - controller.view.safeAreaLayoutGuide.layoutFrame.minY) < 1, "Web surface ignored the top safe area")
            try require(abs(web.frame.maxY - controller.view.keyboardLayoutGuide.layoutFrame.minY) < 1, "Composer surface is not attached to the keyboard guide")
            passed.append("safe-area-and-keyboard-guide")

            web.load(URLRequest(url: home.appendingPathComponent("missing")))
            try await wait { controller.presentedViewController is UIAlertController }
            try require(!web.isHidden, "An attachment/page HTTP error hid a usable conversation")
            let firstDraft = await js("document.getElementById('draft').value", web) as? String
            try require(firstDraft == "A draft that must survive", "Rejected HTTP navigation replaced the unsent draft")
            passed.append("http-error-preserves-committed-document")
            controller.dismiss(animated: false)
            try await wait { controller.presentedViewController == nil }

            web.load(URLRequest(url: home.appendingPathComponent("offline")))
            try await wait { controller.presentedViewController is UIAlertController }
            let secondDraft = await js("document.getElementById('draft').value", web) as? String
            try require(!web.isHidden && secondDraft == firstDraft, "A transient network failure hid or replaced the draft")
            passed.append("provisional-error-preserves-draft")
            controller.dismiss(animated: false)
            try await wait { controller.presentedViewController == nil }

            _ = await js("{const link=document.createElement('a');link.href='/missing-file';link.download='report.txt';document.body.append(link);link.click();link.remove()}", web)
            try await wait { controller.presentedViewController is UIAlertController }
            let failureTitle = (controller.presentedViewController as? UIAlertController)?.title
            try require(failureTitle == NSLocalizedString("download_failed", comment: ""), "A rejected attachment was not identified as a download failure")
            let downloadDraft = await js("document.getElementById('draft').value", web) as? String
            try require(!web.isHidden && downloadDraft == firstDraft, "A failed file download destroyed the conversation")
            passed.append("http-error-is-not-shared-as-a-file")
            controller.dismiss(animated: false)
            try await wait { controller.presentedViewController == nil }

            // A second host isolates the completed-file check from alert acknowledgement:
            // dismissing an alert programmatically is not a user's Close action.
            let fileConfiguration = WKWebViewConfiguration()
            let files = AthenaController(policy: HostPolicy(home: home), configuration: fileConfiguration)
            window?.rootViewController = files
            let fileWeb = files.view.subviews.compactMap({ $0 as? WKWebView }).first!
            try await wait { await self.js("document.getElementById('draft') !== null", fileWeb) as? Bool == true }
            _ = await js("{const link=document.createElement('a');link.href='/file';link.download='report.txt';document.body.append(link);link.click();link.remove()}", fileWeb)
            try await wait { files.presentedViewController is UIActivityViewController }
            let directories = try FileManager.default.contentsOfDirectory(at: FileManager.default.temporaryDirectory, includingPropertiesForKeys: nil).filter { $0.lastPathComponent.hasPrefix("Athena-") }
            let fileURLs = directories.map { $0.appendingPathComponent("report.txt") }.filter { FileManager.default.fileExists(atPath: $0.path) }
            try require(fileURLs.count == 1, "A failed response or duplicate transfer left unexpected files")
            let content = try String(contentsOf: fileURLs[0], encoding: .utf8)
            try require(content == "Athena synthetic file\n", "Shared file contents do not match the successful download")
            let sheet = files.presentedViewController as! UIActivityViewController
            sheet.completionWithItemsHandler?(nil, false, nil, nil)
            try await wait { files.presentedViewController == nil }
            try require(!FileManager.default.fileExists(atPath: fileURLs[0].path), "Finished sharing retained the temporary transfer")
            passed.append("successful-download-shares-and-cleans-up")
            window?.rootViewController = controller

            controller.enteredBackground()
            try await wait { await self.js("window.backgroundEvents", web) as? Int == 1 }
            let proof = await js("window.requestProof", web) as? Int
            try require(proof == 17, "Backgrounding reloaded the page or replayed a task")
            passed.append("background-media-signal-without-reload")

            try require(controller.accessibilityPerformEscape(), "Native VoiceOver recovery was unavailable")
            try await wait { controller.presentedViewController is UIAlertController }
            try require((controller.presentedViewController as? UIAlertController)?.actions.count == 2, "Recovery did not offer cancel and confirmation")
            try require(controller.keyCommands?.contains(where: { $0.input == "r" && $0.modifierFlags == .command }) == true, "Native keyboard recovery is undiscoverable")
            controller.dismiss(animated: false)
            try await wait { controller.presentedViewController == nil }
            // Invoke the same action that the confirmation button invokes, without private UIKit APIs.
            controller.recoverPage()
            try await wait { !web.isLoading && web.url?.query == "recovery=1" }
            try require(HostPolicy(home: home).owns(web.url), "Safe recovery left the fixture origin")
            passed.append("native-accessible-recovery-uses-safe-get")

            controller.webViewWebContentProcessDidTerminate(web)
            try require(web.isHidden, "A dead page process did not reveal independent recovery")
            try require(controller.view.subviews.compactMap({ $0 as? UIStackView }).contains(where: { !$0.isHidden }), "A dead page process left a blank screen")
            passed.append("content-process-death-has-native-recovery")
            let retry = controller.view.subviews.compactMap { $0 as? UIStackView }.flatMap(\.arrangedSubviews).compactMap { $0 as? UIButton }.first!
            retry.sendActions(for: .touchUpInside)
            try await wait { !web.isLoading && !web.isHidden && web.url?.query == "recovery=1" }
            passed.append("retry-after-recovery-keeps-safe-mode")

            // A first load without a committed document must use the full native retry view.
            let configuration = WKWebViewConfiguration()
            let offline = AthenaController(policy: HostPolicy(home: home.appendingPathComponent("offline")), configuration: configuration)
            window?.rootViewController = offline
            try await wait { offline.view.subviews.compactMap({ $0 as? UIStackView }).contains(where: { !$0.isHidden }) }
            passed.append("initial-offline-load-has-native-retry")
            finish(nil)
        } catch { finish(error) }
    }

    private func finish(_ error: Error?) {
        let result: [String: Any] = ["passed": passed, "ok": error == nil, "error": error?.localizedDescription ?? "",
                                     "origin": home.absoluteString,
                                     "physical_device_verified": false]
        let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        let text = String(data: data, encoding: .utf8)!
        print("ATHENA_NATIVE_CHECK_RESULT " + text)
        fflush(stdout)
        exit(error == nil ? 0 : 1)
    }
}
