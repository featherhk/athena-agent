import Foundation

@main
struct UpdateCheckTests {
    static func main() async throws {
        func expect(_ condition: @autoclosure () throws -> Bool) rethrows {
            let value = try condition(); precondition(value)
        }
        let source = String(repeating: "a", count: 40)
        let installed = DesktopVersion(version: "0.1.0", build: 9, source: source)
        func evaluate(_ changes: [String: Any] = [:], current: DesktopVersion? = nil,
                      architecture: String = "arm64", system: String = "13.0") throws -> DesktopUpdate {
            var mac: [String: Any] = ["version": "0.1.0", "build": "9", "architecture": "arm64",
                "minimum_macos": "13.0", "git_revision": source, "git_dirty": false]
            mac.merge(changes, uniquingKeysWith: { _, new in new })
            let data = try JSONSerialization.data(withJSONObject: ["status": "limited-preview", "macos": mac])
            return try UpdateCheck.evaluate(data, installed: current ?? installed, architecture: architecture, systemVersion: system)
        }
        try expect(try evaluate() == .current)
        // Marketing versions stay unchanged between preview builds.
        try expect(try evaluate(["build": "10"]) == .available(DesktopVersion(version: "0.1.0", build: 10, source: source)))
        try expect(try evaluate(["build": 10]) == .available(DesktopVersion(version: "0.1.0", build: 10, source: source)))
        try expect(try evaluate(["version": "0.2.0", "build": "1"]) == .available(DesktopVersion(version: "0.2.0", build: 1, source: source)))
        try expect(try evaluate(["build": "8"]) == .ahead)
        try expect(try evaluate(["version": "0.0.9", "build": "999"]) == .ahead)
        try expect(try evaluate(["version": "0.10.0"]) == .available(DesktopVersion(version: "0.10.0", build: 9, source: source)))
        // An old feed or re-used build number must never claim up-to-date.
        for missing in [NSNull(), "", "0", "-1", "1.5", "1000000000", true] as [Any] {
            try expect(try evaluate(["build": missing]) == .unknown)
        }
        try expect(try evaluate(["git_revision": String(repeating: "b", count: 40)]) == .unknown)
        try expect(try evaluate(["git_revision": NSNull()]) == .unknown)
        try expect(try evaluate(["git_dirty": true]) == .unknown)
        try expect(try evaluate(current: DesktopVersion(version: "0.1.0", build: 9, source: nil)) == .unknown)
        try expect(try evaluate(["minimum_macos": "14.0"]) == .incompatible("14.0"))
        try expect(try evaluate(architecture: "x86_64") == .incompatible("13.0"))
        try expect(try evaluate(system: "15.4.1") == .current)
        // Hostile destinations in a response are ignored: only fixed shell URLs
        // may be used, and discovery never returns an installer URL or command.
        try expect(try evaluate(["url": "file:///tmp/evil.app", "app_url": "https://untrusted.invalid/"]) == .current)
        precondition(UpdateCheck.downloadURL.absoluteString == "https://app.athena.featherhk.com/")
        precondition(UpdateCheck.manifestURL.absoluteString == "https://app.athena.featherhk.com/release.json")
        for invalid in [Data("{}".utf8), Data("not json".utf8), Data(repeating: 32, count: 16385)] {
            do {
                _ = try UpdateCheck.evaluate(invalid, installed: installed, architecture: "arm64", systemVersion: "13.0")
                preconditionFailure("Invalid discovery response was accepted")
            } catch {}
        }
        for invalid in ["13.beta", "13..1", "13.0.0.1", "", "9999999", "١٣"] { precondition(UpdateCheck.components(invalid) == nil) }
        let good = URLSessionConfiguration.ephemeral
        good.protocolClasses = [ValidFeed.self]
        let fetched = try await UpdateCheck.fetch(configuration: good)
        precondition(String(decoding: fetched, as: UTF8.self) == "{}")
        for failure in [RejectedFeed.self, OversizedFeed.self, RedirectedFeed.self, OfflineFeed.self] as [URLProtocol.Type] {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.protocolClasses = [failure]
            do {
                _ = try await UpdateCheck.fetch(configuration: configuration)
                preconditionFailure("Failed, redirected or oversized discovery response was accepted")
            } catch {}
        }
        print("Desktop update comparison, compatibility, source identity and destination boundary checks passed.")
    }
}

private class FeedProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}
    func reply(status: Int = 200, data: Data) {
        precondition(request.url == UpdateCheck.manifestURL)
        precondition(request.value(forHTTPHeaderField: "Authorization") == nil)
        precondition(request.value(forHTTPHeaderField: "Cookie") == nil)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
}
private final class ValidFeed: FeedProtocol {
    override func startLoading() { reply(data: Data("{}".utf8)) }
}
private final class RejectedFeed: FeedProtocol {
    override func startLoading() { reply(status: 503, data: Data("{}".utf8)) }
}
private final class OversizedFeed: FeedProtocol {
    override func startLoading() { reply(data: Data(repeating: 32, count: 16385)) }
}
private final class RedirectedFeed: FeedProtocol {
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: "HTTP/1.1", headerFields: ["Location": "https://untrusted.invalid/"])!
        client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: URL(string: "https://untrusted.invalid/")!), redirectResponse: response)
    }
}
private final class OfflineFeed: FeedProtocol {
    override func startLoading() { client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet)) }
}
