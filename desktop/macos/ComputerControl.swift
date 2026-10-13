import AppKit
import ApplicationServices

struct AccessibilitySnapshotRows {
    private(set) var rows: [[String: Any]] = []
    private var bytes = 0
    mutating func append(_ row: [String: Any]) -> Bool {
        guard let encoded = try? JSONSerialization.data(withJSONObject: row), bytes + encoded.count + 1 <= 450000 else { return false }
        bytes += encoded.count + 1
        rows.append(row)
        return true
    }
}

/// Whole-field replacement requires a complete observed value. AX has no atomic
/// compare-and-set; recheck immediately before dispatch to catch intervening edits.
struct ObservedControlText {
    let value: String?
    let truncated: Bool
    init(_ value: String?) {
        truncated = (value?.count ?? 0) > 1200
        self.value = value.map { String($0.prefix(1200)) }
    }
    var replaceable: Bool { value != nil && !truncated }
    func stillMatches(_ current: String?) -> Bool { replaceable && current == value }
}

/// Owned by the native application, never exposed to generated WebKit frames.
/// AX calls run on one serial queue so an unresponsive target cannot freeze input.
final class ComputerControl: @unchecked Sendable {
    private let queue = DispatchQueue(label: "com.featherhk.athena.accessibility")
    private let cancellationLock = NSLock()
    private var cancelled = Set<String>()
    private var active = Set<String>()
    private var revoked = false
    private var observations: [String: Observation] = [:]
    private struct Target {
        let element: AXUIElement
        let signature: [String]
        let actions: [String]
        let writable: Bool
        let text: ObservedControlText
    }
    private struct Observation {
        let pid: pid_t
        let launch: Date?
        let created: Date
        let targets: [String: Target]
    }

    func cancel(_ id: String) {
        cancellationLock.lock()
        if active.contains(id) { cancelled.insert(id) }
        cancellationLock.unlock()
    }

    func invalidate() {
        cancellationLock.lock(); revoked = true; cancellationLock.unlock()
    }

    private func isCancelled(_ id: String) -> Bool {
        cancellationLock.lock(); defer { cancellationLock.unlock() }
        return revoked || cancelled.contains(id)
    }

    func handle(_ request: [String: Any], reply: @escaping @Sendable ([String: Any]) -> Void) {
        guard let id = request["id"] as? String, id.count <= 64 else { return }
        cancellationLock.lock(); active.insert(id); cancellationLock.unlock()
        queue.async { [self] in
            defer { cancellationLock.lock(); cancelled.remove(id); active.remove(id); cancellationLock.unlock() }
            let result: [String: Any]
            if isCancelled(id) || (request["expires_at"] as? Double ?? 0) < Date().timeIntervalSince1970 {
                result = rejected("request_expired")
            } else if let args = request["args"] as? [String: Any] {
                result = operate(args, cancelled: { self.isCancelled(id) || (request["expires_at"] as? Double ?? 0) < Date().timeIntervalSince1970 })
            } else { result = rejected("invalid_request") }
            reply(["event": "computer_result", "id": id, "result": result])
        }
    }

