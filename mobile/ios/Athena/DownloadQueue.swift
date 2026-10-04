import Foundation

/// Owned by AthenaController on the main actor; one native share sheet at a time.
struct DownloadQueue {
    private var pending: [URL] = []
    private var active: [URL] = []
    private var pendingFailure = false
    private var activeFailure = false

    mutating func append(_ url: URL) { pending.append(url) }

    /// Coalesce failures until they can be acknowledged; never discard one behind a modal.
    mutating func failed() { pendingFailure = true }

    mutating func beginFailure(canPresent: Bool) -> Bool {
        guard canPresent, active.isEmpty, !activeFailure, pendingFailure else { return false }
        pendingFailure = false
        activeFailure = true
        return true
    }

    mutating func finishFailure() { activeFailure = false }

    mutating func begin(canPresent: Bool) -> [URL] {
        guard canPresent, active.isEmpty, !activeFailure, !pending.isEmpty else { return [] }
        active = pending
        pending = []
        return active
    }

    /// Only the active batch is released. Downloads arriving during sharing stay queued.
    mutating func finish() -> [URL] {
        let completed = active
        active = []
        return completed
    }
}
