import OpenFeature
import XCTest

@testable import MegooFlags

private func result(
    _ key: String, _ value: JSONValue, variant: String? = "v", reason: String = "TARGETING_MATCH",
    errorCode: String? = nil, errorMessage: String? = nil
) -> EvaluateResult {
    EvaluateResult(
        key: key, value: value, variant: variant, reason: reason, errorCode: errorCode, errorMessage: errorMessage)
}

private func stored(_ results: [EvaluateResult], source: Source = .network) -> StoredResults {
    StoredResults(results: results, etag: nil, source: source, contextHash: "h", targetingKey: nil)
}

final class ResolveTests: XCTestCase {
    // MARK: Store

    func testStoreHoldsOneResultSetOrNone() {
        let store = Store()
        XCTAssertNil(store.get())
        let s = stored([result("a", .bool(true))])
        store.set(s)
        XCTAssertEqual(store.get(), s)
        store.set(nil)
        XCTAssertNil(store.get())
    }

    func testStoredResultsKeysResultsByFlag() {
        let s = StoredResults(
            results: [result("a", .int(1)), result("b", .int(2))], etag: "e1", source: .cache, contextHash: "h",
            targetingKey: "user-1")
        XCTAssertEqual(s.results["b"]?.value, .int(2))
        XCTAssertEqual(s.results.count, 2)
        XCTAssertEqual(s.etag, "e1")
        XCTAssertEqual(s.source, .cache)
        XCTAssertEqual(s.contextHash, "h")
        XCTAssertEqual(s.targetingKey, "user-1")
    }

    // MARK: resolve, in B5 order

    func testNoStoredResultsIsProviderNotReady() {
        let e = resolve(nil, flag: "a", as: .boolean, default: true)
        XCTAssertEqual(e.value, true)
        XCTAssertNil(e.variant)
        XCTAssertEqual(e.reason, "ERROR")
        XCTAssertEqual(e.errorCode, .providerNotReady)
    }

    func testMissingFlagIsFlagNotFound() {
        let e = resolve(stored([]), flag: "a", as: .string, default: "d")
        XCTAssertEqual(e.value, "d")
        XCTAssertEqual(e.reason, "ERROR")
        XCTAssertEqual(e.errorCode, .flagNotFound)
    }

    func testStoredErrorCodesAreMappedWithTheirMessage() {
        let codes: [(String, ErrorCode)] = [
            ("FLAG_NOT_FOUND", .flagNotFound), ("PARSE_ERROR", .parseError), ("TYPE_MISMATCH", .typeMismatch),
            ("TARGETING_KEY_MISSING", .targetingKeyMissing), ("INVALID_CONTEXT", .invalidContext),
            ("GENERAL", .general), ("SOMETHING_NEW", .general),
        ]
        for (code, mapped) in codes {
            let s = stored([result("a", .null, variant: nil, reason: "ERROR", errorCode: code, errorMessage: "why")])
            let e = resolve(s, flag: "a", as: .boolean, default: false)
            XCTAssertEqual(e.value, false, code)
            XCTAssertNil(e.variant, code)
            XCTAssertEqual(e.reason, "ERROR", code)
            XCTAssertEqual(e.errorCode, mapped, code)
            XCTAssertEqual(e.errorMessage, "why", code)
        }
    }

    func testNullVariantReturnsTheDefaultWithTheStoredReason() {
        let e = resolve(
            stored([result("a", .bool(true), variant: nil, reason: "DISABLED")]), flag: "a", as: .boolean,
            default: false)
        XCTAssertEqual(e.value, false)
        XCTAssertNil(e.variant)
        XCTAssertEqual(e.reason, "DISABLED")
        XCTAssertNil(e.errorCode)
    }

