// The polling fallback: refreshes the values on an interval while the stream
// is down, or always with disableStream.
import Foundation

final class Poller: @unchecked Sendable {
    /// The shortest poll interval, in seconds.
    static let minInterval: TimeInterval = 15

    private let interval: TimeInterval
    private let sleep: @Sendable (Double) async throws -> Void
    private let tick: @Sendable () async -> Void
    private let lock = NSLock()
    private var task: Task<Void, Never>?

    /// tick runs every interval (at least 15 s), each wait starting after
    /// the previous tick returned.
    init(
        interval: TimeInterval, sleep: @escaping @Sendable (Double) async throws -> Void,
        tick: @escaping @Sendable () async -> Void
    ) {
        self.interval = max(interval, Self.minInterval)
        self.sleep = sleep
        self.tick = tick
    }

    var running: Bool {
        lock.lock()
        defer { lock.unlock() }
        return task != nil
    }

    /// start schedules the first tick one interval from now; it is idempotent.
    func start() {
        lock.lock()
        defer { lock.unlock() }
        guard task == nil else { return }
        task = Task { [interval, sleep, tick] in
            while true {
                do {
                    try await sleep(interval)
                } catch {
                    return
                }
                if Task.isCancelled {
                    return
                }
                await tick()
                if Task.isCancelled {
                    return
                }
            }
        }
    }

    func stop() {
        lock.lock()
        defer { lock.unlock() }
        task?.cancel()
        task = nil
    }
}
