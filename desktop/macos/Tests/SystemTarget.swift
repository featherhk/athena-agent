import AppKit

/// A disposable native target for installed-Athena acceptance, with no real files.
/// Launch this app, ask Athena to edit/apply a note, sort items using its menu,
/// and close only the preview. Verify both AX observations and visible effects.
@MainActor final class SystemTarget: NSObject, NSApplicationDelegate {
    private var workspace: NSWindow!
    private var preview: NSWindow!
    private let note = NSTextField(string: "Initial synthetic note")
    private let saved = NSTextField(labelWithString: "Saved note: none")
    private let order = NSTextField(labelWithString: "Order: Pear, Apple, Plum")

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let application = NSMenuItem(); menu.addItem(application)
        application.submenu = NSMenu()
        application.submenu?.addItem(withTitle: "Quit System Target", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let arrange = NSMenuItem(title: "Arrange", action: nil, keyEquivalent: "")
        menu.addItem(arrange); arrange.submenu = NSMenu(title: "Arrange")
        let sort = NSMenuItem(title: "Sort by name", action: #selector(sortItems), keyEquivalent: "")
        sort.target = self; arrange.submenu?.addItem(sort)
        NSApp.mainMenu = menu

        workspace = makeWindow("Athena Test Workspace", origin: NSPoint(x: 80, y: 160))
        let content = workspace.contentView!
        let intro = NSTextField(labelWithString: "Synthetic data only — no personal files")
        intro.frame = NSRect(x: 24, y: 210, width: 445, height: 24); content.addSubview(intro)
        note.frame = NSRect(x: 24, y: 160, width: 340, height: 28)
        note.setAccessibilityLabel("Test note"); content.addSubview(note)
        let apply = NSButton(title: "Apply note", target: self, action: #selector(applyNote))
        apply.frame = NSRect(x: 365, y: 158, width: 115, height: 32); content.addSubview(apply)
        saved.frame = NSRect(x: 24, y: 108, width: 450, height: 30); content.addSubview(saved)
        order.frame = NSRect(x: 24, y: 60, width: 450, height: 30); content.addSubview(order)
        let longDraft = NSButton(title: "Load long test draft", target: self, action: #selector(loadLongDraft))
        longDraft.frame = NSRect(x: 24, y: 16, width: 190, height: 30); content.addSubview(longDraft)
        workspace.orderFront(nil)

        preview = makeWindow("Athena Test Image Preview", origin: NSPoint(x: 120, y: 460))
        let message = NSTextField(labelWithString: "Disposable preview — close this window only")
        message.frame = NSRect(x: 24, y: 120, width: 450, height: 30)
        preview.contentView?.addSubview(message); preview.orderFront(nil)
    }

    private func makeWindow(_ title: String, origin: NSPoint) -> NSWindow {
        let window = NSWindow(contentRect: NSRect(origin: origin, size: NSSize(width: 510, height: 260)),
                              styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = title; window.isReleasedWhenClosed = false
        return window
    }
    @objc private func applyNote() { saved.stringValue = "Saved note: " + note.stringValue }
    @objc private func sortItems() { order.stringValue = "Order: Apple, Pear, Plum" }
    @objc private func loadLongDraft() { note.stringValue = String(repeating: "Synthetic draft. ", count: 400) }
}

@main struct SystemTargetMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = SystemTarget()
        app.setActivationPolicy(.regular); app.delegate = delegate
        app.run()
    }
}
