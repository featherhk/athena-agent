import UIKit
@preconcurrency import WebKit
import SafariServices

private func tr(_ key: String) -> String { NSLocalizedString(key, comment: "") }

@main
final class AthenaApp: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let configuration = UISceneConfiguration(name: "Athena", sessionRole: session.role)
        configuration.delegateClass = AthenaScene.self
        return configuration
    }
}

final class AthenaScene: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: scene)
        window.rootViewController = AthenaController()
        self.window = window
        window.makeKeyAndVisible()
    }
    func sceneDidEnterBackground(_ scene: UIScene) {
        (window?.rootViewController as? AthenaController)?.enteredBackground()
    }
    func sceneDidBecomeActive(_ scene: UIScene) {
        (window?.rootViewController as? AthenaController)?.presentDownloads()
    }
}

@MainActor
final class AthenaController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, SFSafariViewControllerDelegate, UIAdaptivePresentationControllerDelegate {
    private let policy = HostPolicy.launch()
    private var webView: WKWebView!
    private let loading = UIActivityIndicatorView(style: .medium)
    private let recovery = UIStackView()
    private let recoveryTitle = UILabel()
    private var destinations: [ObjectIdentifier: URL] = [:]
    private var downloadQueue = DownloadQueue()
    private var showingFailure = false
    private let paper = UIColor(red: 0.973, green: 0.965, blue: 0.949, alpha: 1)

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = paper
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.applicationNameForUserAgent = "Athena/iOS"
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = paper
        webView.scrollView.backgroundColor = paper
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.allowsBackForwardNavigationGestures = false
        #if DEBUG
        webView.isInspectable = true
        #endif
        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor),
            webView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor)
        ])
        loading.translatesAutoresizingMaskIntoConstraints = false
        loading.accessibilityLabel = tr("starting")
        view.addSubview(loading)
        NSLayoutConstraint.activate([loading.centerXAnchor.constraint(equalTo: view.centerXAnchor), loading.centerYAnchor.constraint(equalTo: view.centerYAnchor)])
        makeRecovery()
        // An independent recovery gesture remains available even if page JavaScript fails.
        let recover = UILongPressGestureRecognizer(target: self, action: #selector(recoveryGesture(_:)))
        recover.numberOfTouchesRequired = 2
        recover.minimumPressDuration = 1.2
        webView.addGestureRecognizer(recover)
        loadHome()
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        presentDownloads()
    }

    private func makeRecovery() {
        recovery.axis = .vertical
        recovery.spacing = 18
        recovery.alignment = .center
        recovery.isHidden = true
        recoveryTitle.text = tr("unavailable")
        recoveryTitle.font = UIFont.preferredFont(forTextStyle: .title2)
        recoveryTitle.adjustsFontForContentSizeCategory = true
        recoveryTitle.numberOfLines = 0
        recoveryTitle.textAlignment = .center
        let detail = UILabel()
        detail.text = tr("connection_detail")
        detail.font = UIFont.preferredFont(forTextStyle: .body)
        detail.adjustsFontForContentSizeCategory = true
        detail.textColor = .secondaryLabel
        detail.numberOfLines = 0
        detail.textAlignment = .center
        let retry = UIButton(type: .system)
        retry.setTitle(tr("retry"), for: .normal)
        retry.titleLabel?.font = UIFont.preferredFont(forTextStyle: .headline)
        retry.addTarget(self, action: #selector(loadHome), for: .touchUpInside)
        for item in [recoveryTitle, detail, retry] { recovery.addArrangedSubview(item) }
        recovery.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(recovery)
        NSLayoutConstraint.activate([
            recovery.centerXAnchor.constraint(equalTo: view.centerXAnchor), recovery.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            recovery.leadingAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 32),
            recovery.trailingAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -32),
            recovery.widthAnchor.constraint(lessThanOrEqualToConstant: 420)
        ])
    }

    @objc private func loadHome() {
        showingFailure = false
        recovery.isHidden = true
        webView.isHidden = false
        loading.startAnimating()
        // Reload only a GET page. The host never retries a message, tool or model request.
        webView.load(URLRequest(url: policy.home, cachePolicy: .reloadRevalidatingCacheData, timeoutInterval: 30))
    }

    @objc private func recoveryGesture(_ gesture: UILongPressGestureRecognizer) {
        guard gesture.state == .began, presentedViewController == nil else { return }
        let alert = UIAlertController(title: "Athena", message: tr("reload_detail"), preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: tr("close"), style: .cancel) { [weak self, weak alert] _ in
            self?.finishPresentation(alert)
        })
        alert.addAction(UIAlertAction(title: tr("retry"), style: .default) { [weak self, weak alert] _ in
            self?.loadHome()
            self?.finishPresentation(alert)
        })
        present(alert, animated: true)
    }

    private func failure() {
        showingFailure = true
        loading.stopAnimating()
        webView.isHidden = true
        recovery.isHidden = false
        UIAccessibility.post(notification: .screenChanged, argument: recoveryTitle)
    }

    func enteredBackground() {
        // This preview has foreground voice only. Release media, never cancel server work.
        if policy.owns(webView.url) {
            webView.evaluateJavaScript("window.dispatchEvent(new Event('athena:background'))", completionHandler: nil)
        }
        webView.setMicrophoneCaptureState(.none, completionHandler: nil)
        webView.pauseAllMediaPlayback(completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading.stopAnimating()
        if !showingFailure { recovery.isHidden = true }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { failure() }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { failure() }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { failure() }

    private func openExternal(_ url: URL) {
        guard policy.canOpenExternally(url), presentedViewController == nil else { return }
        if url.scheme?.lowercased() == "mailto" { UIApplication.shared.open(url); return }
        let browser = SFSafariViewController(url: url)
        browser.delegate = self
        browser.presentationController?.delegate = self
        present(browser, animated: true)
    }

    nonisolated func safariViewControllerDidFinish(_ controller: SFSafariViewController) {
        Task { @MainActor [weak self] in self?.finishPresentation(controller) }
    }
    func presentationControllerDidDismiss(_ presentationController: UIPresentationController) { presentDownloads() }

    private func finishPresentation(_ controller: UIViewController?) {
        guard let controller else { presentDownloads(); return }
        if presentedViewController === controller {
            controller.dismiss(animated: true) { [weak self] in self?.presentDownloads() }
        } else {
            presentDownloads()
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.targetFrame?.isMainFrame == false {
            let scheme = action.request.url?.scheme
            decisionHandler(scheme == "about" || scheme == "blob" || policy.owns(action.request.url) ? .allow : .cancel)
            return
        }
        if policy.owns(action.request.url) {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            if action.sourceFrame.isMainFrame, action.navigationType == .linkActivated, let url = action.request.url { openExternal(url) }
            decisionHandler(.cancel)
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard policy.owns(response.response.url) || !response.isForMainFrame else { decisionHandler(.cancel); return }
        if response.isForMainFrame, let response = response.response as? HTTPURLResponse, !(200..<300).contains(response.statusCode) {
            failure(); decisionHandler(.cancel); return
        }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if action.sourceFrame.isMainFrame, action.navigationType == .linkActivated, let url = action.request.url {
            if policy.owns(url) { webView.load(action.request) } else { openExternal(url) }
        }
        return nil
    }
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        let expectedPort = policy.home.port ?? 443
        let originPort = origin.port == 0 && origin.protocol == "https" ? 443 : origin.port
        guard type == .microphone, frame.isMainFrame, policy.owns(frame.request.url),
              origin.protocol == policy.home.scheme, origin.host == policy.home.host, originPort == expectedPort else {
            decisionHandler(.deny); return
        }
        decisionHandler(.prompt)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse,
                  newRequest request: URLRequest, decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(policy.owns(request.url) ? .allow : .cancel)
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard policy.owns(response.url) else { completionHandler(nil); return }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("Athena-" + UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            let filename = (suggestedFilename as NSString).lastPathComponent
            let url = directory.appendingPathComponent(filename.isEmpty || filename == "." || filename == ".." ? "Athena-file" : filename)
            destinations[ObjectIdentifier(download)] = url
            completionHandler(url)
        } catch { completionHandler(nil) }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let url = destinations.removeValue(forKey: ObjectIdentifier(download)) else { return }
        downloadQueue.append(url)
        presentDownloads()
    }

    func presentDownloads() {
        let ready = isViewLoaded && view.window?.windowScene?.activationState == .foregroundActive && presentedViewController == nil
        if downloadQueue.beginFailure(canPresent: ready) {
            let alert = UIAlertController(title: tr("download_failed"), message: tr("download_detail"), preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: tr("close"), style: .default) { [weak self, weak alert] _ in
                guard let self else { return }
                self.downloadQueue.finishFailure()
                self.finishPresentation(alert)
            })
            present(alert, animated: true)
            return
        }
        let urls = downloadQueue.begin(canPresent: ready)
        guard !urls.isEmpty else { return }
        let sheet = UIActivityViewController(activityItems: urls, applicationActivities: nil)
        sheet.popoverPresentationController?.sourceView = view
        sheet.popoverPresentationController?.sourceRect = CGRect(x: view.bounds.midX, y: view.bounds.midY, width: 1, height: 1)
        sheet.completionWithItemsHandler = { [weak self, weak sheet] _, _, _, _ in
            guard let self else { return }
            for url in self.downloadQueue.finish() { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
            self.finishPresentation(sheet)
        }
        present(sheet, animated: true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let url = destinations.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        downloadQueue.failed()
        presentDownloads()
    }
}
