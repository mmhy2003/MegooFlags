import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!

/// Simulated time: sleep records the wait, advances now and returns at
/// once, unless hold is set; a held sleep ends when its task is cancelled.
private final class FakeClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current = Date(timeIntervalSince1970: 1_794_657_600)
    private var waits: [Double] = []
    private var holding = false

    var now: Date { lock.locked { current } }
    var sleeps: [Double] { lock.locked { waits } }
    var hold: Bool {
        get { lock.locked { holding } }
        set { lock.locked { holding = newValue } }
    }

    func advance(_ seconds: Double) {
        lock.locked { current += seconds }
    }

    func sleep(_ seconds: Double) async throws {
        lock.locked { waits.append(seconds) }
        while hold {
            try Task.checkCancellation()
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        try Task.checkCancellation()
        advance(seconds)
    }
}

/// Keeps every hook call as a line: "connected", "changed <id>", "fatal
/// <error>", "failure <n> <error>".
private final class HookLog: @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []

    var lines: [String] { lock.locked { kept } }
    var names: [String] { lines.map { String($0.split(separator: " ")[0]) } }

    func add(_ line: String) {
        lock.locked { kept.append(line) }
    }

    var hooks: StreamerHooks {
        StreamerHooks(
            onConnected: { self.add("connected") },
            onChanged: { self.add("changed \($0)") },
            onFatal: { self.add("fatal \($0)") },
            onFailure: { n, error in self.add("failure \(n) \(error)") })
    }
}

final class StreamerTests: XCTestCase {
    private var transport: FakeTransport!
    private var clock: FakeClock!
    private var log: HookLog!
    private var logger: LineLogger!
    private var streamer: Streamer!

    override func setUp() {
        transport = FakeTransport()
        clock = FakeClock()
        log = HookLog()
        logger = LineLogger()
        streamer = make(rand: { 1 })
    }

    override func tearDown() async throws {
        await streamer.stop()
    }

    private func make(rand: @escaping @Sendable () -> Double) -> Streamer {
        let clock = clock!
        return Streamer(
            baseURL: base, sdkKey: key, transport: transport, logger: logger, hooks: log.hooks,
            now: { clock.now }, rand: rand, sleep: { try await clock.sleep($0) })
    }

    private func connections(_ n: Int, file: StaticString = #filePath, line: UInt = #line) async {
        let transport = transport!
        await waitUntil("\(n) connections", file: file, line: line) { transport.connections.count >= n }
    }

    private func hooks(_ n: Int, file: StaticString = #filePath, line: UInt = #line) async {
        let log = log!
        await waitUntil("\(n) hook calls", file: file, line: line) { log.lines.count >= n }
    }

    func testOpensTheStreamWithTheSDKHeadersAndReportsTheConnection() async {
        await streamer.start()
        await hooks(1)
        let r = transport.last.request
        XCTAssertEqual(r.url?.absoluteString, "https://flags.example.com/sdk/v1/stream")
        XCTAssertEqual(r.httpMethod, "GET")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer \(key)")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "text/event-stream")
        XCTAssertEqual(r.value(forHTTPHeaderField: "User-Agent"), "megooflags-ios/\(megooFlagsVersion)")
        XCTAssertNil(r.value(forHTTPHeaderField: "Last-Event-ID"))
        XCTAssertEqual(r.cachePolicy, .reloadIgnoringLocalCacheData)
        XCTAssertEqual(log.lines, ["connected"])
        XCTAssertEqual(logger.lines, ["debug megooflags: stream connected"])
    }

    func testASecondStartOpensNothing() async {
        await streamer.start()
        await streamer.start()
        await hooks(1)
        await settle()
        XCTAssertEqual(transport.connections.count, 1)
    }

