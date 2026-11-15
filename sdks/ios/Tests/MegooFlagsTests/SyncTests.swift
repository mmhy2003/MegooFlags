import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!
/// A context with targetingKey and attributes.
private func user(_ targetingKey: String, _ attributes: [String: Value] = [:]) -> ImmutableContext {
    ImmutableContext(targetingKey: targetingKey, structure: ImmutableStructure(attributes: attributes))
}

/// One evaluate result as JSON.
private func result(
    key: String = "f", value: String = "true", variant: String = "on", reason: String = "TARGETING_MATCH"
) -> String {
    #"{"key":"\#(key)","value":\#(value),"variant":"\#(variant)","reason":"\#(reason)","error_code":null,"error_message":null}"#
}

/// An evaluate body ({"results": [...]}) as JSON.
private func body(_ results: String...) -> String {
    #"{"results":[\#(results.joined(separator: ","))]}"#
}

private let off = result(value: "false", variant: "off")

/// Counts the calls Sync makes and keeps the hooks it was given.
private final class FakeStreamer: StreamControl, @unchecked Sendable {
    private let lock = NSLock()
    private var counts = [String: Int]()
    private var given: StreamerHooks?

    var hooks: StreamerHooks { lock.locked { given! } }

    func count(_ name: String) -> Int { lock.locked { counts[name, default: 0] } }

    func make(_ hooks: StreamerHooks) -> any StreamControl {
        lock.locked { given = hooks }
        return self
    }

    private func add(_ name: String) {
        lock.locked { counts[name, default: 0] += 1 }
    }

    func start() async { add("start") }
    func pause() async { add("pause") }
    func resume() async { add("resume") }
    func stop() async { add("stop") }
}

/// Keeps the events as "ready", "stale", "changed a,b", "fatal <error>",
/// "error <error>".
private final class EventLog: @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []
    private var stops = 0

    var lines: [String] { lock.locked { kept } }
    var fatalStops: Int { lock.locked { stops } }

    func add(_ e: SyncEvent) {
        let line: String
        switch e {
        case .ready: line = "ready"
        case .stale: line = "stale"
        case .changed(let flags): line = "changed \(flags.joined(separator: ","))"
        case .fatal(let error): line = "fatal \(error)"
        case .error(let error): line = "error \(error)"
        }
        lock.locked { kept.append(line) }
    }

    func stopped() {
        lock.locked { stops += 1 }
    }
}

/// A flag a task sets when it ends.
private final class Done: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool { lock.locked { value } }

    func set() {
        lock.locked { value = true }
    }
}

final class SyncTests: XCTestCase {
    private var transport: FakeTransport!
    private var clock: ManualClock!
    private var streamer: FakeStreamer!
    private var events: EventLog!
    private var store: Store!
    private var cacheDir: URL!
    private var sync: Sync!

    override func setUp() {
        transport = FakeTransport()
        transport.holdData = true
        clock = ManualClock()
        streamer = FakeStreamer()
        events = EventLog()
        store = Store()
        cacheDir = FileManager.default.temporaryDirectory.appendingPathComponent("megooflags-sync-\(UUID())")
    }

    override func tearDown() async throws {
        await sync?.stop()
        try? FileManager.default.removeItem(at: cacheDir)
    }

    private func cache() -> Cache {
        Cache(sdkKey: key, directory: cacheDir, logger: LineLogger())
    }

    @discardableResult
    private func make(
        bootstrap: String? = nil, offline: Bool = false, disableStream: Bool = false
    ) throws -> Sync {
        let config = try ResolvedConfig(
            MegooFlagsConfig(
                sdkKey: key, baseURL: base, bootstrap: bootstrap, offline: offline, disableStream: disableStream,
                logger: LineLogger()))
        let clock = clock!
        let streamer = streamer!
        let events = events!
        sync = Sync(
            config: config, store: store, cache: cache(), transport: transport,
            makeStreamer: { streamer.make($0) }, onEvent: { events.add($0) }, onFatalStop: { events.stopped() },
            rand: { 1 }, sleep: { try await clock.sleep($0) })
        return sync
    }

