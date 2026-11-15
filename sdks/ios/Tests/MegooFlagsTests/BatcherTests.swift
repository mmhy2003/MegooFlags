import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!
private let user1: [String: JSONValue] = ["key": .string("user-1")]

/// Keeps the errors onFatal receives.
private final class Fatals: @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [SDKError] = []

    var errors: [SDKError] { lock.locked { kept } }

    func add(_ e: SDKError) {
        lock.locked { kept.append(e) }
    }
}

/// A flag a task sets when it ends.
private final class Finished: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var isSet: Bool { lock.locked { value } }

    func set() {
        lock.locked { value = true }
    }
}

final class BatcherTests: XCTestCase {
    private var transport: FakeTransport!
    private var clock: ManualClock!
    private var logger: LineLogger!
    private var exposures: Exposures!
    private var fatals: Fatals!
    private var batcher: Batcher?

    override func setUp() {
        transport = FakeTransport()
        clock = ManualClock()
        logger = LineLogger()
        let clock = clock!
        exposures = Exposures(sendContext: false, logger: logger, now: { clock.now })
        fatals = Fatals()
    }

    override func tearDown() async throws {
        // A close already running may wait out its 5 s deadline, whose sleep
        // may not be registered yet: advance until the close returns.
        if let b = batcher {
            let finished = Finished()
            let closed = Task {
                await b.close(timeout: 0)
                finished.set()
            }
            let clock = clock!
            await waitUntil("close") {
                await clock.advance(5)
                return finished.isSet
            }
            await closed.value
        }
        batcher = nil
    }

    @discardableResult
    private func make(flushInterval: TimeInterval = 30) -> Batcher {
        let clock = clock!
        let fatals = fatals!
        let b = Batcher(
            baseURL: base, sdkKey: key, transport: transport, exposures: exposures, flushInterval: flushInterval,
            logger: logger, onFatal: { fatals.add($0) }, now: { clock.now }, rand: { 1 },
            sleep: { try await clock.sleep($0) })
        batcher = b
        return b
    }

    /// fill records n distinct exposures.
    private func fill(_ n: Int, _ prefix: String = "f") {
        for i in 0..<n {
            exposures.record(flag: "\(prefix)\(i)", variant: "on", reason: "STATIC", body: user1)
        }
    }

    /// The exposures list of each request sent.
    private var sent: [[JSONValue]] {
        transport.requests.map { r in
            guard let data = r.httpBody, case .object(let o)? = try? JSONDecoder().decode(JSONValue.self, from: data),
                case .array(let items)? = o["exposures"]
            else { return [] }
            return items
        }
    }

    private var sizes: [Int] { sent.map(\.count) }

    private func flagKeys(_ items: [JSONValue]) -> [String] {
        items.map { item in
            guard case .object(let o) = item, case .string(let k)? = o["flag_key"] else { return "" }
            return k
        }
    }

    private var warnings: [String] { logger.lines.filter { $0.hasPrefix("warn ") } }

    func testFlushesAfterTheInterval() async {
        transport.replyData(202)
        await make().start()
        fill(3)

        await clock.sleepers(1)
        await clock.advance(29.5)
        XCTAssertEqual(transport.requests.count, 0)
        await clock.advance(0.5)

        let transport = transport!
        await waitUntil("a request") { transport.requests.count == 1 }
        XCTAssertEqual(sizes, [3])
        let exposures = exposures!
        await waitUntil("drained") { exposures.count == 0 }
    }

