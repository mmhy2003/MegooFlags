import Foundation
import XCTest

@testable import MegooFlags

private let hiddenName = Notification.Name("test.megooflags.hidden")
private let visibleName = Notification.Name("test.megooflags.visible")

/// Keeps the controller's calls in order: "flush", "pause", "resume".
private final class Calls: @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []

    var lines: [String] { lock.locked { kept } }

    func add(_ name: String) {
        lock.locked { kept.append(name) }
    }
}

final class BackgroundTests: XCTestCase {
    private var center: NotificationCenter!
    private var clock: ManualClock!
    private var calls: Calls!
    private var background: Background!

    override func setUp() {
        center = NotificationCenter()
        clock = ManualClock()
        calls = Calls()
        let calls = calls!
        let clock = clock!
        background = Background(
            center: center, hidden: hiddenName, visible: visibleName, sleep: { try await clock.sleep($0) },
            flush: { calls.add("flush") }, pause: { calls.add("pause") }, resume: { calls.add("resume") })
    }

    override func tearDown() {
        background.stop()
    }

    private func post(_ name: Notification.Name) async {
        center.post(name: name, object: nil)
        await settle()
    }

    func testHiddenFlushesAtOnceAndPausesAfter30Seconds() async {
        background.start()
        await post(hiddenName)
        XCTAssertEqual(calls.lines, ["flush"])
        await clock.sleepers(1)
        XCTAssertEqual(clock.sleeps, [30])
        await clock.advance(29)
        XCTAssertEqual(calls.lines, ["flush"])
        await clock.advance(1)
        XCTAssertEqual(calls.lines, ["flush", "pause"])
    }

    func testVisibleWithin30SecondsCancelsThePause() async {
        background.start()
        await post(hiddenName)
        await clock.sleepers(1)
        await post(visibleName)
        XCTAssertEqual(clock.pending, 0)
        await clock.advance(60)
        XCTAssertEqual(calls.lines, ["flush"])
    }

    func testVisibleAfterThePauseResumes() async {
        background.start()
        await post(hiddenName)
        await clock.sleepers(1)
        await clock.advance(30)
        await post(visibleName)
        XCTAssertEqual(calls.lines, ["flush", "pause", "resume"])
        await post(visibleName)
        XCTAssertEqual(calls.lines, ["flush", "pause", "resume"])
    }

    func testHiddenTwiceKeepsTheFirstTimer() async {
        background.start()
        await post(hiddenName)
        await clock.sleepers(1)
        await clock.advance(20)
        await post(hiddenName)
        XCTAssertEqual(clock.pending, 1)
        await clock.advance(10)
        XCTAssertEqual(calls.lines, ["flush", "flush", "pause"])
        await post(hiddenName)
        await clock.advance(60)
        XCTAssertEqual(calls.lines, ["flush", "flush", "pause", "flush"])
    }

    func testStartIsIdempotent() async {
        background.start()
        background.start()
        await post(hiddenName)
        XCTAssertEqual(calls.lines, ["flush"])
    }

    func testStopIgnoresNotificationsAndCancelsThePause() async {
        background.start()
        await post(hiddenName)
        await clock.sleepers(1)
        background.stop()
        await settle()
        XCTAssertEqual(clock.pending, 0)
        await clock.advance(60)
        await post(hiddenName)
        await post(visibleName)
        XCTAssertEqual(calls.lines, ["flush"])
    }

    func testNothingHappensBeforeStart() async {
        await post(hiddenName)
        XCTAssertEqual(calls.lines, [])
    }
}
