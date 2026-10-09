import AppKit
import Carbon
import WebKit

/// The panel can accept text without becoming an ordinary application window.
final class CompanionPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

enum DesktopCompanion {
    enum Mode: String { case compact, expanded }

    static func frame(mode: Mode, visibleFrame: NSRect, anchor: NSPoint? = nil, preferredHeight: CGFloat? = nil) -> NSRect {
        let margin: CGFloat = 12
        let area = visibleFrame.insetBy(dx: min(margin, visibleFrame.width / 4), dy: min(margin, visibleFrame.height / 4))
        let desired = mode == .compact ? NSSize(width: 560, height: 260) : NSSize(width: 760, height: min(680, max(260, preferredHeight ?? 680)))
        let size = NSSize(width: min(desired.width, area.width), height: min(desired.height, area.height))
        let center = anchor?.x ?? area.midX
        let bottom = anchor?.y ?? min(area.minY + 72, area.maxY - size.height)
        return NSRect(x: min(max(center - size.width / 2, area.minX), area.maxX - size.width),
                      y: min(max(bottom, area.minY), area.maxY - size.height), width: size.width, height: size.height)
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
}

/// Carbon hot keys do not need Accessibility permission or monitor ordinary keystrokes.
@MainActor
final class CompanionShortcut {
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let action: @MainActor () -> Void
    private(set) var registrationStatus: OSStatus = OSStatus(eventNotHandledErr)

    init(action: @escaping @MainActor () -> Void) { self.action = action }

    @discardableResult
    func register() -> Bool {
        guard hotKey == nil else { return true }
        var type = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let result = InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            var identifier = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                                    nil, MemoryLayout<EventHotKeyID>.size, nil, &identifier) == noErr,
                  identifier.signature == 0x4154484E, identifier.id == 1 else { return OSStatus(eventNotHandledErr) }
            let shortcut = Unmanaged<CompanionShortcut>.fromOpaque(context).takeUnretainedValue()
            MainActor.assumeIsolated { shortcut.action() }
            return noErr
        }, 1, &type, Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard result == noErr else { registrationStatus = result; return false }
        registrationStatus = RegisterEventHotKey(UInt32(kVK_Space), UInt32(optionKey),
            EventHotKeyID(signature: 0x4154484E, id: 1), GetApplicationEventTarget(), 0, &hotKey)
        if registrationStatus != noErr { unregister() }
        return registrationStatus == noErr
    }

    func unregister() {
        if let hotKey { UnregisterEventHotKey(hotKey); self.hotKey = nil }
        if let handler { RemoveEventHandler(handler); self.handler = nil }
    }
}
