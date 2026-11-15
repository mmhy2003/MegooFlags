import XCTest

@testable import MegooFlags

final class BackoffTests: XCTestCase {
    func testFirstWaitIsBase() {
        var b = Backoff(base: 1, max: 60, rand: { 0 })
        XCTAssertEqual(b.next(), 1)
    }

    func testDoublesFromBaseUpToMax() {
        var b = Backoff(base: 3, max: 60, rand: { 1 })
        let waits = (0..<7).map { _ in b.next() }
        XCTAssertEqual(waits, [3, 6, 12, 24, 48, 60, 60])
    }

    func testResetStartsOver() {
        var b = Backoff(base: 3, max: 60, rand: { 1 })
        _ = b.next()
        _ = b.next()
        b.reset()
        XCTAssertEqual(b.next(), 3)
    }

    func testJittersFromTheSecondWait() {
        var b = Backoff(base: 3, max: 60, rand: { 0 })
        XCTAssertEqual(b.next(), 3)
        XCTAssertEqual(b.next(), 4.8, accuracy: 1e-9)
        XCTAssertEqual(b.next(), 9.6, accuracy: 1e-9)
    }

    func testKeepsJitterWithinBounds() {
        var b = Backoff(base: 1, max: 60)
        _ = b.next()
        for n in 1..<6 {
            let w = b.next()
            let full = min(60, pow(2, Double(n)))
            XCTAssertGreaterThanOrEqual(w, 0.8 * full)
            XCTAssertLessThanOrEqual(w, full)
        }
    }

    func testFirstWaitIsNeverAboveMax() {
        var b = Backoff(base: 90, max: 60)
        XCTAssertEqual(b.next(), 60)
    }

    func testNeverGoesAboveMax() {
        var b = Backoff(base: 3, max: 60)
        for _ in 0..<200 {
            let w = b.next()
            XCTAssertGreaterThan(w, 0)
            XCTAssertLessThanOrEqual(w, 60)
        }
    }

    func testBaseCanBeReplaced() {
        var b = Backoff(base: 3, max: 60, rand: { 1 })
        b.base = 1
        XCTAssertEqual(b.next(), 1)
        XCTAssertEqual(b.next(), 2)
    }
}
