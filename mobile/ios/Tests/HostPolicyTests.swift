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
