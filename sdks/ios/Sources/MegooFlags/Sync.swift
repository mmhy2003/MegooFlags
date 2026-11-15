// Keeps the store current for the current context: the first values
// (bootstrap, cache, then the network), refreshes on stream changes, the
// polling fallback, context changes and the stop rules.
import Foundation
import OpenFeature

/// What Sync reports. The callback runs on Sync's actor and must return
/// quickly.
enum SyncEvent: Sendable {
    /// Values arrived after start failed, or the stream is back after stale.
    case ready
    /// The stream, connected before, keeps failing.
    case stale
    /// A refresh changed these flags (sorted) of the previous values.
    case changed([String])
    /// The key was refused after start; everything has stopped.
    case fatal(SDKError)
    /// A refresh failed; it is retried.
    case error(any Error)
}

/// The ways start, a refresh or a context change fail without a server
/// answer.
enum SyncError: Error, Equatable, Sendable, CustomStringConvertible, LocalizedError {
    /// No values within initTimeout seconds.
    case initTimeout(TimeInterval)
    case offlineWithoutValues
    case shutDown

    var description: String {
        switch self {
        case .initTimeout(let t):
            let seconds = t.rounded() == t && abs(t) < 1e15 ? String(Int64(t)) : String(t)
            return "megooflags: no flag values within \(seconds)s"
        case .offlineWithoutValues: return "megooflags: offline mode needs bootstrap values or a cache entry"
        case .shutDown: return "megooflags: provider shut down"
        }
    }

    var errorDescription: String? { description }
}

/// The part of Streamer that Sync drives.
protocol StreamControl: Sendable {
    func start() async
    func pause() async
    func resume() async
    func stop() async
}

extension Streamer: StreamControl {}

