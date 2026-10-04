import Foundation

@main
struct HostPolicyTests {
    static func main() {
        let policy = HostPolicy.production
        for value in ["https://chat.athena.featherhk.com/", "https://chat.athena.featherhk.com:443/api/auth/login", "https://CHAT.ATHENA.FEATHERHK.COM/a?b=1"] {
        precondition(policy.owns(URL(string: value)), "Rejected the configured origin")
        }
        for value in ["http://chat.athena.featherhk.com/", "https://chat.athena.featherhk.com:444/", "https://chat.athena.featherhk.com.evil.example/", "https://evil.example/?next=https://chat.athena.featherhk.com", "https://user:password@chat.athena.featherhk.com", "file:///tmp/secret", "javascript:alert(1)", "https://app.athena.featherhk.com/"] {
            precondition(!policy.owns(URL(string:value)), "A different origin acquired native privileges")
        }
        precondition(!policy.owns(nil))
        precondition(policy.recoveryHome.absoluteString == "https://chat.athena.featherhk.com/?recovery=1")
        precondition(policy.owns(policy.recoveryHome), "Recovery changed the native capability origin")
        let customized = HostPolicy(home: URL(string: "https://chat.athena.featherhk.com/?lang=ja&recovery=0")!)
        let queries = URLComponents(url: customized.recoveryHome, resolvingAgainstBaseURL: false)!.queryItems!
        precondition(queries.filter { $0.name == "recovery" }.map(\.value) == ["1"])
        precondition(queries.contains(URLQueryItem(name: "lang", value: "ja")))
        for status in [200, 201, 206] {
            let response = HTTPURLResponse(url: policy.home, statusCode: status, httpVersion: nil, headerFields: nil)!
            precondition(policy.canDownload(response))
        }
        for status in [301, 401, 403, 404, 429, 500] {
            let response = HTTPURLResponse(url: policy.home, statusCode: status, httpVersion: nil, headerFields: nil)!
            precondition(!policy.canDownload(response), "An HTTP error/redirect was accepted as a shared file")
        }
        let external = HTTPURLResponse(url: URL(string: "https://example.com/file.pdf")!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        precondition(!policy.canDownload(external), "A foreign origin acquired file-sharing privileges")
        precondition(!policy.canDownload(URLResponse(url: policy.home, mimeType: nil, expectedContentLength: 0, textEncodingName: nil)))
        precondition(policy.canOpenExternally(URL(string:"https://example.com/")))
        precondition(!policy.canOpenExternally(URL(string:"file:///tmp/secret")))
        precondition(!policy.canOpenExternally(URL(string:"https://user:password@example.com/")))
        for value in ["https:word", "http:/relative", "javascript:alert(1)"] {
            precondition(!policy.canOpenExternally(URL(string: value)), "Malformed external URL reached a native presentation")
        }
        precondition(policy.canOpenExternally(URL(string:"MAILTO:hello@example.com")))
        precondition(policy.canOpenExternally(URL(string:"mailto:?subject=Hello")))
        precondition(HostPolicy.launch().home == policy.home, "A non-simulator build accepted a test endpoint")
        print("iOS origin and native capability boundary checks passed.")
    }
}
