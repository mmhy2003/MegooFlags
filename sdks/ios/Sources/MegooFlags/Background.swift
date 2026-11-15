// Follows the app to the background and back: flushes the exposures when it
// is hidden, pauses the stream and the poller 30 s later and resumes them
// when it is visible again.
import Foundation

#if canImport(UIKit)
    import UIKit
#elseif canImport(AppKit)
    import AppKit
#endif

final class Background: @unchecked Sendable {
    /// What the controller reacts to, handled one after the other.
    private enum Signal: Sendable {
        case hidden, visible
        /// The pause timer numbered n fired.
        case graceElapsed(Int)
    }

    /// The seconds hidden before the stream and the poller pause.
    static let grace: TimeInterval = 30

    /// The notifications that mean hidden and visible on this platform: the
    /// app entering the background and the foreground on iOS, hidden and
    /// unhidden on macOS; nil elsewhere.
    static var platformNames: (hidden: Notification.Name, visible: Notification.Name)? {
        #if canImport(UIKit)
            (UIApplication.didEnterBackgroundNotification, UIApplication.willEnterForegroundNotification)
        #elseif canImport(AppKit)
            (NSApplication.didHideNotification, NSApplication.didUnhideNotification)
        #else
            nil
        #endif
    }

    private let center: NotificationCenter
    private let hidden: Notification.Name
    private let visible: Notification.Name
    private let sleep: @Sendable (Double) async throws -> Void
    private let flush: @Sendable () async -> Void
    private let pause: @Sendable () async -> Void
    private let resume: @Sendable () async -> Void
    private let lock = NSLock()
    private var observers: [any NSObjectProtocol] = []
    private var signals: AsyncStream<Signal>.Continuation?
    private var consumer: Task<Void, Never>?

    /// flush sends the exposures (the app was hidden); pause and resume
    /// close and reopen the stream and the poller.
    init(
        center: NotificationCenter, hidden: Notification.Name, visible: Notification.Name,
        sleep: @escaping @Sendable (Double) async throws -> Void = { seconds in
            try await Task.sleep(nanoseconds: UInt64(max(seconds, 0) * 1_000_000_000))
        },
        flush: @escaping @Sendable () async -> Void, pause: @escaping @Sendable () async -> Void,
        resume: @escaping @Sendable () async -> Void
    ) {
        self.center = center
        self.hidden = hidden
        self.visible = visible
        self.sleep = sleep
        self.flush = flush
        self.pause = pause
        self.resume = resume
    }

    /// start follows the notifications; it is idempotent.
    func start() {
        lock.lock()
        defer { lock.unlock() }
        guard consumer == nil else { return }
        let (stream, signal) = AsyncStream.makeStream(of: Signal.self)
        signals = signal
        observers = [
            center.addObserver(forName: hidden, object: nil, queue: nil) { _ in signal.yield(.hidden) },
            center.addObserver(forName: visible, object: nil, queue: nil) { _ in signal.yield(.visible) },
        ]
        consumer = Task { [sleep, flush, pause, resume] in
            var timer: Task<Void, Never>?
            var timerNumber = 0
            var paused = false
            for await s in stream {
                switch s {
                case .hidden:
                    Task { await flush() }
                    if !paused && timer == nil {
                        timerNumber += 1
                        let n = timerNumber
                        timer = Task {
                            do {
                                try await sleep(Background.grace)
                            } catch {
                                return
                            }
                            signal.yield(.graceElapsed(n))
                        }
                    }
                case .graceElapsed(let n):
                    guard n == timerNumber, timer != nil else { continue }
                    timer = nil
                    paused = true
                    await pause()
                case .visible:
                    timer?.cancel()
                    timer = nil
                    if paused {
                        paused = false
                        await resume()
                    }
                }
            }
            timer?.cancel()
        }
    }

    /// stop unsubscribes and cancels a pending pause.
    func stop() {
        lock.lock()
        let (observers, signals, consumer) = (self.observers, self.signals, self.consumer)
        self.observers = []
        self.signals = nil
        lock.unlock()
        for o in observers {
            center.removeObserver(o)
        }
        signals?.finish()
        consumer?.cancel()
    }
}
