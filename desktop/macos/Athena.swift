import AppKit
@preconcurrency import WebKit

private func tr(_ key: String) -> String { NSLocalizedString(key, comment: "") }

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler, NSWindowDelegate {
    private var window: CompanionPanel!
    private var statusItem: NSStatusItem?
    private var shortcut: CompanionShortcut?
    private var companionMode = DesktopCompanion.Mode.avatar
    private var companionHeight: CGFloat?
    private var companionPlacement: [String: Any]?
    private var companionGestures: CompanionGestureView!
    private var pendingDesktopAction: String?
    private var webView: WKWebView!
    private var child: Process?
    private var output: Pipe?
    private var nativeInput: Pipe?
    private var computerControl = ComputerControl()
    private var pendingOutput = Data()
    private var localURL: URL?
    private var terminating = false
    private var quitRequest: UUID?
    private var startupGeneration = 0
    private var loadingLabel: NSTextField!
    private var updateTask: Task<Void, Never>?
    private var recovery = WindowRecovery()
    private let scene = DesktopSceneHost()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Opening a second copy only activates the running app; it never attaches to an arbitrary port.
        if let other = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier!)
            .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            other.activate(options: [.activateAllWindows]); NSApp.terminate(nil); return
        }
        makeMenu()
        makeWindow()
        makeStatusItem()
        shortcut = CompanionShortcut(diagnostic: DesktopCompanion.recordDiagnostic) { [weak self] in self?.toggleCompanion() }
        if shortcut?.register() == false {
            let unavailable = NSMenuItem(title: tr("shortcut_unavailable"), action: nil, keyEquivalent: "")
            unavailable.isEnabled = false
            statusItem?.menu?.insertItem(unavailable, at: 1)
        }
        presentOnLaunch()
        startBackend()
    }

    private func presentOnLaunch() {
        // An explicit launch must have a visible result, including after Quit.
        // Recalling Athena never opens the microphone.
        showCompanion(action: "present")
    }

    private func makeWindow() {
        companionPlacement = UserDefaults.standard.dictionary(forKey: DesktopCompanion.placementKey)
        let screen = placementScreen()?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1080, height: 780)
        window = CompanionPanel(contentRect: DesktopCompanion.frame(mode: companionMode, visibleFrame: screen,
                          anchor: DesktopCompanion.anchor(companionPlacement, area: screen)),
                          styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        window.title = "Athena"
        window.delegate = self
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.level = .floating
        window.isFloatingPanel = true
        window.hidesOnDeactivate = false
        window.becomesKeyOnlyIfNeeded = false
        window.isReleasedWhenClosed = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.userContentController.add(self, name: "athenaDesktop")
        configuration.userContentController.addUserScript(WKUserScript(source: DesktopCompanion.bootstrap,
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.underPageBackgroundColor = .clear
        webView.setValue(false, forKey: "drawsBackground")
        let container = NSView()
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor.clear.cgColor
        webView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.topAnchor.constraint(equalTo: container.topAnchor),
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
        companionGestures = CompanionGestureView()
        companionGestures.translatesAutoresizingMaskIntoConstraints = false
        companionGestures.activate = { [weak self] in self?.sendDesktopDetail(["action": "avatar"]) }
        companionGestures.moved = { [weak self] in self?.persistCompanionPlacement() }
        container.addSubview(companionGestures)
        NSLayoutConstraint.activate([
            companionGestures.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            companionGestures.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            companionGestures.topAnchor.constraint(equalTo: container.topAnchor),
            companionGestures.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])
        window.contentView = container
        DesktopHitRegions.shared.register(window)
        scene.onState = { [weak self] state in self?.sendDesktopDetail(["action": "state", "scope": self?.scene.observationScope ?? "", "desktop": state]) }
        scene.onInteraction = { [weak self] detail in
            if ["submit", "navigate"].contains(detail["action"] as? String ?? "") { self?.showCompanion() }
            self?.sendDesktopDetail(detail)
        }
        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged), name: NSApplication.didChangeScreenParametersNotification, object: nil)
    }

    @objc private func screensChanged() { resizeCompanion(companionMode, height: companionHeight); scene.screensChanged() }


    private func makeStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem?.button?.image = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "Athena")
        statusItem?.button?.toolTip = "Athena"
        let menu = NSMenu()
        let toggle = menu.addItem(withTitle: tr("toggle_companion"), action: #selector(toggleCompanion), keyEquivalent: "")
        toggle.target = self
        menu.addItem(withTitle: tr("new_chat"), action: #selector(newConversation), keyEquivalent: "n").target = self
        menu.addItem(withTitle: tr("history"), action: #selector(showHistory), keyEquivalent: "").target = self
        menu.addItem(withTitle: tr("account_settings"), action: #selector(showSettings), keyEquivalent: "").target = self
        menu.addItem(withTitle: tr("mac_control_settings"), action: #selector(showMacControlSettings), keyEquivalent: "").target = self
        menu.addItem(withTitle: tr("stop_work"), action: #selector(stopWork), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: tr("recover_window"), action: #selector(offerRecovery), keyEquivalent: "").target = self
        menu.addItem(withTitle: tr("check_updates"), action: #selector(checkForUpdates(_:)), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: tr("quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem?.menu = menu
    }

    private func resizeCompanion(_ mode: DesktopCompanion.Mode, height: CGFloat? = nil) {
        companionMode = mode
        companionHeight = height
        let screen = window.screen ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        let anchor = DesktopCompanion.anchor(companionPlacement, area: visible) ?? NSPoint(x: window.frame.midX, y: window.frame.minY)
        let frame = DesktopCompanion.frame(mode: mode, visibleFrame: visible, anchor: anchor, preferredHeight: companionHeight)
        guard window.frame != frame else { return }
        window.setFrame(frame, display: true)
        #if ATHENA_NATIVE_CHECKS
        fixtureFrameChanges += 1
        #endif
    }

    private func placementScreen() -> NSScreen? {
        NSScreen.screens.first { DesktopCompanion.screenID($0) == companionPlacement?["screen_id"] as? String }
            ?? window?.screen ?? NSScreen.main ?? NSScreen.screens.first
    }
    private func persistCompanionPlacement() {
        guard let screen = window.screen ?? NSScreen.main else { return }
        companionPlacement = DesktopCompanion.placement(frame: window.frame, screen: screen)
        UserDefaults.standard.set(companionPlacement, forKey: DesktopCompanion.placementKey)
        DesktopHitRegions.shared.refresh()
    }

    @objc private func toggleCompanion() {
        if window.isVisible && companionMode != .avatar { hideCompanion() } else { showCompanion() }
    }

    private func showCompanion(action: String = "show") {
        guard !terminating else { return }
        if !window.isVisible {
            let screen = placementScreen()
            if let visible = screen?.visibleFrame {
                let anchor = DesktopCompanion.anchor(companionPlacement, area: visible) ?? NSPoint(x: window.frame.midX, y: window.frame.minY)
                window.setFrame(DesktopCompanion.frame(mode: companionMode, visibleFrame: visible, anchor: anchor, preferredHeight: companionHeight), display: false)
            }
        }
        window.makeKeyAndOrderFront(nil)
        DesktopHitRegions.shared.visibilityChanged()
        scene.setPresented(true)
        if webView.isLoading || webView.isHidden || !isLocal(webView.url) { pendingDesktopAction = action }
        else { sendDesktopAction(action) }
    }

    private func hideCompanion() {
        guard window.attachedSheet == nil else { return }
        // Visibility is not cancellation. Keep this WebKit document, draft and sidecar alive.
        sendDesktopAction("hidden")
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('athena:background'))", completionHandler: nil)
        webView.setMicrophoneCaptureState(.none, completionHandler: nil)
        webView.pauseAllMediaPlayback(completionHandler: nil)
        window.orderOut(nil)
        DesktopHitRegions.shared.visibilityChanged()
        scene.setPresented(false)
    }

    @objc private func showHistory() { showCompanion(action: "history") }
    @objc private func showSettings() { showCompanion(action: "menu") }
    @objc private func showMacControlSettings() {
        // The person grants OS access. Opening this page does not alter TCC.
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }
    @objc private func newConversation() { showCompanion(action: "new") }
    @objc private func stopWork() { sendDesktopAction("stop") }

    private func sendDesktopAction(_ action: String) {
        guard ["present", "show", "new", "history", "menu", "stop", "reset", "hidden"].contains(action), isLocal(webView.url) else { return }
        sendDesktopDetail(["action": action])
    }

    private func sendDesktopDetail(_ detail: [String: Any]) {
        guard isLocal(webView.url), let data = try? JSONSerialization.data(withJSONObject: detail),
              let value = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('athena:desktop',{detail:\(value)}))", completionHandler: nil)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { hideCompanion(); return false }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "athenaDesktop", message.webView === webView,
              DesktopCompanion.trusts(frameIsMain: message.frameInfo.isMainFrame, frameURL: message.frameInfo.request.url,
                  origin: localURL, currentURL: webView.url),
              let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "resize":
            guard let value = body["mode"] as? String, let mode = DesktopCompanion.Mode(rawValue: value) else { return }
            var height: CGFloat?
            if let raw = body["height"] {
                guard let number = raw as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
                      (mode == .avatar ? 140...220 : 260...680).contains(number.doubleValue) else { return }
                height = number.doubleValue
            }
            resizeCompanion(mode, height: height)
        case "hide": hideCompanion()
        case "quit": NSApp.terminate(nil)
        case "hitRegions": DesktopHitRegions.shared.update(window, payload: body["regions"])
        case "companionDragRegions": companionGestures.updateRegions(body["regions"])
        case "observe": sendDesktopDetail(["action": "state", "scope": scene.observationScope, "desktop": scene.observation()])
        case "scene": if let localURL { scene.reconcile(body, origin: localURL) }
        case "sceneResult": scene.completeInteraction(body)
        case "sceneRefresh": scene.refreshDocuments(body)
        case "focusSurface": scene.focus(body)
        default: break
        }
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
        view.addItem(withTitle: tr("toggle_companion"), action: #selector(toggleCompanion), keyEquivalent: "").target = self
        view.addItem(withTitle: tr("new_chat"), action: #selector(newConversation), keyEquivalent: "n").target = self
        view.addItem(withTitle: tr("history"), action: #selector(showHistory), keyEquivalent: "").target = self
        view.addItem(withTitle: tr("account_settings"), action: #selector(showSettings), keyEquivalent: ",").target = self
        view.addItem(withTitle: tr("stop_work"), action: #selector(stopWork), keyEquivalent: ".").target = self
        view.addItem(.separator())
        view.addItem(withTitle: tr("reload"), action: #selector(reload), keyEquivalent: "r").target = self
        let recover = view.addItem(withTitle: tr("recover_window"), action: #selector(offerRecovery), keyEquivalent: "r")
        recover.keyEquivalentModifierMask = [.command, .shift]
        recover.target = self
        viewItem.submenu = view; bar.addItem(viewItem)
        NSApp.mainMenu = bar
    }

    @objc private func checkForUpdates(_ item: NSMenuItem) {
        guard updateTask == nil, window.attachedSheet == nil else { return }
        showCompanion()
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
                               "ATHENA_DATA_DIR": data.path, "PYTHONDONTWRITEBYTECODE": "1", "ATHENA_NATIVE_HOST": "1"]
        process.currentDirectoryURL = resources.appendingPathComponent("app")
        let pipe = Pipe()
        output = pipe
        process.standardOutput = pipe
        let input = Pipe()
        _ = fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
        nativeInput = input
        process.standardInput = input
        computerControl.invalidate()
        computerControl = ComputerControl()
        let control = computerControl
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
                control.invalidate()
                Task { @MainActor [weak self] in
                    guard let self, self.startupGeneration == generation else { return }
                    self.computerControl.invalidate()
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
        guard generation == startupGeneration else { return }
        pendingOutput.append(bytes)
        while let newline = pendingOutput.firstIndex(of: 10) {
            let line = pendingOutput.prefix(upTo: newline)
            pendingOutput.removeSubrange(...newline)
            guard let value = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            if value["event"] as? String == "computer_cancel", let id = value["id"] as? String {
                computerControl.cancel(id); continue
            }
            if value["event"] as? String == "computer_request", !terminating, let input = nativeInput {
                computerControl.handle(value) { response in
                    guard var bytes = try? JSONSerialization.data(withJSONObject: response) else { return }
                    bytes.append(10)
                    try? input.fileHandleForWriting.write(contentsOf: bytes)
                }
                continue
            }
            guard localURL == nil, value["event"] as? String == "ready", let port = value["port"] as? Int, port > 0, port <= 65535,
                  let url = URL(string: "http://127.0.0.1:\(port)/") else { continue }
            localURL = url
            reload()
        }
        if pendingOutput.count > 65536 { pendingOutput = Data() }
    }

    private func showFailure() {
        guard !terminating else { return }
        loadingLabel.stringValue = tr("unavailable")
        loadingLabel.isHidden = false
        webView.isHidden = true
        guard window.isVisible else { statusItem?.button?.toolTip = tr("unavailable"); return }
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
        if let localURL, let home = DesktopCompanion.home(localURL), let request = recovery.request(for: home) {
            DesktopHitRegions.shared.reset(window)
            webView.load(request)
        }
        else if child == nil { startBackend() }
    }

    @objc private func offerRecovery() {
        guard window.attachedSheet == nil else { return }
        showCompanion()
        let alert = NSAlert()
        alert.messageText = tr("recover_window")
        alert.informativeText = tr("recover_window_detail")
        alert.addButton(withTitle: tr("recover_confirm"))
        alert.addButton(withTitle: tr("close"))
        alert.beginSheetModal(for: window) { [weak self] response in
            guard response == .alertFirstButtonReturn, let self else { return }
            self.scene.closeAll()
            self.recovery.activate()
            self.sendDesktopAction("reset")
            self.resizeCompanion(.compact)
            self.reload()
        }
    }

    private func showPageFailure() {
        loadingLabel.stringValue = tr("page_unavailable")
        loadingLabel.isHidden = false
        webView.isHidden = true
        guard window.isVisible else { statusItem?.button?.toolTip = tr("page_unavailable"); return }
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = tr("page_unavailable")
        alert.informativeText = tr("page_recovery_detail")
        alert.addButton(withTitle: tr("retry"))
        alert.addButton(withTitle: tr("close"))
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.reload() }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showCompanion(); return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating || quitRequest != nil { return .terminateLater }
        if child?.isRunning != true && !isLocal(webView?.url) { return .terminateNow }
        let request = UUID()
        quitRequest = request
        guard let webView, isLocal(webView.url) else {
            beginTermination()
            return .terminateLater
        }
        // The WebView origin changes at every launch. Wait for its account-
        // scoped SQLite draft/intent receipt before stopping the local service.
        webView.callAsyncJavaScript("return window.__ATHENA_FLUSH_RESUME__ ? await window.__ATHENA_FLUSH_RESUME__() : true",
            arguments: [:], in: nil, in: .page) { [weak self] result in
            Task { @MainActor in
                guard let self, self.quitRequest == request else { return }
                if case .success(let value) = result, value as? Bool == true { self.beginTermination() }
                else { self.cancelTermination(request) }
            }
        }
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(5))
            guard let self, self.quitRequest == request, !self.terminating else { return }
            self.cancelTermination(request)
        }
        return .terminateLater
    }

    private func cancelTermination(_ request: UUID) {
        guard quitRequest == request, !terminating else { return }
        quitRequest = nil
        NSApp.reply(toApplicationShouldTerminate: false)
        webView.evaluateJavaScript("window.dispatchEvent(new Event('athena:quit-cancelled'))", completionHandler: nil)
        showCompanion()
        let alert = NSAlert()
        alert.messageText = tr("save_failed")
        alert.addButton(withTitle: tr("close"))
        alert.beginSheetModal(for: window, completionHandler: nil)
    }

    private func beginTermination() {
        terminating = true
        computerControl.invalidate()
        quitRequest = nil
        updateTask?.cancel()
        shortcut?.unregister()
        DesktopHitRegions.shared.unregister(window)
        scene.closeAll()
        sendDesktopAction("hidden")
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('athena:background'))", completionHandler: nil)
        guard let child, child.isRunning else { NSApp.reply(toApplicationShouldTerminate: true); return }
        child.terminate()
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(10))
            if child.isRunning {
                kill(child.processIdentifier, SIGKILL)
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url, let localURL else { return false }
        return url.scheme == localURL.scheme && url.host == localURL.host && url.port == localURL.port
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadingLabel.isHidden = true; webView.isHidden = false
        statusItem?.button?.toolTip = "Athena"
        if let action = pendingDesktopAction { pendingDesktopAction = nil; sendDesktopAction(action) }
        else { sendDesktopAction(window.isVisible ? (companionMode == .avatar ? "present" : "show") : "hidden") }
        sendDesktopDetail(["action": "state", "scope": scene.observationScope, "desktop": scene.observation()])
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if recovery.contentProcessFailed() { reload() }
        else { showPageFailure() }
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // Generated sandbox frames stay inside WebKit. Only the trusted main document can navigate the shell.
        if action.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if isLocal(action.request.url) {
            let fileLink = action.sourceFrame.isMainFrame && action.navigationType == .linkActivated
                && (action.request.httpMethod ?? "GET") == "GET" && DesktopCompanion.isFileContent(action.request.url, origin: localURL)
            decisionHandler(action.shouldPerformDownload || fileLink ? .download : .allow)
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
        guard isLocal(response.url), let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { completionHandler(nil); return }
        #if ATHENA_NATIVE_CHECKS
        if let destination = fixtureDownloadDestination { completionHandler(destination); return }
        #endif
        showCompanion()
        let panel = NSSavePanel()
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    #if ATHENA_NATIVE_CHECKS
    var fixtureDownloadDestination: URL?
    var fixtureCompletedDownloads = 0
    #endif
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse,
                  newRequest request: URLRequest, decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(isLocal(request.url) ? .allow : .cancel)
    }
    func downloadDidFinish(_ download: WKDownload) {
        #if ATHENA_NATIVE_CHECKS
        fixtureCompletedDownloads += 1
        #endif
    }

    #if ATHENA_NATIVE_CHECKS
    private(set) var fixtureFrameChanges = 0
    var fixtureIsTerminating: Bool { terminating }
    var fixtureScene: DesktopSceneHost { scene }
    var fixtureStatusMenu: NSMenu? { statusItem?.menu }
    var fixtureCompanionPlacement: [String: Any]? { companionPlacement }
    func fixtureCompanionGesture(from: NSPoint, to: NSPoint) { companionGestures.fixtureGesture(from: from, to: to) }
    func fixtureCompanionGestureHit(_ point: NSPoint) -> Bool {
        guard let container = window.contentView else { return false }
        let local = NSPoint(x: point.x, y: container.isFlipped ? point.y : container.bounds.height - point.y)
        return container.hitTest(container.convert(local, to: container.superview)) === companionGestures
    }
    /// Separate test executable only; it never starts a backend or opens an account.
    func configureNativeFixture(_ home: URL) -> (NSWindow, WKWebView) {
        precondition(recovery.request(for: home) != nil)
        UserDefaults.standard.removeObject(forKey: DesktopCompanion.placementKey)
        makeMenu()
        makeWindow()
        makeStatusItem()
        presentOnLaunch()
        localURL = home
        reload()
        return (window, webView)
    }
    #endif
}

#if !ATHENA_NATIVE_CHECKS
@main
struct AthenaMain {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}
#endif
