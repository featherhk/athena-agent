import Foundation

/// Native capabilities belong to one exact HTTPS origin, never an iframe or redirect.
struct HostPolicy {
    let home: URL
    static let production = HostPolicy(home: URL(string: "https://chat.athena.featherhk.com/")!)

    func owns(_ url: URL?) -> Bool {
        guard let url, url.user == nil, url.password == nil else { return false }
        return url.scheme?.lowercased() == home.scheme?.lowercased()
            && url.host?.lowercased() == home.host?.lowercased()
            && (url.port ?? 443) == (home.port ?? 443)
    }

    func canOpenExternally(_ url: URL?) -> Bool {
        guard let url, url.user == nil, url.password == nil else { return false }
        switch url.scheme?.lowercased() {
        case "https", "http":
            // SFSafariViewController requires an actual web URL, not "https:word".
            return !(url.host ?? "").isEmpty
        case "mailto":
            // A blank recipient is valid (for example mailto:?subject=Hello).
            return true
        default:
            return false
        }
    }

    static func launch() -> HostPolicy {
        #if DEBUG && targetEnvironment(simulator)
        // Disposable local fixtures only. Release/device builds have no endpoint override.
        let arguments = ProcessInfo.processInfo.arguments
        if let index = arguments.firstIndex(of: "--athena-test-origin"), index + 1 < arguments.count,
           let url = URL(string: arguments[index + 1]), url.scheme == "http",
           ["127.0.0.1", "localhost"].contains(url.host ?? ""), url.port != nil,
           url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
           url.path.isEmpty || url.path == "/" {
            return HostPolicy(home: url)
        }
        #endif
        return .production
    }
}
