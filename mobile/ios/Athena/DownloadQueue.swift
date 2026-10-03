import Foundation

/// Owned by AthenaController on the main actor; one native share sheet at a time.
struct DownloadQueue {
    private var pending: [URL] = []
    private var active: [URL] = []

    mutating func append(_ url: URL) { pending.append(url) }

    mutating func begin(canPresent: Bool) -> [URL] {
        guard canPresent, active.isEmpty, !pending.isEmpty else { return [] }
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