    /// started gives a Sync for user-1 whose first refresh answered f=true
    /// and whose delivery (stream, or poller with disableStream) started.
    private func started(disableStream: Bool = false) async throws {
        try make(bootstrap: body(result()), disableStream: disableStream)
        try await sync.start(context: user("user-1"))
        await transport.heldCount(1)
        transport.held[0].answer(200, body(result()), headers: ["ETag": #""e1""#])
        if disableStream {
            await clock.sleepers(1)
        } else {
            let streamer = streamer!
            await waitUntil("stream start") { streamer.count("start") == 1 }
        }
    }

    private func read(_ flag: String = "f") -> ProviderEvaluation<Bool> {
        resolve(store.get(), flag: flag, as: .boolean, default: false)
    }

    private func saveCache(for ctx: ImmutableContext, _ results: String, etag: String?) throws {
        let body = toRequestContext(ctx)
        let stored = StoredResults(
            results: try parseResults(Data(results.utf8), prefix: "test"), etag: etag, source: .network,
            contextHash: contextHash(body), targetingKey: nil)
        cache().save(stored)
    }

    private var cacheExists: Bool { FileManager.default.fileExists(atPath: cache().fileURL.path) }

    // MARK: start

    func testStartReturnsAtOnceWithBootstrapThenARefreshReplacesThem() async throws {
        try make(bootstrap: body(result(value: "false", variant: "off", reason: "STATIC")))

        try await sync.start(context: user("user-1"))

        XCTAssertEqual(read().value, false)
        XCTAssertEqual(read().variant, "off")
        XCTAssertEqual(read().reason, "STATIC")
        XCTAssertEqual(store.get()?.source, .bootstrap)
        await transport.heldCount(1)
        XCTAssertEqual(transport.held[0].context, .object(["key": .string("user-1")]))
        XCTAssertEqual(streamer.count("start"), 0)

        transport.held[0].answer(200, body(result()), headers: ["ETag": #""e1""#])
        let streamer = streamer!
        await waitUntil("stream start") { streamer.count("start") == 1 }

        XCTAssertEqual(read().value, true)
        XCTAssertEqual(read().reason, "TARGETING_MATCH")
        XCTAssertEqual(store.get()?.source, .network)
        XCTAssertEqual(events.lines, ["changed f"])
        let saved = try String(contentsOf: cache().fileURL, encoding: .utf8)
        XCTAssertTrue(saved.contains(#""etag":"\"e1\"""#), saved)
    }

    func testStartServesAMatchingCacheEntryOverBootstrapSendsItsETagAndA304MakesItFresh() async throws {
        try saveCache(for: user("user-1"), body(off), etag: #""c1""#)
        try make(bootstrap: body(result(reason: "STATIC")))

        try await sync.start(context: user("user-1"))

        XCTAssertEqual(read().value, false)
        XCTAssertEqual(read().reason, "CACHED")
        XCTAssertEqual(store.get()?.targetingKey, "user-1")
        await transport.heldCount(1)
        XCTAssertEqual(transport.held[0].etag, #""c1""#)

        transport.held[0].answer(304, headers: ["ETag": #""c1""#])
        let store = store!
        await waitUntil("fresh") { store.get()?.source == .network }

        XCTAssertEqual(read().value, false)
        XCTAssertEqual(read().reason, "TARGETING_MATCH")
        XCTAssertEqual(store.get()?.etag, #""c1""#)
        XCTAssertEqual(events.lines, [])
    }

    func testStartIgnoresACacheEntryForAnotherContext() async throws {
        try saveCache(for: user("user-2"), body(result()), etag: #""c1""#)
        try make()
        let done = Done()
        let sync = sync!

        Task {
            try await sync.start(context: user("user-1"))
            done.set()
        }
        await transport.heldCount(1)
        await settle()

        XCTAssertFalse(done.isSet)
        XCTAssertNil(store.get())
        XCTAssertNil(transport.held[0].etag)
    }

    func testStartWaitsForTheFirstEvaluateWhenThereAreNoValues() async throws {
        try make()
        let done = Done()
        let sync = sync!

        let start = Task {
            try await sync.start(context: user("user-1"))
            done.set()
        }
        await transport.heldCount(1)
        await settle()
        XCTAssertFalse(done.isSet)
        XCTAssertEqual(read().errorCode, .providerNotReady)

        transport.held[0].answer(200, body(result()))
        try await start.value

        XCTAssertEqual(read().value, true)
        XCTAssertEqual(events.lines, [])
        let streamer = streamer!
        await waitUntil("stream start") { streamer.count("start") == 1 }
    }

    func testStartFailsAfterInitTimeoutAndEmitsReadyWhenValuesArriveLater() async throws {
        try make()
        let sync = sync!

        let start = Task { try await sync.start(context: user("user-1")) }
        await transport.heldCount(1)
        await clock.sleepers(1)
        XCTAssertEqual(clock.sleeps, [5])
        await clock.advance(4.5)
        XCTAssertEqual(events.lines, [])
        await clock.advance(0.5)
        do {
            try await start.value
            XCTFail("start succeeded")
        } catch {
            XCTAssertEqual("\(error)", "megooflags: no flag values within 5s")
        }

        transport.held[0].answer(200, body(result()))
        let events = events!
        await waitUntil("ready") { events.lines == ["ready"] }

        XCTAssertEqual(read().value, true)
    }

    func testStartFailsWithAFatalErrorOn401AndRetriesNothing() async throws {
        try make()
        let sync = sync!

        let start = Task { try await sync.start(context: user("user-1")) }
        await transport.heldCount(1)
        transport.held[0].answer(401)
        do {
            try await start.value
            XCTFail("start succeeded")
        } catch {
            XCTAssertEqual(error as? SDKError, .fatal(401))
        }
        await clock.advance(300)

        XCTAssertEqual(transport.held.count, 1)
        XCTAssertEqual(streamer.count("start"), 0)
        XCTAssertEqual(events.fatalStops, 1)
        XCTAssertEqual(events.lines, [])
    }

    func testStartFailsOfflineWithoutBootstrapOrCache() async throws {
        try make(offline: true)

        do {
            try await sync.start(context: user("user-1"))
            XCTFail("start succeeded")
        } catch {
            XCTAssertEqual("\(error)", "megooflags: offline mode needs bootstrap values or a cache entry")
        }
    }

    func testStartsOfflineFromBootstrapWithoutAnyNetwork() async throws {
        try make(bootstrap: body(result()), offline: true)

        try await sync.start(context: user("user-1"))
        await sync.refresh()
        await clock.advance(120)

        XCTAssertEqual(read().value, true)
        XCTAssertEqual(transport.held.count, 0)
        XCTAssertEqual(streamer.count("start"), 0)
        XCTAssertEqual(clock.pending, 0)
    }

    // MARK: refresh

    func testRefreshRunsAtMostOneEvaluateInFlightAndOneQueued() async throws {
        try await started()

        streamer.hooks.onChanged("1")
        streamer.hooks.onChanged("2")
        streamer.hooks.onChanged("3")
        await transport.heldCount(2)
        await settle()
        XCTAssertEqual(transport.held.count, 2)

        transport.held[1].answer(200, body(result()))
        await transport.heldCount(3)
        transport.held[2].answer(200, body(result()))
        await clock.advance(120)

        XCTAssertEqual(transport.held.count, 3)
    }

    func testRefreshSendsTheStoredETagAndEmitsNoChangeWhenNothingChanged() async throws {
        try await started()

        streamer.hooks.onChanged("1")
        await transport.heldCount(2)
        XCTAssertEqual(transport.held[1].etag, #""e1""#)
        transport.held[1].answer(200, body(result()), headers: ["ETag": #""e2""#])
        let store = store!
        await waitUntil("new etag") { store.get()?.etag == #""e2""# }

        XCTAssertEqual(events.lines, [])
    }

    func testRefreshRetriesAFailureWithBackoffFrom1s() async throws {
        try await started()
        let sync = sync!

        let refresh = Task { await sync.refresh() }
        await transport.heldCount(2)
        transport.held[1].answer(500)
        await refresh.value
        XCTAssertEqual(events.lines, ["error megooflags: HTTP 500"])
        await clock.sleepers(1)
        XCTAssertEqual(clock.sleeps, [1])

        await clock.advance(0.5)
        XCTAssertEqual(transport.held.count, 2)
        await clock.advance(0.5)
        await transport.heldCount(3)

        transport.held[2].fail(URLError(.notConnectedToInternet))
        await clock.sleepers(1)
        XCTAssertEqual(clock.sleeps, [1, 2])
        await clock.advance(1.5)
        XCTAssertEqual(transport.held.count, 3)
        await clock.advance(0.5)
        await transport.heldCount(4)

        transport.held[3].answer(200, body(result()))
        await clock.advance(300)
        XCTAssertEqual(transport.held.count, 4)
    }

    func testRefreshWaitsRetryAfterAfterA429() async throws {
        try await started()

        streamer.hooks.onChanged("1")
        await transport.heldCount(2)
        transport.held[1].answer(429, headers: ["Retry-After": "7"])
        await clock.sleepers(1)
        await clock.advance(6.5)
        XCTAssertEqual(transport.held.count, 2)
        await clock.advance(0.5)

        await transport.heldCount(3)
        XCTAssertEqual(clock.sleeps, [7])
    }

    func testRefreshStopsEverythingOnAFatalAnswerAfterStart() async throws {
        try await started()

        streamer.hooks.onChanged("1")
        await transport.heldCount(2)
        transport.held[1].answer(403)
        let events = events!
        await waitUntil("fatal") { events.lines.count == 1 }
        await clock.advance(300)

        XCTAssertEqual(events.lines, ["fatal megooflags: SDK key refused (403)"])
        XCTAssertEqual(streamer.count("stop"), 1)
        XCTAssertEqual(events.fatalStops, 1)
        XCTAssertEqual(transport.held.count, 2)
        XCTAssertEqual(read().value, true)
    }

    func testRefreshStopsEverythingWhenTheStreamReportsTheKeyRevoked() async throws {
        try await started()

        streamer.hooks.onFatal(.revoked)
        streamer.hooks.onChanged("1")
        let events = events!
        await waitUntil("fatal") { events.lines.count == 1 }
        await clock.advance(300)

        XCTAssertEqual(events.lines, ["fatal megooflags: SDK key refused (revoked)"])
        XCTAssertEqual(events.fatalStops, 1)
        XCTAssertEqual(transport.held.count, 1)
    }

    // MARK: setContext

    func testSetContextClearsTheStoreAndTheCacheEntryAtOnceWhenTheTargetingKeyChanges() async throws {
        try await started()
        let store = store!
        XCTAssertTrue(cacheExists)
        let done = Done()
        let sync = sync!

        let change = Task {
            try await sync.setContext(user("user-2"))
            done.set()
        }
        await waitUntil("store cleared") { store.get() == nil }
        XCTAssertEqual(read().errorCode, .providerNotReady)
        XCTAssertFalse(cacheExists)
        await transport.heldCount(2)
        XCTAssertEqual(transport.held[1].context, .object(["key": .string("user-2")]))
        XCTAssertNil(transport.held[1].etag)
        XCTAssertFalse(done.isSet)

        transport.held[1].answer(200, body(off))
        try await change.value

        XCTAssertEqual(read().value, false)
        XCTAssertEqual(store.get()?.targetingKey, "user-2")
        XCTAssertEqual(events.lines, [])
    }

    func testSetContextKeepsServingTheOldValuesWhenOnlyAttributesChange() async throws {
        try await started()
        let sync = sync!
        let change = Task { try await sync.setContext(user("user-1", ["plan": .string("pro")])) }
        await transport.heldCount(2)

        XCTAssertEqual(read().value, true)
        XCTAssertEqual(transport.held[1].context, .object(["key": .string("user-1"), "plan": .string("pro")]))
        XCTAssertNil(transport.held[1].etag)
        transport.held[1].answer(200, body(off))
        try await change.value

        XCTAssertEqual(read().value, false)
        XCTAssertEqual(events.lines, ["changed f"])
    }

    func testSetContextIgnoresALateAnswerForTheOldContext() async throws {
        try await started()
        transport.honorsCancel = false
        streamer.hooks.onChanged("1")
        await transport.heldCount(2)
        let old = transport.held[1]
        let sync = sync!
        let change = Task { try await sync.setContext(user("user-1", ["plan": .string("pro")])) }
        await waitUntil("old request cancelled") {
            if case .object(let o)? = old.context { return o["plan"] == nil }
            return false
        }
        old.answer(200, body(result(key: "late")), headers: ["ETag": #""old""#])
        await transport.heldCount(3)

        XCTAssertNil(store.get()?.results["late"])
        XCTAssertNil(transport.held[2].etag)
        transport.held[2].answer(200, body(off))
        try await change.value

        XCTAssertEqual(read().value, false)
        XCTAssertNil(store.get()?.results["late"])
        XCTAssertEqual(transport.held.count, 3)
    }

    func testSetContextFailsWhenTheEvaluateForTheNewContextFailsAndKeepsRetrying() async throws {
        try await started()
        let sync = sync!

        let change = Task { try await sync.setContext(user("user-2")) }
        await transport.heldCount(2)
        transport.held[1].answer(503)
        do {
            try await change.value
            XCTFail("setContext succeeded")
        } catch {
            XCTAssertEqual(error as? SDKError, .http(503))
        }
        await clock.sleepers(1)
        await clock.advance(1)
        await transport.heldCount(3)
        transport.held[2].answer(200, body(result()))
        let store = store!
        await waitUntil("values") { store.get() != nil }

        XCTAssertEqual(transport.held.count, 3)
        XCTAssertEqual(read().value, true)
    }

    // MARK: stream fallback

    func testPollsAfterTwoStreamFailuresStopsOnReconnectAndEmitsStaleThenReady() async throws {
        try await started()
        let hooks = streamer.hooks
        let events = events!
        hooks.onConnected()

        hooks.onFailure(1, StreamFailure.ended)
        await settle()
        await clock.advance(60)
        XCTAssertEqual(transport.held.count, 1)
        XCTAssertEqual(events.lines, [])

        hooks.onFailure(2, StreamFailure.ended)
        await waitUntil("stale") { events.lines == ["stale"] }
        await clock.sleepers(1)
        await clock.advance(30)
        await transport.heldCount(2)
        transport.held[1].answer(200, body(result()))
        hooks.onFailure(3, StreamFailure.ended)
        await clock.sleepers(1)
        await clock.advance(30)
        await transport.heldCount(3)
        transport.held[2].answer(200, body(result()))

        hooks.onConnected()
        await waitUntil("ready") { events.lines == ["stale", "ready"] }
        await clock.advance(120)

        XCTAssertEqual(transport.held.count, 3)
        XCTAssertEqual(events.lines, ["stale", "ready"])
    }

    func testEmitsNoStaleForFailuresBeforeTheStreamEverConnected() async throws {
        try await started()

        streamer.hooks.onFailure(2, StreamFailure.ended)
        streamer.hooks.onConnected()
        await settle()

        XCTAssertEqual(events.lines, [])
    }

    func testPollsFromTheStartWithDisableStream() async throws {
        try await started(disableStream: true)
        XCTAssertEqual(streamer.count("start"), 0)

        await clock.advance(30)
        await transport.heldCount(2)
        transport.held[1].answer(200, body(result()))
        await clock.sleepers(1)
        await clock.advance(30)

        await transport.heldCount(3)
        XCTAssertEqual(clock.sleeps, [30, 30])
    }

    func testPauseStopsTheStreamAndThePollerAndResumeRestartsWhatRan() async throws {
        try await started()
        streamer.hooks.onFailure(2, StreamFailure.ended)
        await clock.sleepers(1)

        await sync.pause()
        await clock.advance(60)
        XCTAssertEqual(streamer.count("pause"), 1)
        XCTAssertEqual(transport.held.count, 1)
        XCTAssertEqual(clock.pending, 0)

        await sync.resume()
        await clock.sleepers(1)
        await clock.advance(30)

        XCTAssertEqual(streamer.count("resume"), 1)
        await transport.heldCount(2)
    }

    func testStopEndsTheStreamThePollerAndTheRetries() async throws {
        try await started()
        streamer.hooks.onFailure(2, StreamFailure.ended)
        await clock.sleepers(1)
        let sync = sync!
        Task { await sync.refresh() }
        await transport.heldCount(2)
        transport.held[1].answer(500)
        await clock.sleepers(2)

        await sync.stop()
        await clock.advance(300)

        XCTAssertEqual(streamer.count("stop"), 1)
        XCTAssertEqual(transport.held.count, 2)
        XCTAssertEqual(clock.pending, 0)
    }
}

/// Counts the ticks of a poller.
private final class Ticks: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0

    var count: Int { lock.locked { n } }

    func tick() {
        lock.locked { n += 1 }
    }
}

final class PollerTests: XCTestCase {
    func testCallsTickEveryIntervalWhileRunning() async {
        let clock = ManualClock()
        let ticks = Ticks()
        let p = Poller(interval: 30, sleep: { try await clock.sleep($0) }, tick: { ticks.tick() })

        p.start()
        p.start()
        XCTAssertTrue(p.running)
        await clock.sleepers(1)
        await clock.advance(60)
        XCTAssertEqual(ticks.count, 2)

        p.stop()
        XCTAssertFalse(p.running)
        await clock.advance(60)
        XCTAssertEqual(ticks.count, 2)
        XCTAssertEqual(clock.pending, 0)
    }

    func testNeverTicksMoreOftenThanEvery15s() async {
        let clock = ManualClock()
        let ticks = Ticks()
        let p = Poller(interval: 1, sleep: { try await clock.sleep($0) }, tick: { ticks.tick() })

        p.start()
        await clock.sleepers(1)
        await clock.advance(14.5)
        XCTAssertEqual(ticks.count, 0)
        await clock.advance(0.5)

        XCTAssertEqual(ticks.count, 1)
        XCTAssertEqual(clock.sleeps.first, 15)
        p.stop()
    }
}
