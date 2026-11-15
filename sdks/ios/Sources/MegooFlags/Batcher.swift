// Sends the buffered exposures to POST /sdk/v1/events in the background: on
// an interval, at 1000 buffered items, on request (the app was hidden) and on
// close. No counts are ever sent: the server counts remote evaluations.
import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// The longest one request may take, in seconds, before it fails and is
/// retried.
private let requestTimeout: TimeInterval = 10

actor Batcher {
    private let baseURL: URL
    private let sdkKey: String
    private let transport: any HTTPTransport
    private let exposures: Exposures
    private let flushInterval: TimeInterval
    private let logger: any MegooFlagsLogger
    private let onFatal: @Sendable (SDKError) -> Void
    private let now: @Sendable () -> Date
    private let sleep: @Sendable (Double) async throws -> Void
    private var backoff: Backoff
    private var timer: Task<Void, Never>?
    private var retry: Task<Void, Never>?
    private var flushing: Task<Void, Never>?
    private var finalRun: Task<Void, Never>?
    private var closing: Task<Void, Never>?
    /// A flush before this time sends nothing (a retry wait runs).
    private var nextAttempt = Date.distantPast
    private var stopped = false

    init(
        baseURL: URL, sdkKey: String, transport: any HTTPTransport, exposures: Exposures,
        flushInterval: TimeInterval, logger: any MegooFlagsLogger, onFatal: @escaping @Sendable (SDKError) -> Void,
        now: @escaping @Sendable () -> Date = { Date() },
        rand: @escaping @Sendable () -> Double = { Double.random(in: 0..<1) },
        sleep: @escaping @Sendable (Double) async throws -> Void = { seconds in
            try await Task.sleep(nanoseconds: UInt64(max(seconds, 0) * 1_000_000_000))
        }
    ) {
        self.baseURL = baseURL
        self.sdkKey = sdkKey
        self.transport = transport
        self.exposures = exposures
        self.flushInterval = flushInterval
        self.logger = logger
        self.onFatal = onFatal
        self.now = now
        self.sleep = sleep
        backoff = Backoff(base: 1, max: 60, rand: rand)
    }

    /// start runs the flush timer and flushes at 1000 buffered items; it is
    /// idempotent.
    func start() {
        guard timer == nil, !stopped, closing == nil else { return }
        exposures.onFull = { [weak self] in
            Task { await self?.flush() }
        }
        timer = Task { [sleep, flushInterval] in
            while true {
                do {
                    try await sleep(flushInterval)
                } catch {
                    return
                }
                if Task.isCancelled {
                    return
                }
                await self.flush()
            }
        }
    }

    /// flush sends the buffered exposures in batches of at most 1000,
    /// joining the flush in flight if there is one. It sends nothing while
    /// a retry wait runs and stops at the first batch that must be retried.
    /// While closing it waits for the close.
    func flush() async {
        if let closing {
            await closing.value
            return
        }
        let run: Task<Void, Never>
        if let flushing {
            run = flushing
        } else {
            run = Task {
                await self.run(final: false)
                self.flushing = nil
            }
            flushing = run
        }
        await run.value
    }

    /// close stops the timers and flushes once, ignoring a retry wait, then
    /// stops for good; it returns within timeout seconds, cancelling a
    /// request still in flight. A second call waits for the first.
    func close(timeout: TimeInterval = 5) async {
        if let closing {
            await closing.value
            return
        }
        clearTimers()
        let previous = flushing
        let (done, signal) = AsyncStream.makeStream(of: Void.self)
        finalRun = Task {
            await previous?.value
            await self.run(final: true)
            signal.yield()
        }
        let c = Task { [sleep] in
            let deadline = Task {
                try? await sleep(timeout)
                signal.yield()
            }
            for await _ in done {
                break
            }
            deadline.cancel()
            self.stop()
        }
        closing = c
        await c.value
    }

    /// stop ends the timers and cancels a request in flight, sending
    /// nothing more.
    func stop() {
        stopped = true
        clearTimers()
        flushing?.cancel()
        finalRun?.cancel()
        exposures.onFull = nil
    }

    private func clearTimers() {
        timer?.cancel()
        retry?.cancel()
        timer = nil
        retry = nil
    }

    private func run(final: Bool) async {
        if !final && now() < nextAttempt {
            return
        }
        while !stopped && exposures.count > 0 {
            if let unsent = await send(exposures.drain(max: exposureBatchSize)) {
                if !stopped {
                    exposures.restore(unsent)
                }
                return
            }
        }
    }

    /// send posts batch, splitting it on 413, and returns what must be
    /// retried, after scheduling the retry.
    private func send(_ batch: [Exposure]) async -> [Exposure]? {
        if stopped {
            return nil
        }
        let body = canonicalJSON(.object(["exposures": .array(batch.map(\.json))]))
        var request = makeRequest(
            baseURL: baseURL, path: "events", sdkKey: sdkKey, method: "POST", accept: "application/json",
            body: Data(body.utf8))
        request.timeoutInterval = requestTimeout
        var failure: (any Error)?
        do {
            let (_, response) = try await transport.data(for: request)
            failure = statusError(status: response.statusCode, header: { response.value(forHTTPHeaderField: $0) })
        } catch {
            failure = error
        }
        if stopped {
            return nil
        }
        guard let failure else {
            backoff.reset()
            logger.log(level: .debug, message: "megooflags: sent \(batch.count) exposures")
            return nil
        }
        if let e = failure as? SDKError, case .fatal = e {
            stop()
            onFatal(e)
            return nil
        }
        switch failure as? SDKError {
        case .http(413)?:
            return await split(batch)
        case .http(let status)? where status < 500:
            logger.log(
                level: .warn,
                message: "megooflags: the server refused \(batch.count) exposures (HTTP \(status)); dropping them")
            return nil
        default:
            let wait: Double
            if case .retryAfter(let seconds)? = failure as? SDKError {
                wait = seconds
            } else {
                wait = backoff.next()
            }
            logger.log(
                level: .debug, message: "megooflags: sending exposures failed (\(failureReason(failure))); will retry")
            retryIn(wait)
            return batch
        }
    }

    /// split sends batch as two halves; a single item cannot be split and
    /// is dropped. When the first half must be retried, the second is kept
    /// behind it.
    private func split(_ batch: [Exposure]) async -> [Exposure]? {
        if batch.count <= 1 {
            logger.log(
                level: .warn,
                message: "megooflags: the server refused an exposure as too large (HTTP 413); dropping it")
            return nil
        }
        let half = batch.count / 2
        let second = Array(batch[half...])
        if let unsent = await send(Array(batch[..<half])) {
            return unsent + second
        }
        return await send(second)
    }

    private func retryIn(_ wait: Double) {
        nextAttempt = now().addingTimeInterval(wait)
        retry?.cancel()
        retry = Task { [sleep] in
            do {
                try await sleep(wait)
            } catch {
                return
            }
            self.nextAttempt = .distantPast
            await self.flush()
        }
    }
}
