import AppKit

/// Pointer routing for transparent panels. No mouse event is captured or reinjected.
/// Movement monitors update immediately; a short timer also handles programmatic cursor moves.
@MainActor
final class DesktopHitRegions {
    private final class Entry {
        weak var window: NSWindow?
        var regions: [NSRect]?
        init(_ window: NSWindow) { self.window = window }
    }
    static let shared = DesktopHitRegions()
    private var entries: [ObjectIdentifier: Entry] = [:]
    private var local: Any?
    private var global: Any?
    private var timer: Timer?

    func register(_ window: NSWindow) {
        entries[ObjectIdentifier(window)] = Entry(window)
        window.acceptsMouseMovedEvents = true
        if local == nil {
            local = NSEvent.addLocalMonitorForEvents(matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged]) { [weak self] event in
                MainActor.assumeIsolated { self?.refresh() }; return event
            }
            global = NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged]) { [weak self] _ in
                MainActor.assumeIsolated { self?.refresh() }
            }
        }
        visibilityChanged()
    }
    func visibilityChanged() {
        let anyVisible = entries.values.contains { $0.window?.isVisible == true }
        if anyVisible && timer == nil {
            let timer = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.refresh() } }
            RunLoop.main.add(timer, forMode: .common)
            self.timer = timer
        } else if !anyVisible { timer?.invalidate(); timer = nil }
        refresh()
    }
    #if ATHENA_NATIVE_CHECKS
    var fixtureTimerActive: Bool { timer?.isValid == true }
    #endif
    func unregister(_ window: NSWindow) {
        entries.removeValue(forKey: ObjectIdentifier(window))
        window.ignoresMouseEvents = false
        visibilityChanged()
        if entries.isEmpty {
            timer?.invalidate(); timer = nil
            if let local { NSEvent.removeMonitor(local); self.local = nil }
            if let global { NSEvent.removeMonitor(global); self.global = nil }
        }
    }
    func reset(_ window: NSWindow) { entries[ObjectIdentifier(window)]?.regions = nil; refresh() }
    @discardableResult
    func update(_ window: NSWindow, payload: Any?) -> Bool {
        guard let objects = payload as? [[String: Any]], objects.count <= 256, let entry = entries[ObjectIdentifier(window)] else { return false }
        var regions: [NSRect] = []
        for object in objects {
            var values: [Double] = []
            for key in ["x", "y", "width", "height"] {
                guard let value = object[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite,
                      abs(value.doubleValue) <= 1_000_000 else { return false }
                values.append(value.doubleValue)
            }
            guard values[2] >= 0, values[3] >= 0 else { return false }
            regions.append(NSRect(x: values[0], y: values[1], width: values[2], height: values[3]))
        }
        entry.regions = regions; refresh(); return true
    }
    static func accepts(point: NSPoint, regions: [NSRect]?, bounds: NSRect) -> Bool {
        guard bounds.contains(point) else { return false }
        return regions?.contains(where: { $0.intersection(bounds).contains(point) }) ?? true
    }
    func refresh() {
        let pointer = NSEvent.mouseLocation
        for entry in entries.values {
            guard let window = entry.window, let view = window.contentView else { continue }
            // Native sheets must remain usable regardless of the document's last hit map.
            guard window.isVisible, window.attachedSheet == nil else { window.ignoresMouseEvents = false; continue }
            let local = view.convert(window.convertPoint(fromScreen: pointer), from: nil)
            let point = NSPoint(x: local.x, y: view.isFlipped ? local.y : view.bounds.height - local.y)
            window.ignoresMouseEvents = !Self.accepts(point: point, regions: entry.regions, bounds: view.bounds)
        }
    }
}
