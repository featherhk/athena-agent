import Foundation

/// Session-local recovery state. It survives a sidecar restart but never changes chat data.
struct WindowRecovery {
    private(set) var active = false

    mutating func activate() { active = true }

    /// Attempt the default surface once; a second crash must wait for native user recovery.
    mutating func contentProcessFailed() -> Bool {
        guard !active else { return false }
        active = true
        return true
    }

    func request(for home: URL) -> URLRequest? {
        guard home.scheme == "http", home.host == "127.0.0.1", let port = home.port, (1...65535).contains(port),
              home.user == nil, home.password == nil, home.fragment == nil,
              var components = URLComponents(url: home, resolvingAgainstBaseURL: false) else { return nil }
        if active {
            components.queryItems = (components.queryItems ?? []).filter { $0.name != "recovery" }
                + [URLQueryItem(name: "recovery", value: "1")]
        }
        guard let url = components.url else { return nil }
        var request = URLRequest(url: url, cachePolicy: .reloadRevalidatingCacheData, timeoutInterval: 30)
        request.httpMethod = "GET"
        return request
    }
}
