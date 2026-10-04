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

        // An unsuccessful download may finish behind another native modal or in background.
        queue.failed()
        precondition(!queue.beginFailure(canPresent: false), "A failure attempted background/modal presentation")
        precondition(queue.beginFailure(canPresent: true), "The deferred failure disappeared")
        precondition(!queue.beginFailure(canPresent: true), "A failure opened overlapping alerts")
        queue.append(first)
        precondition(queue.begin(canPresent: true).isEmpty, "A share sheet opened over its failure alert")
        queue.finishFailure()
        precondition(!queue.beginFailure(canPresent: true), "An acknowledged failure was replayed")
        precondition(queue.begin(canPresent: true) == [first], "A file waiting behind an alert was lost")

        // This is the original lost-error case: a second transfer fails while sharing the first.
        queue.failed()
        queue.failed()
        queue.append(second)
        precondition(!queue.beginFailure(canPresent: true), "A failure overlapped an active share sheet")
        precondition(queue.finish() == [first], "Failure handling touched another transfer's file")
        precondition(queue.beginFailure(canPresent: true), "The failure was lost behind a share sheet")
        precondition(queue.begin(canPresent: true).isEmpty)
        queue.finishFailure()
        precondition(!queue.beginFailure(canPresent: true), "A background batch produced duplicate alerts")
        precondition(queue.begin(canPresent: true) == [second], "Failure acknowledgement lost a queued success")
        precondition(queue.finish() == [second])

        // A genuinely later failure must still be surfaced after the earlier alert closes.
        queue.failed()
        precondition(queue.beginFailure(canPresent: true))
        queue.failed()
        queue.finishFailure()
        precondition(queue.beginFailure(canPresent: true), "A later failure was swallowed by the current alert")
        queue.finishFailure()
        precondition(!queue.beginFailure(canPresent: true))
        print("iOS deferred/concurrent download and failure recovery queue checks passed.")
    }
}
