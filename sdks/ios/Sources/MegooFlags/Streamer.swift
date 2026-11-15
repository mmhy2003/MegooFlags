// The SSE client for GET /sdk/v1/stream: follows `changed` events and
// reconnects with backoff until it is paused, stopped or the key is refused.
import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// A connection that lasted this long, in seconds, resets the backoff.
private let stableAfter: TimeInterval = 60

/// What the streamer reports. The hooks run on the streamer's actor and must
/// return quickly.
struct StreamerHooks: Sendable {
    /// onConnected runs when the server answers 200.
    var onConnected: @Sendable () -> Void
    /// onChanged receives the id of each `changed` event.
    var onChanged: @Sendable (String) -> Void
    /// onFatal runs once when the key is refused (.fatal(status) or
    /// .revoked); the streamer is done.
    var onFatal: @Sendable (SDKError) -> Void
    /// onFailure receives the failures since the last 200 and the last error.
    var onFailure: @Sendable (Int, any Error) -> Void
}

/// StreamFailure is a stream the server closed.
enum StreamFailure: Error, CustomStringConvertible {
    case ended

    var description: String { "megooflags: stream ended" }
}

actor Streamer {
    private enum State {
        case idle, running, paused, done
    }

    /// What the transport delivers for one connection.
    private enum Delivery: Sendable {
        case response(HTTPURLResponse)
        case data(Data)
        case complete((any Error)?)
    }

    /// What the parser found in one chunk, in order.
    private final class Parsed {
        enum Item {
            case retry(Int)
            case event(SSEEvent)
        }

        var items: [Item] = []

        func take() -> [Item] {
            defer { items = [] }
            return items
        }
    }

    private let baseURL: URL
    private let sdkKey: String
    private let transport: any HTTPTransport
    private let logger: any MegooFlagsLogger
    private let hooks: StreamerHooks
    private let now: @Sendable () -> Date
    private let sleep: @Sendable (Double) async throws -> Void
    private var backoff: Backoff
    private var state = State.idle
    private var task: Task<Void, Never>?
    private var connection: (any CancelHandle)?
    private var connectionNumber = 0
    private var failures = 0

    /// lastEventID is the id of the last `changed` event, sent on reconnect.
    private(set) var lastEventID: String?

    init(
        baseURL: URL, sdkKey: String, transport: any HTTPTransport, logger: any MegooFlagsLogger,
        hooks: StreamerHooks, now: @escaping @Sendable () -> Date = { Date() },
        rand: @escaping @Sendable () -> Double = { Double.random(in: 0..<1) },
        sleep: @escaping @Sendable (Double) async throws -> Void = { seconds in
            try await Task.sleep(nanoseconds: UInt64(max(seconds, 0) * 1_000_000_000))
        }
    ) {
        self.baseURL = baseURL
        self.sdkKey = sdkKey
        self.transport = transport
        self.logger = logger
        self.hooks = hooks
        self.now = now
        self.sleep = sleep
        backoff = Backoff(base: 3, max: 60, rand: rand)
    }

    /// start opens the stream; only the first call does anything.
    func start() {
        if state == .idle {
            run()
        }
    }

    /// pause closes the connection and stops reconnecting until resume.
    func pause() {
        guard state == .running else { return }
        state = .paused
        cancel()
    }

    /// resume reconnects at once after pause, with Last-Event-ID.
    func resume() {
        if state == .paused {
            run()
        }
    }

    /// stop closes the connection for good.
    func stop() {
        state = .done
        cancel()
    }

    private func run() {
        state = .running
        task = Task { await self.loop() }
    }

    private func cancel() {
        task?.cancel()
        task = nil
        connection?.cancel()
        connection = nil
    }

    private func loop() async {
        while true {
            let (connectedAt, error) = await connect()
            if Task.isCancelled {
                return
            }
            if let fatal = error as? SDKError, fatal == .revoked || fatal.isFatal {
                state = .done
                cancel()
                hooks.onFatal(fatal)
                return
            }
            if let connectedAt, now().timeIntervalSince(connectedAt) >= stableAfter {
                backoff.reset()
            }
            failures += 1
            var wait = backoff.next()
            if case .retryAfter(let seconds)? = error as? SDKError {
                wait = max(seconds, wait)
            }
            logger.log(level: .debug, message: "megooflags: stream failed: \(failureReason(error))")
            hooks.onFailure(failures, error)
            do {
                try await sleep(wait)
            } catch {
                return
            }
            if Task.isCancelled {
                return
            }
        }
    }

    /// connect opens one stream and reads it until it ends, and returns when
    /// the server answered 200 (nil if it did not) and why it ended.
    private func connect() async -> (Date?, any Error) {
        var request = makeRequest(
            baseURL: baseURL, path: "stream", sdkKey: sdkKey, method: "GET", accept: "text/event-stream",
            lastEventID: lastEventID)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        var continuation: AsyncStream<Delivery>.Continuation?
        let deliveries = AsyncStream<Delivery> { continuation = $0 }
        // AsyncStream calls its build closure before init returns.
        let deliver = continuation!
        let handle = transport.stream(
            for: request, onResponse: { deliver.yield(.response($0)) }, onData: { deliver.yield(.data($0)) },
            onComplete: {
                deliver.yield(.complete($0))
                deliver.finish()
            })
        connectionNumber += 1
        let number = connectionNumber
        connection = handle
        defer {
            handle.cancel()
            if connectionNumber == number {
                connection = nil
            }
        }

        let parsed = Parsed()
        let parser = SSEParser(
            onLine: {}, onRetry: { parsed.items.append(.retry($0)) }, onEvent: { parsed.items.append(.event($0)) })
        var connectedAt: Date?
        for await delivery in deliveries {
            if Task.isCancelled {
                break
            }
            switch delivery {
            case .response(let r):
                let header = { (name: String) in r.value(forHTTPHeaderField: name) }
                let status = r.statusCode
                if let bad = statusError(status: status, header: header, now: now())
                    ?? (status == 200 ? nil : .http(status))
                {
                    return (nil, bad)
                }
                connectedAt = now()
                failures = 0
                logger.log(level: .debug, message: "megooflags: stream connected")
                hooks.onConnected()
            case .data(let chunk):
                guard connectedAt != nil else { continue }
                // onEvent never throws, so neither does feed.
                try? parser.feed(chunk)
                for item in parsed.take() {
                    switch item {
                    case .retry(let ms):
                        backoff.base = Double(ms) / 1000
                    case .event(let event) where event.name == "changed":
                        if !event.id.isEmpty {
                            lastEventID = event.id
                        }
                        hooks.onChanged(event.id)
                    case .event(let event) where event.name == "revoked":
                        return (connectedAt, SDKError.revoked)
                    case .event:
                        break
                    }
                }
            case .complete(let error):
                return (connectedAt, error ?? StreamFailure.ended)
            }
        }
        return (connectedAt, CancellationError())
    }
}

extension SDKError {
    /// isFatal is a refused key (401 or 403).
    fileprivate var isFatal: Bool {
        if case .fatal = self { return true }
        return false
    }
}

/// failureReason describes a stream failure for the debug log: an SDK
/// error's message without the prefix, else the error's domain and code.
private func failureReason(_ error: any Error) -> String {
    let text: String
    switch error {
    case let e as SDKError: text = e.description
    case let e as StreamFailure: text = e.description
    default:
        let ns = error as NSError
        return "\(ns.domain) \(ns.code)"
    }
    return text.hasPrefix("megooflags: ") ? String(text.dropFirst("megooflags: ".count)) : text
}