    func testPostsExposuresOnlyWithTheKey() async throws {
        transport.replyData(202)
        fill(1)

        await make().flush()

        let r = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(r.url?.absoluteString, "https://flags.example.com/sdk/v1/events")
        XCTAssertEqual(r.httpMethod, "POST")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer \(key)")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(r.value(forHTTPHeaderField: "User-Agent"), "megooflags-ios/\(megooFlagsVersion)")
        XCTAssertEqual(r.timeoutInterval, 10)
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(r.httpBody))
        XCTAssertEqual(
            body,
            .object([
                "exposures": .array([
                    .object([
                        "flag_key": .string("f0"), "variant": .string("on"), "reason": .string("STATIC"),
                        "timestamp": .string("2026-11-10T09:15:02.000Z"),
                    ])
                ])
            ]))
    }

    func testLogsTheCountItSentAfterA202() async {
        transport.replyData(202)
        fill(2)

        await make().flush()

        XCTAssertTrue(logger.lines.contains("debug megooflags: sent 2 exposures"), "\(logger.lines)")
    }

    func testDoesNotSendAnEmptyBatch() async {
        await make().flush()

        XCTAssertEqual(transport.requests.count, 0)
    }

    func testFlushesAtOnceWhen1000ExposuresAreBuffered() async {
        await make().start()

        fill(999)
        await settle()
        XCTAssertEqual(transport.requests.count, 0)
        fill(1, "last")

        let transport = transport!
        await waitUntil("a request") { transport.requests.count == 1 }
        XCTAssertEqual(sizes, [1000])
    }

    func testSendsAtMost1000ExposuresPerBatch() async {
        fill(2500)

        await make().flush()

        XCTAssertEqual(sizes, [1000, 1000, 500])
    }

    func testKeepsOneSendInFlight() async {
        transport.holdData = true
        let b = make()
        fill(1)

        Task { await b.flush() }
        Task { await b.flush() }
        await transport.heldCount(1)
        await settle()

        XCTAssertEqual(transport.requests.count, 1)
    }

    func testSplitsA413BatchOf1000Into500And500() async {
        transport.replyData(413)
        fill(1000)

        await make().flush()

        XCTAssertEqual(sizes, [1000, 500, 500])
        XCTAssertEqual(exposures.count, 0)
    }

    func testSplitsRecursivelyAndDropsASingleItemThatIsStill413() async {
        for status in [413, 202, 413, 413, 202] {
            transport.replyData(status)
        }
        fill(3)

        await make().flush()

        // [3] 413 → [1] 202, [2] 413 → [1] 413 dropped, [1] 202
        XCTAssertEqual(sizes, [3, 1, 2, 1, 1])
        XCTAssertEqual(
            warnings, ["warn megooflags: the server refused an exposure as too large (HTTP 413); dropping it"])
        XCTAssertEqual(exposures.count, 0)
    }

    func testDropsA422BatchWithAWarning() async {
        transport.replyData(422)
        fill(2)
        let b = make()

        await b.flush()
        await b.flush()

        XCTAssertEqual(sizes, [2])
        XCTAssertEqual(exposures.count, 0)
        XCTAssertEqual(warnings, ["warn megooflags: the server refused 2 exposures (HTTP 422); dropping them"])
    }

    func testRetriesA429AfterRetryAfterKeepingTheItemsInFront() async {
        transport.replyData(429, headers: ["Retry-After": "7"])
        fill(2)
        let b = make()

        await b.flush()
        fill(1, "later")
        XCTAssertEqual(exposures.count, 3)

        await b.flush()  // still waiting: nothing sent
        await clock.sleepers(1)
        await clock.advance(6.5)
        XCTAssertEqual(transport.requests.count, 1)
        await clock.advance(0.5)

        let transport = transport!
        await waitUntil("the retry") { transport.requests.count == 2 }
        XCTAssertEqual(flagKeys(sent[1]), ["f0", "f1", "later0"])
        XCTAssertEqual(clock.sleeps, [7])
    }

    func testRetriesA503WithABackoffFrom1s() async {
        transport.replyData(503)
        transport.replyData(503)
        fill(1)
        let transport = transport!

        await make().flush()
        await clock.sleepers(1)
        await clock.advance(0.5)
        XCTAssertEqual(transport.requests.count, 1)
        await clock.advance(0.5)
        await waitUntil("the first retry") { transport.requests.count == 2 }
        await clock.sleepers(1)
        await clock.advance(1.5)
        XCTAssertEqual(transport.requests.count, 2)
        await clock.advance(0.5)

        await waitUntil("the second retry") { transport.requests.count == 3 }
        let exposures = exposures!
        await waitUntil("drained") { exposures.count == 0 }
        XCTAssertEqual(clock.sleeps, [1, 2])
    }

    func testRetriesAfterANetworkFailure() async {
        transport.failData(URLError(.notConnectedToInternet))
        fill(1)

        await make().flush()
        XCTAssertEqual(exposures.count, 1)
        await clock.sleepers(1)
        await clock.advance(1)

        let transport = transport!
        await waitUntil("the retry") { transport.requests.count == 2 }
        let exposures = exposures!
        await waitUntil("drained") { exposures.count == 0 }
    }

    func testCallsOnFatalOn401And403AndStops() async {
        for status in [401, 403] {
            transport = FakeTransport()
            fatals = Fatals()
            transport.replyData(status)
            let b = make()
            await b.start()
            fill(1, "s\(status)-")

            await b.flush()
            fill(1, "later\(status)-")
            await b.flush()
            await clock.advance(120)

            XCTAssertEqual(fatals.errors, [.fatal(status)])
            XCTAssertEqual(transport.requests.count, 1)
            await b.close(timeout: 0)
            _ = exposures.drain(max: 10)
        }
    }

    func testCloseReturnsWithin5sWhenTheServerNeverAnswers() async {
        transport.holdData = true
        let b = make()
        await b.start()
        fill(1)
        let done = Finished()

        Task {
            await b.close(timeout: 5)
            done.set()
        }
        await transport.heldCount(1)
        await clock.sleepers(1)
        await clock.advance(4.5)
        XCTAssertFalse(done.isSet)
        await clock.advance(0.5)

        await waitUntil("closed") { done.isSet }
        XCTAssertEqual(transport.requests.count, 1)
    }

    func testDoesNotStartAnotherSendWhileClosing() async {
        transport.holdData = true
        let b = make()
        fill(1)

        Task { await b.close(timeout: 5) }
        await transport.heldCount(1)
        fill(1, "late")
        Task { await b.flush() }
        await settle()

        XCTAssertEqual(transport.requests.count, 1)
    }

    func testCloseFlushesOnceAndASecondCloseReturnsAtOnce() async {
        transport.replyData(202)
        let b = make()
        await b.start()
        fill(1)

        await b.close(timeout: 5)
        XCTAssertEqual(sizes, [1])

        fill(1, "late")
        await b.close(timeout: 5)
        await clock.advance(60)

        XCTAssertEqual(transport.requests.count, 1)
        XCTAssertEqual(clock.pending, 0)
    }

    func testStopSendsNothingMoreEvenOnClose() async {
        let b = make()
        await b.start()
        fill(2)

        await b.stop()
        await clock.advance(60)
        await b.close(timeout: 5)

        XCTAssertEqual(transport.requests.count, 0)
        XCTAssertEqual(clock.pending, 0)
    }
}
