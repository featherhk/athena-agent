import Foundation

/// Native capabilities belong to one exact HTTPS origin, never an iframe or redirect.
struct HostPolicy {
    let home: URL
    static let production = HostPolicy(home: URL(string: "https://chat.athena.featherhk.com/")!)

    /// User-requested recovery bypasses registered surfaces, while retaining login and data.
    var recoveryHome: URL {
        var components = URLComponents(url: home, resolvingAgainstBaseURL: false)!
        components.queryItems = (components.queryItems ?? []).filter { $0.name != "recovery" }
            + [URLQueryItem(name: "recovery", value: "1")]
        return components.url!
    }

    func canDownload(_ response: URLResponse) -> Bool {
        guard owns(response.url), let response = response as? HTTPURLResponse else { return false }
        // An expired login or missing attachment is an error, never a file to share.
        return (200..<300).contains(response.statusCode)
    }

    /// File links may be inline text/images. They must not replace the only
    /// conversation window when the web client requests a new browser tab.
    func isFileContent(_ url: URL?) -> Bool {
        guard owns(url), let url else { return false }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: false)
        return parts.count == 5 && parts[0].isEmpty && parts[1] == "api" && parts[2] == "files"
            && !parts[3].isEmpty && parts[3] != "." && parts[3] != ".." && parts[4] == "content"
    }

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
