import Foundation
import XCTest

@testable import MegooFlags

private struct Seen: Equatable {
    var lines = 0
    var retries: [Int] = []
    var events: [SSEEvent] = []
}

private struct Stop: Error {}

private final class Recorder {
    var seen = Seen()
    lazy var parser = SSEParser(
        onLine: { [unowned self] in self.seen.lines += 1 },
        onRetry: { [unowned self] in self.seen.retries.append($0) },
        onEvent: { [unowned self] in self.seen.events.append($0) })
}

private func read(_ parts: [Data]) throws -> Seen {
    let r = Recorder()
    for p in parts {
        try r.parser.feed(p)
    }
    r.parser.finish()
    return r.seen
}

private func read(_ parts: String...) throws -> Seen {
    try read(parts.map { Data($0.utf8) })
}

private func event(_ name: String, _ data: String, _ id: String = "") -> SSEEvent {
    SSEEvent(name: name, data: data, id: id)
}

final class SSEParserTests: XCTestCase {
    func testCases() throws {
        let cases: [(String, String, Int, [SSEEvent])] = [
            (
                "named event with id", "event: put\nid: 42\ndata: {\"flags\":{}}\n\n", 4,
                [event("put", #"{"flags":{}}"#, "42")]
            ),
            ("default name", "data: x\n\n", 2, [event("message", "x")]),
            ("multi-line data", "data: a\ndata: b\ndata:c\n\n", 4, [event("message", "a\nb\nc")]),
            ("CRLF endings", "event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4, [event("put", "x", "7")]),
            ("unknown event name passed through", "event: mystery\ndata: 1\n\n", 3, [event("mystery", "1")]),
            ("unterminated last event discarded", "data: a\n\nevent: put\ndata: b\n", 4, [event("message", "a")]),
            ("unterminated last line discarded", "data: a\n\ndata: b", 2, [event("message", "a")]),
            (
                "state resets after dispatch", "event: put\nid: 1\ndata: a\n\ndata: b\n\n", 6,
                [event("put", "a", "1"), event("message", "b")]
            ),
            ("empty line without data dispatches nothing", "event: put\n\ndata: a\n\n", 4, [event("message", "a")]),
            ("id with NUL ignored", "id: a\0b\ndata: x\n\n", 3, [event("message", "x")]),
            ("field without colon has an empty value", "data\ndata\n\n", 3, [event("message", "\n")]),
            (
                "only one leading space removed, unknown field ignored", "foo: bar\ndata:  two\n\n", 3,
                [event("message", " two")]
            ),
            ("colon inside the value kept", "data: a: b\n\n", 2, [event("message", "a: b")]),
        ]
        for (name, input, lines, events) in cases {
            let seen = try read(input)
            XCTAssertEqual(seen.events, events, name)
            XCTAssertEqual(seen.lines, lines, name)
            XCTAssertEqual(seen.retries, [], name)
        }
    }

    func testCommentsCallOnLineOnly() throws {
        XCTAssertEqual(try read(": ping\n:\n\n"), Seen(lines: 3, retries: [], events: []))
    }

    func testAValidRetryCallsOnRetryInMilliseconds() throws {
        XCTAssertEqual(try read("retry: 3000\n\n"), Seen(lines: 2, retries: [3000], events: []))
    }

    func testAnInvalidRetryIsIgnored() throws {
        for input in ["retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: +5\n", "retry: ٣\n"] {
            XCTAssertEqual(try read(input).retries, [], input)
        }
    }

    func testAnOverlongRetryIsIgnored() throws {
        XCTAssertEqual(try read("retry: 99999999999999999999999\n").retries, [])
    }

    func testReadsA1MiBDataLine() throws {
        let big = String(repeating: "x", count: 1 << 20)
        var parts = [Data("event: put\ndata: ".utf8)]
        let bytes = Data(big.utf8)
        for i in stride(from: 0, to: bytes.count, by: 16_384) {
            parts.append(bytes.subdata(in: i..<min(i + 16_384, bytes.count)))
        }
        parts.append(Data("\n\n".utf8))
        let seen = try read(parts)
        XCTAssertEqual(seen.events.count, 1)
        XCTAssertEqual(seen.events.first?.data, big)
    }

    func testDecodesAMultiByteCharacterSplitAcrossTwoChunks() throws {
        let bytes = Data("data: é€😀\n\n".utf8)
        let at = bytes.firstIndex(of: 0xe2)! + 1
        let seen = try read([bytes.subdata(in: 0..<at), bytes.subdata(in: at..<bytes.count)])
        XCTAssertEqual(seen.events, [event("message", "é€😀")])
    }

    func testSplitsLinesThatArriveInOneChunkOrByteByByte() throws {
        let text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n"
        let one = try read(text)
        let split = try read(Data(text.utf8).map { Data([$0]) })
        XCTAssertEqual(split, one)
        XCTAssertEqual(one.events.map(\.data), ["a", "b"])
    }

    func testAcceptsDataSlicesWithANonZeroStartIndex() throws {
        let whole = Data("xxdata: a\n\n".utf8)
        let seen = try read([whole[2...]])
        XCTAssertEqual(seen.events, [event("message", "a")])
    }

    func testAnErrorThrownByOnEventStopsReading() {
        var names: [String] = []
        let parser = SSEParser(
            onLine: {}, onRetry: { _ in },
            onEvent: {
                names.append($0.name)
                throw Stop()
            })
        XCTAssertThrowsError(try parser.feed(Data("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n".utf8))) {
            XCTAssert($0 is Stop)
        }
        XCTAssertEqual(names, ["revoked"])
    }

    func testFinishDiscardsAPartialLineAndEvent() throws {
        let r = Recorder()
        try r.parser.feed(Data("data: a\ndata: b".utf8))
        r.parser.finish()
        try r.parser.feed(Data("data: c\n\n".utf8))
        XCTAssertEqual(r.seen.events, [event("message", "c")])
    }
}