    func testCallsOnChangedWithTheIDAndResumesFromItOnTheNextConnection() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("event: changed\nid: 42\ndata: {}\n\n")
        await hooks(2)
        XCTAssertEqual(log.lines, ["connected", "changed 42"])
        let lastEventID = await streamer.lastEventID
        XCTAssertEqual(lastEventID, "42")
        transport.last.complete()
        await connections(2)
        XCTAssertEqual(transport.last.header("Last-Event-ID"), "42")
    }

    func testAChangedEventSplitAcrossChunksIsDispatchedOnce() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("event: chan")
        transport.last.send("ged\nid: 7\ndata: {}\n")
        transport.last.send("\n")
        await hooks(2)
        await settle()
        XCTAssertEqual(log.lines, ["connected", "changed 7"])
    }

    func testIgnoresUnknownEventsAndComments() async {
        await streamer.start()
        await hooks(1)
        transport.last.send(": keep-alive\n\nevent: hello\ndata: {}\n\ndata: plain\n\n")
        transport.last.send("event: changed\nid: 1\ndata: {}\n\n")
        await hooks(2)
        XCTAssertEqual(log.lines, ["connected", "changed 1"])
    }

    func testTheRetryFieldSetsTheNextWait() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("retry: 5000\n\n")
        transport.last.complete()
        await connections(2)
        XCTAssertEqual(clock.sleeps, [5])
    }

    func testTheRetryFieldNeverSetsAWaitUnder1Second() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("retry: 0\n\n")
        transport.last.complete()
        await connections(2)
        XCTAssertEqual(clock.sleeps, [1])
    }

    func testATimedOutReadIsAFailureAndReconnects() async {
        // URLSessionTransport's 45 s request timeout is the dead timer.
        await streamer.start()
        await hooks(1)
        transport.last.complete(URLError(.timedOut))
        await connections(2)
        XCTAssertEqual(log.names.prefix(2), ["connected", "failure"])
        XCTAssertEqual(clock.sleeps, [3])
        XCTAssertTrue(logger.lines.contains { $0.hasPrefix("debug megooflags: stream failed: ") }, "\(logger.lines)")
    }

    func testBacksOff3612UpTo60Seconds() async {
        for _ in 0..<7 {
            transport.replyStream(503)
        }
        await streamer.start()
        await connections(8)
        XCTAssertEqual(clock.sleeps, [3, 6, 12, 24, 48, 60, 60])
    }

    func testJittersLaterWaitsBetween80And100Percent() async {
        await streamer.stop()
        streamer = make(rand: { 0 })
        for _ in 0..<3 {
            transport.replyStream(503)
        }
        await streamer.start()
        await connections(4)
        XCTAssertEqual(clock.sleeps.count, 3)
        XCTAssertEqual(clock.sleeps[0], 3)
        XCTAssertEqual(clock.sleeps[1], 4.8, accuracy: 1e-9)
        XCTAssertEqual(clock.sleeps[2], 9.6, accuracy: 1e-9)
    }

    func testResetsTheBackoffAfterAConnectionThatLasted60Seconds() async {
        transport.replyStream(503)
        transport.replyStream(503)
        await streamer.start()
        await connections(3)
        await hooks(3)
        clock.advance(60)
        transport.last.complete()
        await connections(4)
        XCTAssertEqual(clock.sleeps, [3, 6, 3])
    }

    func testKeepsTheBackoffAfterAShortConnection() async {
        transport.replyStream(503)
        transport.replyStream(503)
        await streamer.start()
        await connections(3)
        await hooks(3)
        clock.advance(59)
        transport.last.complete()
        await connections(4)
        XCTAssertEqual(clock.sleeps, [3, 6, 12])
    }

    func testOnFailureCountsConsecutiveFailuresAndTheCountResetsOnConnect() async {
        transport.replyStream(503)
        transport.replyStream(500)
        await streamer.start()
        await hooks(3)
        transport.last.complete()
        await hooks(4)
        XCTAssertEqual(
            log.lines.prefix(4),
            ["failure 1 megooflags: HTTP 503", "failure 2 megooflags: HTTP 500", "connected", log.lines[3]])
        XCTAssertTrue(log.lines[3].hasPrefix("failure 1 "), log.lines[3])
    }

    func testAReadErrorIsAFailure() async {
        await streamer.start()
        await hooks(1)
        transport.last.complete(URLError(.networkConnectionLost))
        await hooks(2)
        XCTAssertEqual(log.names.prefix(2), ["connected", "failure"])
        await connections(2)
    }

    func test401And403CallOnFatalOnceAndNeverReconnect() async {
        for status in [401, 403] {
            await streamer.stop()
            transport = FakeTransport()
            log = HookLog()
            streamer = make(rand: { 1 })
            transport.replyStream(status)
            await streamer.start()
            await hooks(1)
            await settle()
            XCTAssertEqual(log.lines, ["fatal megooflags: SDK key refused (\(status))"])
            XCTAssertEqual(transport.connections.count, 1)
            await streamer.resume()
            await streamer.start()
            await settle()
            XCTAssertEqual(transport.connections.count, 1)
        }
    }

    func testRevokedCallsOnFatalOnceAndNeverReconnects() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("event: revoked\ndata: {}\n\n")
        await hooks(2)
        await settle()
        XCTAssertEqual(log.lines, ["connected", "fatal megooflags: SDK key refused (revoked)"])
        XCTAssertEqual(transport.connections.count, 1)
        XCTAssertTrue(transport.last.cancelled)
    }

    func test429WaitsRetryAfterSeconds() async {
        transport.replyStream(429, ["Retry-After": "120"])
        await streamer.start()
        await connections(2)
        XCTAssertEqual(clock.sleeps, [120])
        XCTAssertEqual(log.lines.first, "failure 1 megooflags: rate limited, retry after 120s")
    }

    func testPauseClosesTheConnectionAndDoesNotReconnect() async {
        await streamer.start()
        await hooks(1)
        await streamer.pause()
        XCTAssertTrue(transport.last.cancelled)
        await settle()
        XCTAssertEqual(transport.connections.count, 1)
        XCTAssertEqual(log.lines, ["connected"])
    }

    func testPauseDuringABackoffWaitCancelsTheReconnect() async {
        clock.hold = true
        transport.replyStream(503)
        await streamer.start()
        await hooks(1)
        await waitUntil("a backoff wait") { [clock] in clock!.sleeps.count == 1 }
        await streamer.pause()
        clock.hold = false
        await settle()
        XCTAssertEqual(transport.connections.count, 1)
    }

    func testResumeOpensAtOnceWithLastEventID() async {
        await streamer.start()
        await hooks(1)
        transport.last.send("event: changed\nid: 9\ndata: {}\n\n")
        await hooks(2)
        await streamer.pause()
        await streamer.resume()
        await connections(2)
        XCTAssertEqual(transport.last.header("Last-Event-ID"), "9")
        XCTAssertEqual(clock.sleeps, [])
        await hooks(3)
        XCTAssertEqual(log.lines, ["connected", "changed 9", "connected"])
    }

    func testResumeWithoutPauseOpensNothing() async {
        await streamer.resume()
        await settle()
        XCTAssertEqual(transport.connections.count, 0)
        await streamer.start()
        await hooks(1)
        await streamer.resume()
        await settle()
        XCTAssertEqual(transport.connections.count, 1)
    }

    func testStopIsFinal() async {
        await streamer.start()
        await hooks(1)
        await streamer.stop()
        XCTAssertTrue(transport.last.cancelled)
        await streamer.start()
        await streamer.resume()
        await settle()
        XCTAssertEqual(transport.connections.count, 1)
        XCTAssertEqual(log.lines, ["connected"])
    }

    func testStopBeforeTheOpenAnswersRunsNoHook() async {
        // The transport neither answers nor stops on cancel: the streamer
        // itself drops what arrives after stop.
        transport.autoRespond = false
        transport.honorsCancel = false
        await streamer.start()
        await connections(1)
        await streamer.stop()
        transport.last.respond(200)
        transport.last.send("event: changed\nid: 1\ndata: {}\n\n")
        await settle()
        XCTAssertEqual(log.lines, [])
        XCTAssertEqual(transport.connections.count, 1)
    }
}
