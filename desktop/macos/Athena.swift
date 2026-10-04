import AppKit
import WebKit

private func tr(_ key: String) -> String { NSLocalizedString(key, comment: "") }

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var child: Process?
    private var output: Pipe?
    private var pendingOutput = Data()
    private var localURL: URL?
    private var terminating = false
    private var startupGeneration = 0
    private var loadingLabel: NSTextField!
    private var updateTask: Task<Void, Never>?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Opening a second copy only activates the running app; it never attaches to an arbitrary port.
        if let other = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier!)
            .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            other.activate(options: [.activateAllWindows]); NSApp.terminate(nil); return
        }
        makeMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1080, height: 780),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Athena"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.minSize = NSSize(width: 390, height: 560)
        window.setFrameAutosaveName("AthenaMainWindow")
        window.center()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        let container = NSView()
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor(calibratedRed: 0.973, green: 0.965, blue: 0.949, alpha: 1).cgColor
        webView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.topAnchor.constraint(equalTo: container.topAnchor, constant: 28),
            webView.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])
        loadingLabel = NSTextField(labelWithString: tr("starting"))
        loadingLabel.font = NSFont(name: "Iowan Old Style", size: 22) ?? .systemFont(ofSize: 22)
        loadingLabel.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(loadingLabel)
        NSLayoutConstraint.activate([
            loadingLabel.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            loadingLabel.centerYAnchor.constraint(equalTo: container.centerYAnchor)
        ])
        window.contentView = container
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startBackend()
    }

    private func makeMenu() {
        let bar = NSMenu()
        let application = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: tr("about"), action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(withTitle: tr("check_updates"), action: #selector(checkForUpdates(_:)), keyEquivalent: "").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: tr("quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        application.submenu = appMenu
        bar.addItem(application)
        let editItem = NSMenuItem(title: tr("edit"), action: nil, keyEquivalent: "")
        let edit = NSMenu(title: tr("edit"))
        for (key, action, shortcut) in [("undo", "undo:", "z"), ("cut", "cut:", "x"), ("copy", "copy:", "c"), ("paste", "paste:", "v"), ("select_all", "selectAll:", "a")] {
            edit.addItem(withTitle: tr(key), action: Selector(action), keyEquivalent: shortcut)
        }
        editItem.submenu = edit; bar.addItem(editItem)
        let viewItem = NSMenuItem(title: tr("view"), action: nil, keyEquivalent: "")
        let view = NSMenu(title: tr("view"))
        view.addItem(withTitle: tr("reload"), action: #selector(reload), keyEquivalent: "r").target = self
        viewItem.submenu = view; bar.addItem(viewItem)
        NSApp.mainMenu = bar
    }

    @objc private func checkForUpdates(_ item: NSMenuItem) {
        guard updateTask == nil, window.attachedSheet == nil else { return }
        item.title = tr("checking_updates")
        let info = Bundle.main.infoDictionary ?? [:]
        let installed = DesktopVersion(version: info["CFBundleShortVersionString"] as? String ?? "?",
            build: Int(info["CFBundleVersion"] as? String ?? "") ?? 0,
            source: info["AthenaSourceDirty"] as? Bool == false ? info["AthenaSourceRevision"] as? String : nil)
        let os = ProcessInfo.processInfo.operatingSystemVersion
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        updateTask = Task { @MainActor [weak self] in
            defer { self?.updateTask = nil; item.title = tr("check_updates") }
            let title: String
            do {
                let bytes = try await UpdateCheck.fetch()
                let result = try UpdateCheck.evaluate(bytes, installed: installed, architecture: architecture,
                    systemVersion: "\(os.majorVersion).\(os.minorVersion).\(os.patchVersion)")
                switch result {
                case .available(let latest): title = String(format: tr("update_available"), latest.label)
                case .current: title = tr("update_current")
                case .ahead: title = tr("update_ahead")
                case .incompatible(let minimum): title = String(format: tr("update_incompatible"), minimum)
                case .unknown: title = tr("update_unknown")
                }
            } catch { title = tr("update_failed") }
            guard let self, !self.terminating, !Task.isCancelled, self.window.attachedSheet == nil else { return }
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = String(format: tr("update_installed"), installed.label) + "\n\n" + tr("update_manual")
            alert.addButton(withTitle: tr("close"))
            alert.addButton(withTitle: tr("download_page"))
            alert.beginSheetModal(for: self.window) { response in
                if response == .alertSecondButtonReturn { NSWorkspace.shared.open(UpdateCheck.downloadURL) }
            }
        }
    }

    private func startBackend() {
        guard child == nil, let resources = Bundle.main.resourceURL else { return }
        startupGeneration += 1
        let generation = startupGeneration
        loadingLabel.stringValue = tr("starting")
        loadingLabel.isHidden = false
        webView.isHidden = true
        pendingOutput = Data()
        localURL = nil
        let process = Process()
        process.executableURL = resources.appendingPathComponent("python/bin/python3.14t")
        process.arguments = ["-I", "-B", resources.appendingPathComponent("launcher.py").path]
        // Never inherit operator API keys, PYTHONPATH or shell startup variables.
        let home = FileManager.default.homeDirectoryForCurrentUser
        let data = home.appendingPathComponent("Library/Application Support/Athena Desktop")
        process.environment = ["HOME": home.path, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                               "TMPDIR": NSTemporaryDirectory(), "LANG": "en_US.UTF-8",
                               "ATHENA_DATA_DIR": data.path, "PYTHONDONTWRITEBYTECODE": "1"]
        process.currentDirectoryURL = resources.appendingPathComponent("app")
        let pipe = Pipe()
        output = pipe
        process.standardOutput = pipe
        // Logs remain local and private; they are never bundled or uploaded.
        do {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
            let logs = data.appendingPathComponent("desktop.log")
            if let attrs = try? FileManager.default.attributesOfItem(atPath: logs.path),
               (attrs[.size] as? UInt64 ?? 0) > 2_000_000 { try? FileManager.default.removeItem(at: logs) }
            if !FileManager.default.fileExists(atPath: logs.path) {
                FileManager.default.createFile(atPath: logs.path, contents: nil, attributes: [.posixPermissions: 0o600])
            }
            let log = try FileHandle(forWritingTo: logs)
            try log.seekToEnd()
            process.standardError = log
            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let bytes = handle.availableData
                if bytes.isEmpty { handle.readabilityHandler = nil; return }
                Task { @MainActor [weak self] in self?.receive(bytes, generation: generation) }
            }
            process.terminationHandler = { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self, self.startupGeneration == generation else { return }
                    self.child = nil
                    if self.terminating { NSApp.reply(toApplicationShouldTerminate: true) }
                    else { self.showFailure() }
                }
            }
            child = process
            try process.run()
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: .seconds(45))
                guard let self, self.startupGeneration == generation, self.localURL == nil, !self.terminating else { return }
                self.child?.terminate()
            }
        } catch {
            child = nil
            showFailure()
        }
    }

    private func receive(_ bytes: Data, generation: Int) {
        guard generation == startupGeneration, localURL == nil else { return }
        pendingOutput.append(bytes)
        while let newline = pendingOutput.firstIndex(of: 10) {
            let line = pendingOutput.prefix(upTo: newline)
            pendingOutput.removeSubrange(...newline)
            guard let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  value["event"] as? String == "ready", let port = value["port"] as? Int, port > 0, port <= 65535,
                  let url = URL(string: "http://127.0.0.1:\(port)/") else { continue }
            localURL = url
            webView.load(URLRequest(url: url))
        }
        if pendingOutput.count > 65536 { pendingOutput = Data() }
    }

    private func showFailure() {
        guard !terminating else { return }
        loadingLabel.stringValue = tr("unavailable")
        loadingLabel.isHidden = false
        webView.isHidden = true
        let alert = NSAlert()
        alert.messageText = tr("unavailable")
        alert.informativeText = tr("recovery_detail")
        alert.addButton(withTitle: tr("retry"))
        alert.addButton(withTitle: tr("quit"))
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.startBackend() }
            else { NSApp.terminate(nil) }
        }
    }

    @objc private func reload() {
        if let localURL { webView.load(URLRequest(url: localURL)) }
        else if child == nil { startBackend() }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminating = true
        updateTask?.cancel()
        guard let child, child.isRunning else { return .terminateNow }
        child.terminate()
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(10))
            if child.isRunning {
                kill(child.processIdentifier, SIGKILL)
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url, let localURL else { return false }
        return url.scheme == localURL.scheme && url.host == localURL.host && url.port == localURL.port
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadingLabel.isHidden = true; webView.isHidden = false
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { reload() }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Generated sandbox frames stay inside WebKit. Only the trusted main document can navigate the shell.
        if action.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if isLocal(action.request.url) {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            if let url = action.request.url, ["https", "http", "mailto"].contains(url.scheme ?? ""),
               action.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, ["http", "https", "mailto"].contains(url.scheme ?? ""),
           action.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
        return nil
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        guard type == .microphone, frame.isMainFrame, origin.host == localURL?.host,
              origin.protocol == "http", origin.port == localURL?.port else { decisionHandler(.deny); return }
        decisionHandler(.prompt)
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard frame.isMainFrame, isLocal(frame.request.url) else { completionHandler(nil); return }
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.urls : nil) }
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }
}

@main
struct AthenaMain {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
