import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

private func context(_ targetingKey: String = "", _ attributes: [String: Value] = [:]) -> EvaluationContext {
    ImmutableContext(targetingKey: targetingKey, structure: ImmutableStructure(attributes: attributes))
}

private func utc(_ text: String) -> Date {
    ISO8601DateFormatter().date(from: text)!
}

final class ContextTests: XCTestCase {
    // MARK: toRequestContext

    func testWritesANonEmptyTargetingKeyAsKey() {
        XCTAssertEqual(
            toRequestContext(context("u1", ["plan": .string("pro")])), ["key": .string("u1"), "plan": .string("pro")])
    }

    func testReplacesAKeyAttributeWithTheTargetingKey() {
        XCTAssertEqual(toRequestContext(context("u1", ["key": .string("other")])), ["key": .string("u1")])
    }

    func testKeepsAKeyAttributeWithoutATargetingKey() {
        XCTAssertEqual(toRequestContext(context("", ["key": .string("u2")])), ["key": .string("u2")])
    }

    func testDropsAnEmptyTargetingKey() {
        XCTAssertEqual(toRequestContext(context("", ["plan": .string("free")])), ["plan": .string("free")])
    }

    func testNoContextIsAnEmptyBody() {
        XCTAssertEqual(toRequestContext(nil), [:])
    }

    func testMapsEveryValueKind() {
        let body = toRequestContext(
            context(
                "u1",
                [
                    "admin": .boolean(true), "age": .integer(42), "ratio": .double(1.5), "whole": .double(3.0),
                    "team": .null, "huge": .double(1e20),
                    "device": .structure(["tags": .list([.string("beta"), .structure(["level": .integer(2)])])]),
                ]))
        XCTAssertEqual(
            body,
            [
                "key": .string("u1"), "admin": .bool(true), "age": .int(42), "ratio": .double(1.5), "whole": .int(3),
                "team": .null, "huge": .double(1e20),
                "device": .object(["tags": .array([.string("beta"), .object(["level": .int(2)])])]),
            ])
    }

    func testWritesDatesAsUTCWithMilliseconds() {
        let body = toRequestContext(
            context(
                "",
                [
                    "at": .date(utc("2026-11-10T11:15:02+02:00")),
                    "nested": .structure([
                        "list": .list([.date(Date(timeIntervalSince1970: 1_794_302_102.5))])
                    ]),
                ]))
        XCTAssertEqual(
            body,
            [
                "at": .string("2026-11-10T09:15:02.000Z"),
                "nested": .object(["list": .array([.string("2026-11-10T09:15:02.500Z")])]),
            ])
    }

    // MARK: canonicalJSON

    func testSortsKeysByCodePointNotUTF16Unit() {
        let v = JSONValue.object(["😀": .int(1), "～": .int(2), "é": .int(3), "Z": .int(4), "a": .int(5)])
        XCTAssertEqual(canonicalJSON(v), #"{"Z":4,"a":5,"é":3,"～":2,"😀":1}"#)
    }

    func testSortsAPrefixBeforeItsExtension() {
        XCTAssertEqual(canonicalJSON(.object(["ab": .int(1), "a": .int(2)])), #"{"a":2,"ab":1}"#)
    }

    func testSortsNestedKeysWithoutWhitespace() {
        let v = JSONValue.object([
            "b": .array([.object(["y": .int(1), "x": .int(2)]), .array([])]), "a": .object([:]),
        ])
        XCTAssertEqual(canonicalJSON(v), #"{"a":{},"b":[{"x":2,"y":1},[]]}"#)
    }

    func testEscapesOnlyQuoteBackslashAndControlCharacters() {
        let s = "q \" b \\ n \n t \t \u{8}\u{c}\r \u{1} \u{1f} / \u{7f} café 😀"
        XCTAssertEqual(
            canonicalJSON(.string(s)), #""q \" b \\ n \n t \t \b\f\r \u0001 \u001f / "# + "\u{7f}" + #" café 😀""#)
    }

    func testWritesIntegralNumbersAsIntegers() {
        let v = JSONValue.array([
            .double(3.0), .int(-7), .double(0), .double(-0.0), .double(1.5), .double(4_503_599_627_370_496),
            .int(Int64.max), .double(0.1),
        ])
        XCTAssertEqual(canonicalJSON(v), "[3,-7,0,0,1.5,4503599627370496,9223372036854775807,0.1]")
    }

    func testWritesLargeAndNonFiniteDoubles() {
        XCTAssertEqual(canonicalJSON(.double(1e21)), "1e+21")
        XCTAssertEqual(canonicalJSON(.double(.infinity)), "null")
        XCTAssertEqual(canonicalJSON(.double(.nan)), "null")
    }

    func testWritesBooleansAndNull() {
        XCTAssertEqual(canonicalJSON(.array([.bool(true), .bool(false), .null])), "[true,false,null]")
    }

    // MARK: contextHash and targetingKey(of:)

    func testContextHashIsTheSHA256OfTheCanonicalJSON() {
        XCTAssertEqual(
            contextHash(["plan": .string("pro"), "key": .string("user-1")]),
            "e38a15b496693e013a31fba1d1c8804c3c06f1d0686e0152549c8c05609babbe")
    }

    func testTargetingKeyIsKeyWhenItIsAString() {
        XCTAssertEqual(targetingKey(of: ["key": .string("u1")]), "u1")
        XCTAssertNil(targetingKey(of: [:]))
        XCTAssertNil(targetingKey(of: ["key": .int(1)]))
        XCTAssertNil(targetingKey(of: ["key": .null]))
    }

    // MARK: JSONValue decoding

    func testDecodesEachKindInOrder() throws {
        let text = #"[null, true, false, 3, -7, 1.5, 1e20, "s", [1], {"a": {"b": null}}]"#
        let v = try JSONDecoder().decode(JSONValue.self, from: Data(text.utf8))
        XCTAssertEqual(
            v,
            .array([
                .null, .bool(true), .bool(false), .int(3), .int(-7), .double(1.5), .double(1e20), .string("s"),
                .array([.int(1)]), .object(["a": .object(["b": .null])]),
            ]))
    }

    func testEncodesAndDecodesTheSameValue() throws {
        let v = JSONValue.object([
            "a": .array([.null, .bool(true), .int(-3), .double(1.5), .string("x")]), "b": .object([:]),
        ])
        XCTAssertEqual(try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(v)), v)
    }
}