    func testWrongTypeIsTypeMismatch() {
        let s = stored([result("s", .string("x")), result("b", .bool(true)), result("n", .int(1))])
        XCTAssertEqual(resolve(s, flag: "s", as: .boolean, default: false).errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "n", as: .string, default: "").errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "b", as: .integer, default: Int64(0)).errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "b", as: .double, default: 0.5).errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "s", as: .object, default: Value.null).errorCode, .typeMismatch)
        let e = resolve(s, flag: "s", as: .integer, default: Int64(7))
        XCTAssertEqual(e.value, 7)
        XCTAssertNil(e.variant)
        XCTAssertEqual(e.reason, "ERROR")
    }

    func testIntegerReadsAcceptIntegralNumbersWithinInt64() {
        let s = stored([
            result("i", .int(3)), result("d", .double(3)), result("f", .double(1.5)), result("big", .double(1e20)),
            result("min", .double(-9_223_372_036_854_775_808)), result("max", .double(9_223_372_036_854_775_808)),
        ])
        XCTAssertEqual(resolve(s, flag: "i", as: .integer, default: Int64(0)).value, 3)
        XCTAssertEqual(resolve(s, flag: "d", as: .integer, default: Int64(0)).value, 3)
        XCTAssertEqual(resolve(s, flag: "min", as: .integer, default: Int64(0)).value, Int64.min)
        XCTAssertEqual(resolve(s, flag: "f", as: .integer, default: Int64(0)).errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "big", as: .integer, default: Int64(0)).errorCode, .typeMismatch)
        XCTAssertEqual(resolve(s, flag: "max", as: .integer, default: Int64(0)).errorCode, .typeMismatch)
    }

    func testNumberReadsAcceptAnyNumber() {
        let s = stored([result("i", .int(3)), result("d", .double(2.5))])
        XCTAssertEqual(resolve(s, flag: "i", as: .double, default: 0.5).value, 3)
        XCTAssertEqual(resolve(s, flag: "d", as: .double, default: 0.5).value, 2.5)
    }

    func testObjectReadsBuildStructuresAndLists() {
        let s = stored([
            result("o", .object(["a": .array([.int(1), .double(1.5), .null, .bool(true), .string("x")])])),
            result("l", .array([.object([:])])),
        ])
        XCTAssertEqual(
            resolve(s, flag: "o", as: .object, default: Value.null).value,
            .structure(["a": .list([.integer(1), .double(1.5), .null, .boolean(true), .string("x")])]))
        XCTAssertEqual(resolve(s, flag: "l", as: .object, default: Value.null).value, .list([.structure([:])]))
    }

    func testServesTheValueWithTheStoredReason() {
        let s = stored([result("a", .string("blue"), variant: "blue", reason: "SPLIT")])
        let e = resolve(s, flag: "a", as: .string, default: "red")
        XCTAssertEqual(e.value, "blue")
        XCTAssertEqual(e.variant, "blue")
        XCTAssertEqual(e.reason, "SPLIT")
        XCTAssertNil(e.errorCode)
        XCTAssertNil(e.errorMessage)
        XCTAssert(e.flagMetadata.isEmpty)
    }

    func testValuesFromTheCacheHaveReasonCached() {
        for source in [Source.cache, .bootstrap] {
            let s = stored([result("a", .bool(true), reason: "SPLIT")], source: source)
            XCTAssertEqual(
                resolve(s, flag: "a", as: .boolean, default: false).reason, source == .cache ? "CACHED" : "SPLIT")
        }
    }

    // MARK: changedFlags

    func testChangedFlagsListsChangedAddedAndRemovedKeysSorted() {
        let a = stored([
            result("same", .int(1)), result("value", .int(1)), result("variant", .int(1), variant: "x"),
            result("gone", .int(1)), result("int-double", .int(1)),
        ])
        let b = stored([
            result("same", .int(1)), result("value", .int(2)), result("variant", .int(1), variant: "y"),
            result("new", .int(1)), result("int-double", .double(1)),
        ])
        XCTAssertEqual(changedFlags(a, b), ["gone", "new", "value", "variant"])
        XCTAssertEqual(changedFlags(a, a), [])
    }

    func testSameJSON() {
        XCTAssert(sameJSON(.int(1), .double(1)))
        XCTAssertFalse(sameJSON(.bool(true), .int(1)))
        XCTAssertFalse(sameJSON(.string("1"), .int(1)))
        XCTAssertFalse(sameJSON(.object([:]), .array([])))
        XCTAssertFalse(sameJSON(.object(["a": .int(1)]), .object(["a": .int(1), "b": .int(2)])))
        XCTAssert(sameJSON(.array([.object(["a": .int(2)])]), .array([.object(["a": .double(2)])])))
    }
}
