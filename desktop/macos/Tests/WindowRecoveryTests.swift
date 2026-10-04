import Foundation

@main
struct WindowRecoveryTests {
    static func main() {
        let home = URL(string: "http://127.0.0.1:8123/")!
        var recovery = WindowRecovery()
        precondition(recovery.request(for: home)?.url == home)
        precondition(recovery.request(for: home)?.httpMethod == "GET")
        precondition(recovery.contentProcessFailed(), "First content-process failure did not choose safe recovery")
        precondition(recovery.request(for: home)?.url?.absoluteString == "http://127.0.0.1:8123/?recovery=1")
        precondition(!recovery.contentProcessFailed(), "A second crash created an automatic reload loop")
        precondition(recovery.request(for: home)?.httpBody == nil, "Recovery replayed a mutation payload")
        let changedPort = URL(string: "http://127.0.0.1:9234/?language=ja&recovery=0")!
        let request = recovery.request(for: changedPort)!
        precondition(request.url?.port == 9234, "Sidecar restart reused an untrusted obsolete port")
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
        precondition(query.filter { $0.name == "recovery" }.map(\.value) == ["1"])
        precondition(query.contains(URLQueryItem(name: "language", value: "ja")))
        var manual = WindowRecovery()
        manual.activate()
        precondition(!manual.contentProcessFailed(), "An already-safe page crash looped automatically")
        for value in ["https://example.com", "file:///tmp/app", "http://localhost:8123/", "http://127.0.0.1/", "http://user:pass@127.0.0.1:8123/", "http://127.0.0.1:8123/#other"] {
            precondition(recovery.request(for: URL(string: value)!) == nil, "Recovery accepted an origin unrelated to the launched sidecar")
        }
        print("macOS safe recovery, reload-loop guard, GET-only and sidecar-origin checks passed.")
    }
}
