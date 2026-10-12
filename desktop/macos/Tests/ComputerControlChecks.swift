import Foundation

/// Protocol checks only. No third-party windows, screenshots, TCC changes or input events.
@main struct ComputerControlChecks {
    static func main() {
        let host = ComputerControl()
        let status = host.operate(["action": "status"])
        precondition(status["status"] as? String == "complete")
        precondition(["granted", "not_granted"].contains(status["accessibility"] as? String ?? ""))
        var snapshot = AccessibilitySnapshotRows()
        let longRow: [String: Any] = ["title": String(repeating: "界", count: 600), "description": String(repeating: "面", count: 600), "value": String(repeating: "測", count: 1200)]
        for _ in 0..<300 { if !snapshot.append(longRow) { break } }
        precondition(snapshot.rows.count > 1 && snapshot.rows.count < 300)
        precondition(try! JSONSerialization.data(withJSONObject: snapshot.rows).count < 500000)
        let first = DispatchSemaphore(value: 0), block = DispatchSemaphore(value: 0), second = DispatchSemaphore(value: 0)
        host.handle(["id": "expired", "expires_at": 0.0, "args": ["action": "invoke"]]) { value in
            precondition((value["result"] as? [String: Any])?["code"] as? String == "request_expired")
            first.signal()
            _ = block.wait(timeout: .now() + 4)
        }
        precondition(first.wait(timeout: .now() + 4) == .success)
        // Queued cancellation is checked before any AX operation starts.
        host.handle(["id": "cancelled", "expires_at": Date().timeIntervalSince1970 + 5, "args": ["action": "invoke"]]) { value in
            precondition((value["result"] as? [String: Any])?["code"] as? String == "request_expired")
            second.signal()
        }
        host.cancel("cancelled")
        block.signal()
        precondition(second.wait(timeout: .now() + 4) == .success)
        host.invalidate()
        let revoked = DispatchSemaphore(value: 0)
        host.handle(["id": "revoked", "expires_at": Date().timeIntervalSince1970 + 5, "args": ["action": "invoke"]]) { value in
            precondition((value["result"] as? [String: Any])?["code"] as? String == "request_expired")
            revoked.signal()
        }
        precondition(revoked.wait(timeout: .now() + 4) == .success)
        print("Native control: non-prompting status, expiry, queued cancellation and host revocation passed. Cross-app effects not tested.")
    }
}