    private func rejected(_ code: String, _ extra: [String: Any] = [:]) -> [String: Any] {
        ["status": "rejected", "code": code].merging(extra) { _, rhs in rhs }
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
    }
    private func text(_ element: AXUIElement, _ name: String) -> String {
        String((attribute(element, name) as? String ?? "").prefix(600))
    }
    private func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] {
        guard let value = attribute(element, name) else { return [] }
        if CFGetTypeID(value) == AXUIElementGetTypeID() { return [unsafeBitCast(value, to: AXUIElement.self)] }
        return value as? [AXUIElement] ?? []
    }
    private func signature(_ element: AXUIElement) -> [String] {
        [kAXRoleAttribute, kAXSubroleAttribute, kAXTitleAttribute, kAXDescriptionAttribute].map { text(element, $0) }
    }
    private func actions(_ element: AXUIElement) -> [String] {
        var values: CFArray?
        guard AXUIElementCopyActionNames(element, &values) == .success else { return [] }
        return Array((values as? [String] ?? []).filter { $0.count <= 100 }.prefix(16))
    }
    private func writable(_ element: AXUIElement, _ sig: [String]) -> Bool {
        guard ["AXTextField", "AXTextArea", "AXComboBox"].contains(sig[0]), sig[1] != "AXSecureTextField" else { return false }
        var settable = DarwinBoolean(false)
        return AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable) == .success && settable.boolValue
    }

    /// Does not prompt or change TCC. A user grants access to Athena in macOS settings.
    func operate(_ args: [String: Any], cancelled: () -> Bool = { false }) -> [String: Any] {
        let action = args["action"] as? String ?? "status"
        let trusted = AXIsProcessTrusted()
        if action == "status" {
            return ["status": "complete", "supported": true, "accessibility": trusted ? "granted" : "not_granted",
                    "application": Bundle.main.bundleIdentifier ?? "Athena",
                    "scope": "macOS accessibility elements; no screenshot, coordinate clicks or global keystrokes",
                    "permission_location": "System Settings > Privacy & Security > Accessibility > Athena"]
        }
        if action == "list" {
            let apps = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular && !$0.isTerminated }
            return ["status": "complete", "apps": apps.compactMap { app -> [String: Any]? in
                guard let id = app.bundleIdentifier else { return nil }
                return ["app_id": id, "name": app.localizedName ?? id, "active": app.isActive]
            }]
        }
        guard trusted else { return rejected("accessibility_not_granted", ["permission_location": "System Settings > Privacy & Security > Accessibility > Athena"]) }
        observations = observations.filter { Date().timeIntervalSince($0.value.created) < 30 }
        if action == "snapshot" {
            guard let appID = args["app_id"] as? String else { return rejected("app_id_required") }
            let apps = NSRunningApplication.runningApplications(withBundleIdentifier: appID).filter { !$0.isTerminated }
            guard apps.count == 1, let app = apps.first else { return rejected("app_not_running_or_ambiguous") }
            let root = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.3)
            var snapshotRoot = root
            let windowTitle = args["window_title"] as? String
            if let windowTitle {
                // Exact, unique scope. Never fall back to reading all windows.
                let windows = elements(root, kAXWindowsAttribute).filter { text($0, kAXTitleAttribute) == windowTitle }
                guard windows.count == 1, let window = windows.first else { return rejected("window_not_found_or_ambiguous") }
                snapshotRoot = window
            }
            let began = Date()
            var targets: [String: Target] = [:], seen: [AXUIElement] = []
            var snapshot = AccessibilitySnapshotRows()
            var pending: [(AXUIElement, Int, String?)] = [(snapshotRoot, 0, nil)]
            var truncated = false
            while !pending.isEmpty {
                if cancelled() { return rejected("request_cancelled") }
                if snapshot.rows.count >= 300 || Date().timeIntervalSince(began) > 6 { truncated = true; break }
                let (element, depth, parent) = pending.removeFirst()
                if depth > 12 { truncated = true; continue }
                if seen.contains(where: { CFEqual($0, element) }) { continue }
                seen.append(element)
                AXUIElementSetMessagingTimeout(element, 0.25)
                let sig = signature(element)
                if sig[0].isEmpty { continue }
                let ref = "@n\(snapshot.rows.count + 1)", ops = actions(element)
                let secure = sig[1] == "AXSecureTextField"
                let observedText = ObservedControlText(secure ? nil : attribute(element, kAXValueAttribute) as? String)
                let canWrite = writable(element, sig) && observedText.replaceable
                var row: [String: Any] = ["ref": ref, "role": sig[0], "subrole": sig[1], "title": sig[2],
                                        "description": sig[3], "actions": ops, "settable": canWrite]
                if let parent { row["parent"] = parent }
                if let enabled = attribute(element, kAXEnabledAttribute) as? Bool { row["enabled"] = enabled }
                // Secure fields expose neither values nor descendants.
                if !secure {
                    if let value = observedText.value {
                        row["value"] = value
                        row["value_truncated"] = observedText.truncated
                    }
                    var children = elements(element, kAXChildrenAttribute)
                    if depth == 0 && windowTitle == nil { children += elements(element, kAXWindowsAttribute) + elements(element, kAXMenuBarAttribute) }
                    if sig[0] == "AXWindow" {
                        children += elements(element, kAXCloseButtonAttribute) + elements(element, kAXMinimizeButtonAttribute)
                    }
                    let remaining = max(0, 600 - pending.count)
                    if children.count > remaining { truncated = true }
                    pending += children.prefix(remaining).map { ($0, depth + 1, ref) }
                }
                // Budget actual UTF-8 JSON bytes (CJK/emoji can take multiple bytes).
                // Leave headroom below the private pipe's 1 MiB framing limit.
                if !snapshot.append(row) { truncated = true; break }
                targets[ref] = Target(element: element, signature: sig, actions: ops, writable: canWrite, text: observedText)
            }
            guard !snapshot.rows.isEmpty, windowTitle != nil || snapshot.rows.count > 1 else { return rejected("application_not_accessible") }
            let id = UUID().uuidString
            observations = [id: Observation(pid: app.processIdentifier, launch: app.launchDate, created: Date(), targets: targets)]
            var result: [String: Any] = ["status": "complete", "observation_id": id, "app_id": appID, "nodes": snapshot.rows, "truncated": truncated, "expires_in_seconds": 30]
            if let windowTitle { result["window_title"] = windowTitle }
            return result
        }
        guard ["invoke", "set_value"].contains(action),
              let id = args["observation_id"] as? String, let ref = args["ref"] as? String,
              let observation = observations[id], let target = observation.targets[ref] else { return rejected("observation_expired") }
        // Consume the observation even if validation fails. No action by a guessed coordinate.
        observations.removeAll()
        guard let app = NSRunningApplication(processIdentifier: observation.pid), !app.isTerminated,
              app.launchDate == observation.launch, signature(target.element) == target.signature else { return rejected("target_changed") }
        if cancelled() { return rejected("request_cancelled") }
        let result: AXError
        if action == "invoke" {
            guard let name = args["native_action"] as? String, target.actions.contains(name), actions(target.element).contains(name) else { return rejected("action_not_observed") }
            if attribute(target.element, kAXEnabledAttribute) as? Bool == false { return rejected("target_disabled") }
            if cancelled() { return rejected("request_cancelled") }
            result = AXUIElementPerformAction(target.element, name as CFString)
        } else {
            guard target.writable, writable(target.element, target.signature), let value = args["text"] as? String, value.count <= 10000 else { return rejected("value_not_writable") }
            guard target.text.stillMatches(attribute(target.element, kAXValueAttribute) as? String) else { return rejected("value_changed") }
            if cancelled() { return rejected("request_cancelled") }
            result = AXUIElementSetAttributeValue(target.element, kAXValueAttribute as CFString, value as CFString)
        }
        if result == .success { return ["status": "complete", "effect": "action_dispatched", "verification": "Take a fresh snapshot to verify the requested outcome."] }
        // AX cannotComplete may mean a modal callback is still running. Never retry.
        return ["status": "unknown", "code": "accessibility_action_unconfirmed", "ax_error": result.rawValue,
                "verification": "Observe current state before deciding what to do next."]
    }
}