actor Sync {
    /// What the streamer's hooks report, in order.
    private enum StreamSignal: Sendable {
        case connected, changed
        case fatal(SDKError)
        case failure(Int)
    }

    /// How the first refresh of start ended, or that initTimeout passed.
    private enum FirstOutcome: Sendable {
        case settled((any Error)?)
        case timeout
    }

    /// Someone waiting for a refresh: a refresh() or setContext caller, or
    /// start's first refresh, after which delivery starts.
    private enum Waiter {
        case caller(CheckedContinuation<Void, any Error>)
        case first(AsyncStream<FirstOutcome>.Continuation)
    }

    private let store: Store
    private let cache: Cache?
    private let evaluator: Evaluator?
    private let makeStreamer: (@Sendable (StreamerHooks) -> any StreamControl)?
    private let onEvent: @Sendable (SyncEvent) -> Void
    private let onFatalStop: @Sendable () -> Void
    private let logger: any MegooFlagsLogger
    private let bootstrap: [EvaluateResult]?
    private let initTimeout: TimeInterval
    private let disableStream: Bool
    private let pollInterval: TimeInterval
    private let sleep: @Sendable (Double) async throws -> Void
    private var backoff: Backoff

    private var body: [String: JSONValue] = [:]
    private var hash = contextHash([:])
    /// Incremented by every context change; older answers are dropped.
    private var generation = 0
    /// The number of evaluate runs started.
    private var runs = 0
    private var inFlight: Task<Void, Never>?
    private var queued = false
    /// Each waiter is served by the first run numbered run or later.
    private var waiters: [(run: Int, waiter: Waiter)] = []
    private var retry: Task<Void, Never>?
    private var retryID = 0
    /// Set once stopped or fatal: the error later refreshes fail with.
    private var halted: (any Error)?
    private var starting = false
    /// start failed without a fatal error: emit ready when values arrive.
    private var readyPending = false
    private var deliveryStarted = false
    private var connected = false
    private var stale = false
    private var paused = false
    private var pollerWasRunning = false
    private var poller: Poller?
    private var streamer: (any StreamControl)?
    /// The streamer calls, run one after the other.
    private var streamerOps: Task<Void, Never>?
    private var signals: Task<Void, Never>?

    /// The cache is not used with disableCache; offline, nothing touches
    /// the network. makeStreamer builds the streamer with Sync's hooks once
    /// the first refresh settled.
    init(
        config: ResolvedConfig, store: Store, cache: Cache, transport: any HTTPTransport,
        makeStreamer: @escaping @Sendable (StreamerHooks) -> any StreamControl,
        onEvent: @escaping @Sendable (SyncEvent) -> Void, onFatalStop: @escaping @Sendable () -> Void,
        rand: @escaping @Sendable () -> Double = { Double.random(in: 0..<1) },
        sleep: @escaping @Sendable (Double) async throws -> Void = { seconds in
            try await Task.sleep(nanoseconds: UInt64(max(seconds, 0) * 1_000_000_000))
        }
    ) {
        self.store = store
        self.cache = config.disableCache ? nil : cache
        if config.offline {
            evaluator = nil
            self.makeStreamer = nil
        } else {
            evaluator = config.baseURL.map { Evaluator(baseURL: $0, sdkKey: config.sdkKey, transport: transport) }
            self.makeStreamer = makeStreamer
        }
        self.onEvent = onEvent
        self.onFatalStop = onFatalStop
        logger = config.logger
        bootstrap = config.bootstrap
        initTimeout = config.initTimeout
        disableStream = config.disableStream
        pollInterval = config.pollInterval
        self.sleep = sleep
        backoff = Backoff(base: 1, max: 60, rand: rand)
    }

    /// start serves bootstrap values, then a cache entry for context. With
    /// values it returns at once and refreshes in the background; without,
    /// it waits for the first refresh up to initTimeout. It throws the
    /// SDKError.fatal of a refused key, SyncError.initTimeout, the error of
    /// another failure, or SyncError.offlineWithoutValues; refreshes keep
    /// retrying unless fatal.
    nonisolated func start(context: (any EvaluationContext)?) async throws {
        try await start(body: toRequestContext(context))
    }

    private func start(body: [String: JSONValue]) async throws {
        starting = true
        defer { starting = false }
        setBody(body)
        let key = targetingKey(of: body)
        if let bootstrap {
            store.set(
                StoredResults(results: bootstrap, etag: nil, source: .bootstrap, contextHash: hash, targetingKey: key))
        }
        if let cached = cache?.load(contextHash: hash) {
            store.set(
                StoredResults(
                    results: Array(cached.results.values), etag: cached.etag, source: .cache, contextHash: hash,
                    targetingKey: key))
        }
        guard evaluator != nil else {
            if store.get() == nil {
                throw SyncError.offlineWithoutValues
            }
            return
        }
        let (outcomes, first) = AsyncStream.makeStream(of: FirstOutcome.self)
        waiters.append((runs + 1, .first(first)))
        kick()
        if store.get() != nil {
            return
        }
        let timeout = Task { [sleep, initTimeout] in
            do {
                try await sleep(initTimeout)
            } catch {
                return
            }
            first.yield(.timeout)
        }
        defer { timeout.cancel() }
        var outcome = FirstOutcome.timeout
        for await o in outcomes {
            outcome = o
            break
        }
        switch outcome {
        case .settled(nil):
            return
        case .settled(let error?):
            readyPending = halted == nil
            throw error
        case .timeout:
            readyPending = halted == nil
            throw SyncError.initTimeout(initTimeout)
        }
    }

    /// refresh evaluates the current context and returns when a run started
    /// after the call stored values or failed (a retry follows unless
    /// fatal). At most one evaluate is in flight and one queued; calls while
    /// one is queued share it. Offline or stopped it returns at once.
    func refresh() async {
        try? await request()
    }

    /// setContext switches to context. A new targeting key clears the store
    /// and the cache entry at once; other changes keep serving the old
    /// values until the new ones arrive. It returns when the new context's
    /// values are stored and throws when that evaluate fails, while retries
    /// continue. Offline it only records the context.
    nonisolated func setContext(_ context: (any EvaluationContext)?) async throws {
        try await setContext(body: toRequestContext(context))
    }

    private func setContext(body: [String: JSONValue]) async throws {
        if evaluator == nil || halted != nil {
            setBody(body)
            try await request()
            return
        }
        let keyChanged = targetingKey(of: body) != targetingKey(of: self.body)
        generation += 1
        setBody(body)
        inFlight?.cancel()
        if keyChanged {
            store.set(nil)
            cache?.remove()
        }
        try await request()
    }

    /// pause closes the stream and stops the poller (the app is hidden).
    func pause() async {
        guard halted == nil, !paused else { return }
        paused = true
        pollerWasRunning = poller?.running ?? false
        poller?.stop()
        streamerDo { await $0.pause() }
        await streamerIdle()
    }

    /// resume reopens the stream, and the poller when it was running.
    func resume() async {
        guard halted == nil, paused else { return }
        paused = false
        if !deliveryStarted {
            startDelivery()
        } else {
            streamerDo { await $0.resume() }
            if pollerWasRunning {
                pollerRef().start()
            }
        }
        await streamerIdle()
    }

    /// stop ends the stream, the poller and every refresh for good.
    func stop() async {
        halt(SyncError.shutDown)
        await streamerIdle()
    }

    private func setBody(_ b: [String: JSONValue]) {
        body = b
        hash = contextHash(b)
    }

    /// request waits for the next run, as refresh, and throws its error.
    private func request() async throws {
        if let halted {
            throw halted
        }
        guard evaluator != nil else { return }
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, any Error>) in
            waiters.append((runs + 1, .caller(c)))
            kick()
        }
    }

    /// kick starts a run, or queues one behind the run in flight.
    private func kick() {
        guard halted == nil, let evaluator else { return }
        if inFlight != nil {
            queued = true
            return
        }
        retry?.cancel()
        retry = nil
        runs += 1
        let run = runs
        let gen = generation
        let body = self.body
        let hash = self.hash
        let etag = store.get().flatMap { $0.contextHash == hash ? $0.etag : nil }
        inFlight = Task {
            let result: Result<EvaluateOutcome, any Error>
            do {
                result = .success(try await evaluator.evaluate(body: body, etag: etag))
            } catch {
                result = .failure(error)
            }
            self.finished(run: run, generation: gen, body: body, hash: hash, result: result)
        }
    }

    private func finished(
        run: Int, generation gen: Int, body: [String: JSONValue], hash: String,
        result: Result<EvaluateOutcome, any Error>
    ) {
        inFlight = nil
        if halted != nil {
            return
        }
        if gen == generation {
            let error: (any Error)?
            switch result {
            case .success(let outcome): error = apply(outcome, body: body, hash: hash)
            case .failure(let e): error = e
            }
            if let error {
                failed(run: run, error)
            } else {
                settle(run: run, nil)
            }
        }
        if queued && halted == nil {
            queued = false
            kick()
        }
    }

    /// apply stores an answer for the current context; it returns an error
    /// for a 304 it cannot use.
    private func apply(_ outcome: EvaluateOutcome, body: [String: JSONValue], hash: String) -> (any Error)? {
        let old = store.get()
        switch outcome {
        case .notModified(let etag):
            guard let old, old.contextHash == hash else {
                return SDKError.invalidResponse("304 without values")
            }
            store.set(
                StoredResults(
                    results: Array(old.results.values), etag: etag ?? old.etag, source: .network, contextHash: hash,
                    targetingKey: old.targetingKey))
        case .ok(let results, let etag):
            let next = StoredResults(
                results: results, etag: etag, source: .network, contextHash: hash, targetingKey: targetingKey(of: body))
            store.set(next)
            cache?.save(next)
            if readyPending {
                readyPending = false
                onEvent(.ready)
            } else if let old {
                let changed = changedFlags(old, next)
                if !changed.isEmpty {
                    onEvent(.changed(changed))
                }
            }
        }
        backoff.reset()
        return nil
    }

    private func failed(run: Int, _ error: any Error) {
        if let e = error as? SDKError, case .fatal = e {
            stopAll(e)
            return
        }
        logger.log(level: .debug, message: "megooflags: evaluate failed: \(failureReason(error))")
        onEvent(.error(error))
        settle(run: run, error)
        if queued {
            return
        }
        let wait: Double
        if case .retryAfter(let seconds)? = error as? SDKError {
            wait = seconds
        } else {
            wait = backoff.next()
        }
        retryID += 1
        let id = retryID
        retry = Task { [sleep] in
            do {
                try await sleep(wait)
            } catch {
                return
            }
            self.retryFired(id)
        }
    }

    private func retryFired(_ id: Int) {
        guard id == retryID, retry != nil else { return }
        retry = nil
        kick()
    }

    /// settle ends, with error or not, the waits served by run.
    private func settle(run: Int, _ error: (any Error)?) {
        var served: [Waiter] = []
        waiters.removeAll { w in
            guard w.run <= run else { return false }
            served.append(w.waiter)
            return true
        }
        for w in served {
            end(w, error, deliver: true)
        }
    }

    private func end(_ w: Waiter, _ error: (any Error)?, deliver: Bool) {
        switch w {
        case .caller(let c):
            if let error {
                c.resume(throwing: error)
            } else {
                c.resume()
            }
        case .first(let c):
            c.yield(.settled(error))
            c.finish()
            if deliver {
                startDelivery()
            }
        }
    }

    private func pollerRef() -> Poller {
        if let poller {
            return poller
        }
        let p = Poller(interval: pollInterval, sleep: sleep, tick: { [weak self] in await self?.refresh() })
        poller = p
        return p
    }

    /// startDelivery opens the stream, or starts the poller with
    /// disableStream; once, and not while paused.
    private func startDelivery() {
        guard halted == nil, !deliveryStarted, !paused else { return }
        deliveryStarted = true
        guard !disableStream, let makeStreamer else {
            pollerRef().start()
            return
        }
        let (stream, signal) = AsyncStream.makeStream(of: StreamSignal.self)
        streamer = makeStreamer(
            StreamerHooks(
                onConnected: { signal.yield(.connected) }, onChanged: { _ in signal.yield(.changed) },
                onFatal: { signal.yield(.fatal($0)) }, onFailure: { n, _ in signal.yield(.failure(n)) }))
        signals = Task { [weak self] in
            for await s in stream {
                guard let self else { return }
                await self.handle(s)
            }
        }
        streamerDo { await $0.start() }
    }

    private func handle(_ s: StreamSignal) {
        guard halted == nil else { return }
        switch s {
        case .connected:
            poller?.stop()
            connected = true
            if stale {
                stale = false
                onEvent(.ready)
            }
        case .changed:
            kick()
        case .fatal(let e):
            stopAll(e)
        case .failure(let n):
            guard n >= 2 else { return }
            if !paused {
                pollerRef().start()
            }
            if connected && !stale {
                stale = true
                onEvent(.stale)
            }
        }
    }

    /// streamerDo runs op on the streamer after the calls before it.
    private func streamerDo(_ op: @escaping @Sendable (any StreamControl) async -> Void) {
        guard let s = streamer else { return }
        let previous = streamerOps
        streamerOps = Task {
            await previous?.value
            await op(s)
        }
    }

    private func streamerIdle() async {
        let ops = streamerOps
        await ops?.value
    }

    /// stopAll stops everything for a refused key and keeps the store.
    /// While start runs, its error reports the key instead of a fatal event.
    private func stopAll(_ e: SDKError) {
        guard halted == nil else { return }
        halt(e)
        onFatalStop()
        if !starting {
            onEvent(.fatal(e))
        }
    }

    private func halt(_ error: any Error) {
        guard halted == nil else { return }
        halted = error
        inFlight?.cancel()
        retry?.cancel()
        retry = nil
        queued = false
        poller?.stop()
        streamerDo { await $0.stop() }
        signals?.cancel()
        let all = waiters
        waiters = []
        for w in all {
            end(w.waiter, error, deliver: false)
        }
    }
}
