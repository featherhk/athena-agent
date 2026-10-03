import Foundation

@main
struct DownloadQueueTests {
    static func main() {
        let first = URL(fileURLWithPath: "/private/tmp/first/report.pdf")
        let second = URL(fileURLWithPath: "/private/tmp/second/report.pdf")
        let third = URL(fileURLWithPath: "/private/tmp/third/report.pdf")
        var queue = DownloadQueue()
        queue.append(first)
        precondition(queue.begin(canPresent: false).isEmpty, "Background/modal-blocked download presented")
        precondition(queue.begin(canPresent: true) == [first], "Deferred download was lost")
        queue.append(second)
        queue.append(third)
        precondition(queue.begin(canPresent: true).isEmpty, "Concurrent downloads opened overlapping share sheets")
        precondition(queue.finish() == [first], "Finished sharing deleted pending files")
        precondition(queue.finish().isEmpty, "A completed batch was released twice")
        precondition(queue.begin(canPresent: true) == [second, third], "Queued files were lost or confused by a duplicate filename")
        precondition(queue.finish() == [second, third])
        precondition(queue.begin(canPresent: true).isEmpty)
        print("iOS deferred/concurrent download queue checks passed.")
    }
}
