import Foundation
import CoreFoundation

struct DesktopVersion: Equatable {
    let version: String
    let build: Int
    let source: String?
    var label: String { "\(version) (\(build))" }
}

enum DesktopUpdate: Equatable {
    case available(DesktopVersion)
    case current
    case ahead
    case incompatible(String)
    case unknown
}

enum UpdateCheckError: Error { case invalidResponse }

/// Discovery only. Download destinations are owned by the shell, never the feed.
enum UpdateCheck {
    static let manifestURL = URL(string: "https://app.athena.featherhk.com/release.json")!
    static let downloadURL = URL(string: "https://app.athena.featherhk.com/")!

    static func components(_ version: String) -> [Int]? {
        let parts = version.split(separator: ".", omittingEmptySubsequences: false)
        guard (1...3).contains(parts.count), parts.allSatisfy({ !$0.isEmpty && $0.count <= 6 && $0.allSatisfy(\.isNumber) }) else { return nil }
        let numbers = parts.compactMap { Int($0) }
        guard numbers.count == parts.count else { return nil }
        return numbers + Array(repeating: 0, count: 3 - numbers.count)
    }

    static func evaluate(_ data: Data, installed: DesktopVersion, architecture: String, systemVersion: String) throws -> DesktopUpdate {
        guard data.count <= 16384,
              let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              value["status"] as? String == "limited-preview",
              let mac = value["macos"] as? [String: Any],
              let version = mac["version"] as? String, let release = components(version),
              let current = components(installed.version), installed.build > 0,
              let arch = mac["architecture"] as? String,
              let minimum = mac["minimum_macos"] as? String, let requirement = components(minimum),
              let system = components(systemVersion) else { throw UpdateCheckError.invalidResponse }
        guard arch == architecture, !system.lexicographicallyPrecedes(requirement) else { return .incompatible(minimum) }
        // Older preview manifests lack the build counter. A matching marketing
        // version alone cannot distinguish preview 1 from preview 9.
        let buildString: String?
        if let string = mac["build"] as? String { buildString = string }
        else if let number = mac["build"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() { buildString = number.stringValue }
        else { buildString = nil }
        guard let buildString, !buildString.isEmpty, buildString.count <= 9,
              buildString.allSatisfy(\.isNumber), let build = Int(buildString), build > 0 else { return .unknown }
        let source = mac["git_revision"] as? String
        let latest = DesktopVersion(version: version, build: build, source: source)
        if current.lexicographicallyPrecedes(release) || (current == release && installed.build < build) { return .available(latest) }
        if release.lexicographicallyPrecedes(current) || (current == release && installed.build > build) { return .ahead }
        guard let installedSource = installed.source, let source,
              source.range(of: "^[a-f0-9]{40}$", options: .regularExpression) != nil,
              installedSource == source, mac["git_dirty"] as? Bool == false else { return .unknown }
        return .current
    }

    static func fetch(configuration: URLSessionConfiguration = .ephemeral) async throws -> Data {
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 10
        let session = URLSession(configuration: configuration, delegate: UpdateRedirectPolicy(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: manifestURL, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await session.bytes(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              response.url == manifestURL, response.expectedContentLength <= 16384 else { throw UpdateCheckError.invalidResponse }
        var data = Data()
        for try await byte in bytes {
            guard data.count < 16384 else { throw UpdateCheckError.invalidResponse }
            data.append(byte)
        }
        return data
    }
}

private final class UpdateRedirectPolicy: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
