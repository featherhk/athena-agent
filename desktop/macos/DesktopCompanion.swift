import AppKit
import Carbon
import WebKit

/// The panel can accept text without becoming an ordinary application window.
final class CompanionPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

enum DesktopCompanion {
    enum Mode: String { case avatar, compact, expanded }

    static let placementKey = "AthenaCompanionPlacement"

    static func screenID(_ screen: NSScreen) -> String {
        (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.stringValue ?? "0"
    }

    static func placement(frame: NSRect, screen: NSScreen) -> [String: Any] {
        let area = screen.visibleFrame
        return ["screen_id": screenID(screen), "center_x": min(1, max(0, (frame.midX - area.minX) / area.width)),
                "bottom_y": min(1, max(0, (frame.minY - area.minY) / area.height))]
    }

    static func anchor(_ placement: [String: Any]?, area: NSRect) -> NSPoint? {
        guard let placement, let x = placement["center_x"] as? NSNumber, let y = placement["bottom_y"] as? NSNumber,
              CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
              x.doubleValue.isFinite, y.doubleValue.isFinite, (0...1).contains(x.doubleValue), (0...1).contains(y.doubleValue) else { return nil }
        return NSPoint(x: area.minX + x.doubleValue * area.width, y: area.minY + y.doubleValue * area.height)
    }

    static func frame(mode: Mode, visibleFrame: NSRect, anchor: NSPoint? = nil, preferredHeight: CGFloat? = nil) -> NSRect {
        let margin: CGFloat = 12
        let area = visibleFrame.insetBy(dx: min(margin, visibleFrame.width / 4), dy: min(margin, visibleFrame.height / 4))
        // Answers grow upwards. The input must not slide sideways when a reply arrives.
        let desired = mode == .avatar
            ? NSSize(width: 180, height: min(220, max(140, preferredHeight ?? 170)))
            : NSSize(width: 560, height: min(680, max(260, preferredHeight ?? (mode == .compact ? 260 : 680))))
        let size = NSSize(width: min(desired.width, area.width), height: min(desired.height, area.height))
        let center = anchor?.x ?? area.midX
        let bottom = anchor?.y ?? min(area.minY + 72, area.maxY - size.height)
        return NSRect(x: min(max(center - size.width / 2, area.minX), area.maxX - size.width),
                      y: min(max(bottom, area.minY), area.maxY - size.height), width: size.width, height: size.height)
    }

    static func clamp(_ frame: NSRect, to visibleFrame: NSRect) -> NSRect {
        let area = visibleFrame.insetBy(dx: min(12, visibleFrame.width / 4), dy: min(12, visibleFrame.height / 4))
        let width = min(frame.width, area.width), height = min(frame.height, area.height)
        return NSRect(x: min(max(frame.minX, area.minX), area.maxX - width),
                      y: min(max(frame.minY, area.minY), area.maxY - height), width: width, height: height)
    }

    static func home(_ base: URL) -> URL? {
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        components.queryItems = (components.queryItems ?? []).filter { $0.name != "desktop" }
            + [URLQueryItem(name: "desktop", value: "companion")]
        return components.url
    }

    static func trusts(frameIsMain: Bool, frameURL: URL?, origin: URL?, currentURL: URL?) -> Bool {
        guard frameIsMain, let frameURL, let origin, let currentURL,
              origin.scheme == "http", origin.host == "127.0.0.1", let port = origin.port, (1...65535).contains(port) else { return false }
        for url in [frameURL, currentURL] {
            guard url.scheme == origin.scheme, url.host == origin.host, url.port == port,
                  url.user == nil, url.password == nil else { return false }
        }
        return true
    }

    static func isFileContent(_ url: URL?, origin: URL?) -> Bool {
        guard trusts(frameIsMain: true, frameURL: url, origin: origin, currentURL: origin), let url else { return false }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 5, parts[0].isEmpty, parts[1] == "api", parts[2] == "files", parts[4] == "content" else { return false }
        return parts[3].range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil
    }

    /// Only the application document receives this marker. Sandboxed component frames do not.
    static let bootstrap = """
    Object.defineProperty(window, '__ATHENA_DESKTOP__', {
      value: Object.freeze({version: 1}), writable: false, configurable: false
    });
    """

    /// Small, private native diagnostics; never include text, URLs or account details.
    static func recordDiagnostic(_ event: String, fields: [String: Int]) {
        let directory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Athena Desktop")
        let url = directory.appendingPathComponent("companion-native.log")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            if let size = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? UInt64, size > 256_000 {
                try FileManager.default.removeItem(at: url)
            }
        } catch { /* A missing log is normal on first launch. */ }
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let bytes = try? JSONSerialization.data(withJSONObject: ["time": Date().timeIntervalSince1970, "event": event, "values": fields]),
              let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        do { try handle.seekToEnd(); try handle.write(contentsOf: bytes + Data([10])) } catch { }
    }
}

