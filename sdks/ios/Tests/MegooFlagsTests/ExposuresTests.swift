import Foundation
import XCTest

@testable import MegooFlags

private let user1: [String: JSONValue] = ["key": .string("user-1"), "plan": .string("pro")]

/// Counts onFull calls.
private final class Calls: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0

    var count: Int { lock.locked { n } }

    func add() {
        lock.locked { n += 1 }
    }
}

final class ExposuresTests: XCTestCase {
    private var clock: ManualClock!
    private var logger: LineLogger!

    override func setUp() {
        clock = ManualClock()
        logger = LineLogger()
    }

    private func make(sendContext: Bool = false, maxKeys: Int = 10_000, maxBuffer: Int = 10_000) -> Exposures {
        let clock = clock!
        return Exposures(
            sendContext: sendContext, logger: logger, now: { clock.now }, maxKeys: maxKeys, maxBuffer: maxBuffer)
    }

    private func flags(_ items: [Exposure]) -> [String] { items.map(\.flagKey) }

    private var warnings: [String] { logger.lines.filter { $0.hasPrefix("warn ") } }

    func testRecordsTheSameUserFlagAndVariantOnceWithinTheHour() async {
        let ex = make()

        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)
        await clock.advance(30 * 60)
        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)

        XCTAssertEqual(ex.count, 1)
    }

    func testRecordsItAgainInTheNextUTCHour() async {
        let ex = make()

        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)
        await clock.advance(45 * 60)
        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)

        XCTAssertEqual(ex.count, 2)
    }

    func testRecordsAnotherVariantFlagOrUserAgain() {
        let ex = make()

        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)
        ex.record(flag: "f", variant: "off", reason: "TARGETING_MATCH", body: user1)
        ex.record(flag: "g", variant: "on", reason: "TARGETING_MATCH", body: user1)
        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: ["key": .string("user-2")])

        XCTAssertEqual(ex.count, 4)
    }

    func testTreatsContextsWithoutATargetingKeyAsOneUser() {
        let ex = make()

        ex.record(flag: "f", variant: "on", reason: "STATIC", body: [:])
        ex.record(flag: "f", variant: "on", reason: "STATIC", body: ["plan": .string("pro")])

        XCTAssertEqual(ex.count, 1)
    }

    func testEvictsTheLeastRecentlySeenKey() {
        let ex = make(maxKeys: 2)

        ex.record(flag: "a", variant: "on", reason: "STATIC", body: user1)
        ex.record(flag: "b", variant: "on", reason: "STATIC", body: user1)
        ex.record(flag: "a", variant: "on", reason: "STATIC", body: user1)  // a is now the most recent
        ex.record(flag: "c", variant: "on", reason: "STATIC", body: user1)  // evicts b
        _ = ex.drain(max: 10)
        ex.record(flag: "a", variant: "on", reason: "STATIC", body: user1)
        ex.record(flag: "b", variant: "on", reason: "STATIC", body: user1)

        XCTAssertEqual(flags(ex.drain(max: 10)), ["b"])
    }

    func testKeepsEvictingCorrectlyOverManyTouches() {
        let ex = make(maxKeys: 3)

        for _ in 0..<50 {
            for f in ["a", "b", "c"] {
                ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
            }
        }
        ex.record(flag: "d", variant: "on", reason: "STATIC", body: user1)  // evicts a
        _ = ex.drain(max: 100)
        for f in ["b", "c", "d", "a"] {
            ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
        }

        XCTAssertEqual(flags(ex.drain(max: 100)), ["a"])
    }

    func testBuildsTheExposureWithAnRFC3339UTCTimestampAndNoContext() throws {
        let ex = make()

        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)

        let items = ex.drain(max: 10)
        XCTAssertEqual(
            items,
            [Exposure(flagKey: "f", variant: "on", reason: "TARGETING_MATCH", timestamp: "2026-11-10T09:15:02.000Z")])
        XCTAssertEqual(
            canonicalJSON(items[0].json),
            #"{"flag_key":"f","reason":"TARGETING_MATCH","timestamp":"2026-11-10T09:15:02.000Z","variant":"on"}"#)
    }

    func testAddsTheContextOnlyWithSendContext() {
        let ex = make(sendContext: true)

        ex.record(flag: "f", variant: "on", reason: "TARGETING_MATCH", body: user1)

        let item = ex.drain(max: 10)[0]
        XCTAssertEqual(item.context, user1)
        guard case .object(let o) = item.json else { return XCTFail("not an object") }
        XCTAssertEqual(o["context"], .object(user1))
    }

    func testDrainsAtMostMaxItemsOldestFirst() {
        let ex = make()
        for f in ["a", "b", "c"] {
            ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
        }

        XCTAssertEqual(flags(ex.drain(max: 2)), ["a", "b"])
        XCTAssertEqual(ex.count, 1)
        XCTAssertEqual(flags(ex.drain(max: 2)), ["c"])
    }

    func testPutsItemsBackInFrontOfTheBuffer() {
        let ex = make()
        for f in ["a", "b", "c"] {
            ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
        }
        let batch = ex.drain(max: 2)

        ex.restore(batch)

        XCTAssertEqual(flags(ex.drain(max: 10)), ["a", "b", "c"])
    }

    func testDropsNewItemsOverTheBoundAndWarnsOnceUntilTheNextDrain() {
        let ex = make(maxBuffer: 2)

        for f in ["a", "b", "c", "d"] {
            ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
        }

        XCTAssertEqual(ex.count, 2)
        XCTAssertEqual(warnings, ["warn megooflags: the exposure buffer is full (2); dropping new exposures"])
        XCTAssertEqual(flags(ex.drain(max: 10)), ["a", "b"])

        for f in ["e", "f", "g"] {
            ex.record(flag: f, variant: "on", reason: "STATIC", body: user1)
        }
        XCTAssertEqual(warnings.count, 2)
    }

    func testRecordsADroppedItemAgainOnceThereIsRoom() {
        let ex = make(maxBuffer: 1)

        ex.record(flag: "a", variant: "on", reason: "STATIC", body: user1)
        ex.record(flag: "b", variant: "on", reason: "STATIC", body: user1)
        _ = ex.drain(max: 10)
        ex.record(flag: "b", variant: "on", reason: "STATIC", body: user1)

        XCTAssertEqual(flags(ex.drain(max: 10)), ["b"])
    }

    func testCallsOnFullOnceWhen1000ItemsAreBuffered() {
        let ex = make()
        let calls = Calls()
        ex.onFull = { calls.add() }

        for i in 0..<999 {
            ex.record(flag: "f\(i)", variant: "on", reason: "STATIC", body: user1)
        }
        XCTAssertEqual(calls.count, 0)
        ex.record(flag: "f999", variant: "on", reason: "STATIC", body: user1)
        XCTAssertEqual(calls.count, 1)
        ex.record(flag: "more", variant: "on", reason: "STATIC", body: user1)
        XCTAssertEqual(calls.count, 1)

        _ = ex.drain(max: 1000)
        for i in 0..<999 {
            ex.record(flag: "g\(i)", variant: "on", reason: "STATIC", body: user1)
        }
        XCTAssertEqual(calls.count, 2)
    }
}