/// A short press remains an owl click. Only a deliberate drag moves Athena;
/// input fields and the rest of the transparent panel never become drag handles.
@MainActor
final class CompanionGestureView: NSView {
    var regions: [NSRect] = []
    var activate: (() -> Void)?
    var moved: (() -> Void)?
    private var start: (frame: NSRect, pointer: NSPoint)?
    private var dragging = false
    override var isFlipped: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = convert(point, from: superview)
        return !isHidden && bounds.contains(local) && regions.contains(where: { $0.contains(local) }) ? self : nil
    }
    @discardableResult func updateRegions(_ payload: Any?) -> Bool {
        guard let objects = payload as? [[String: Any]], objects.count <= 16 else { return false }
        var next: [NSRect] = []
        for object in objects {
            var values: [CGFloat] = []
            for key in ["x", "y", "width", "height"] {
                guard let number = object[key] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
                      number.doubleValue >= 0, number.doubleValue <= 16384 else { return false }
                values.append(number.doubleValue)
            }
            next.append(NSRect(x: values[0], y: values[1], width: values[2], height: values[3]))
        }
        regions = next; return true
    }
    private func begin(pointer: NSPoint) { if let window { start = (window.frame, pointer); dragging = false } }
    private func move(pointer: NSPoint) {
        guard let start, let window else { return }
        let delta = NSPoint(x: pointer.x - start.pointer.x, y: pointer.y - start.pointer.y)
        guard dragging || hypot(delta.x, delta.y) >= 4 else { return }
        dragging = true
        let screen = NSScreen.screens.first { $0.frame.contains(pointer) } ?? window.screen ?? NSScreen.main
        guard let area = screen?.visibleFrame else { return }
        let target = DesktopCompanion.clamp(start.frame.offsetBy(dx: delta.x, dy: delta.y), to: area)
        window.setFrame(target, display: true)
    }
    private func end() {
        guard let start else { return }
        #if !ATHENA_NATIVE_CHECKS
        DesktopCompanion.recordDiagnostic("owl-pointer-up", fields: ["dragged": dragging ? 1 : 0,
            "moved": window?.frame.origin != start.frame.origin ? 1 : 0])
        #endif
        self.start = nil
        if dragging { moved?() } else { activate?() }
        dragging = false
    }
    override func mouseDown(with event: NSEvent) {
        guard let window else { return }
        #if !ATHENA_NATIVE_CHECKS
        DesktopCompanion.recordDiagnostic("owl-pointer-down", fields: [:])
        #endif
        begin(pointer: window.convertPoint(toScreen: event.locationInWindow))
    }
    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        // Mouse events can already be queued when we handle them. Reading the
        // global cursor here would assign every event the latest cursor position.
        move(pointer: window.convertPoint(toScreen: event.locationInWindow))
    }
    override func mouseUp(with event: NSEvent) {
        guard let window else { return }
        move(pointer: window.convertPoint(toScreen: event.locationInWindow)); end()
    }
    #if ATHENA_NATIVE_CHECKS
    func fixtureGesture(from: NSPoint, to: NSPoint) {
        guard let window else { return }
        func event(_ type: NSEvent.EventType, _ point: NSPoint) -> NSEvent {
            NSEvent.mouseEvent(with: type, location: window.convertPoint(fromScreen: point), modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: window.windowNumber, context: nil,
                eventNumber: 1, clickCount: 1, pressure: type == .leftMouseUp ? 0 : 1)!
        }
        mouseDown(with: event(.leftMouseDown, from))
        mouseDragged(with: event(.leftMouseDragged, NSPoint(x: (from.x + to.x) / 2, y: (from.y + to.y) / 2)))
        mouseDragged(with: event(.leftMouseDragged, to))
        mouseUp(with: event(.leftMouseUp, to))
    }
    #endif
}

/// Carbon hot keys do not need Accessibility permission or monitor ordinary keystrokes.
@MainActor
final class CompanionShortcut {
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let action: @MainActor () -> Void
    private let diagnostic: @MainActor (String, [String: Int]) -> Void
    private(set) var registrationStatus: OSStatus = OSStatus(eventNotHandledErr)
    private(set) var deliveryCount = 0

    init(diagnostic: @escaping @MainActor (String, [String: Int]) -> Void = { _, _ in }, action: @escaping @MainActor () -> Void) {
        self.diagnostic = diagnostic
        self.action = action
    }

    private func installHandler() -> OSStatus {
        guard handler == nil else { return noErr }
        var type = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        return InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            var identifier = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                    nil, MemoryLayout<EventHotKeyID>.size, nil, &identifier) == noErr,
                  identifier.signature == 0x4154484E, identifier.id == 1 else { return OSStatus(eventNotHandledErr) }
            let shortcut = Unmanaged<CompanionShortcut>.fromOpaque(context).takeUnretainedValue()
            MainActor.assumeIsolated {
                shortcut.deliveryCount += 1
                shortcut.diagnostic("shortcut-delivered", ["count": shortcut.deliveryCount])
                shortcut.action()
            }
            return noErr
        }, 1, &type, Unmanaged.passUnretained(self).toOpaque(), &handler)
    }

    @discardableResult
    func register() -> Bool {
        guard hotKey == nil else { return true }
        let result = installHandler()
        diagnostic("shortcut-handler-registration", ["status": Int(result)])
        guard result == noErr else { registrationStatus = result; return false }
        registrationStatus = RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey),
            EventHotKeyID(signature: 0x4154484E, id: 1), GetApplicationEventTarget(), 0, &hotKey)
        diagnostic("shortcut-registration", ["status": Int(registrationStatus), "key_code": Int(kVK_Space), "modifiers": Int(optionKey)])
        if registrationStatus != noErr { unregister() }
        return registrationStatus == noErr
    }

    func unregister() {
        if let hotKey { UnregisterEventHotKey(hotKey); self.hotKey = nil }
        if let handler { RemoveEventHandler(handler); self.handler = nil }
    }

    #if ATHENA_NATIVE_CHECKS
    // Test actual Carbon dispatch without synthesizing global keyboard input or taking a user's shortcut.
    func fixtureInstallHandler() -> OSStatus { installHandler() }
    #endif
}
