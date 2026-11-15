// The language-neutral resolution cases every client SDK must pass
// (spec/client/README.md). integer cases use the Int64 read, number cases
// the Double read.
import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

/// name is the OpenFeature name of an error code.
private func name(of code: ErrorCode) -> String {
    switch code {
    case .providerNotReady: return "PROVIDER_NOT_READY"
    case .flagNotFound: return "FLAG_NOT_FOUND"
    case .parseError: return "PARSE_ERROR"
    case .typeMismatch: return "TYPE_MISMATCH"
    case .targetingKeyMissing: return "TARGETING_KEY_MISSING"
    case .invalidContext: return "INVALID_CONTEXT"
    case .general: return "GENERAL"
    case .providerFatal: return "PROVIDER_FATAL"
    }
}

/// toJSON turns a resolved OpenFeature value back into JSON for comparison.
private func toJSON(_ v: Value) -> JSONValue {
    switch v {
    case .boolean(let b): return .bool(b)
    case .string(let s): return .string(s)
    case .integer(let i): return .int(i)
    case .double(let d): return .double(d)
    case .date(let d): return .string("\(d)")
    case .list(let l): return .array(l.map(toJSON))
    case .structure(let s): return .object(s.mapValues(toJSON))
    case .null: return .null
    }
}

/// The resolution of a case, as JSON.
private struct Outcome {
    let value: JSONValue
    let variant: String?
    let reason: String?
    let errorCode: String?

    init<T>(_ e: ProviderEvaluation<T>, _ toJSON: (T) -> JSONValue) {
        value = toJSON(e.value)
        variant = e.variant
        reason = e.reason
        errorCode = e.errorCode.map(name(of:))
    }
}

private struct CaseError: Error, CustomStringConvertible {
    let description: String
}

/// resolveCase resolves one case's flag with its type and default.
private func resolveCase(_ c: [String: JSONValue]) throws -> Outcome {
    guard case .string(let flag)? = c["flag"], case .string(let type)? = c["type"],
        case .string(let sourceName)? = c["source"], let source = Source(rawValue: sourceName),
        let results = c["results"], let def = c["default"]
    else { throw CaseError(description: "malformed case") }
    var store: StoredResults?
    if results != .null {
        let parsed = try parseResults(
            Data(("{\"results\":" + canonicalJSON(results) + "}").utf8), prefix: "megooflags: invalid results")
        store = StoredResults(results: parsed, etag: nil, source: source, contextHash: "", targetingKey: nil)
    }
    switch (type, def) {
    case ("boolean", .bool(let d)):
        return Outcome(resolve(store, flag: flag, as: .boolean, default: d), JSONValue.bool)
    case ("string", .string(let d)):
        return Outcome(resolve(store, flag: flag, as: .string, default: d), JSONValue.string)
    case ("integer", .int(let d)):
        return Outcome(resolve(store, flag: flag, as: .integer, default: d), JSONValue.int)
    case ("number", .int(let d)):
        return Outcome(resolve(store, flag: flag, as: .double, default: Double(d)), JSONValue.double)
    case ("number", .double(let d)):
        return Outcome(resolve(store, flag: flag, as: .double, default: d), JSONValue.double)
    case ("object", _):
        return Outcome(resolve(store, flag: flag, as: .object, default: toValue(def)), toJSON)
    default:
        throw CaseError(description: "unsupported type \(type) with default \(canonicalJSON(def))")
    }
}

final class SharedResolutionTests: XCTestCase {
    func testSharedResolutionCases() throws {
        let file = try loadCaseFile(clientDir.appendingPathComponent("resolution.json"))
        guard case .array(let tests)? = file["tests"] else {
            return XCTFail("resolution.json has no tests")
        }
        XCTAssertGreaterThan(tests.count, 0)
        var types = Set<String>()
        for test in tests {
            guard case .object(let c) = test, case .string(let name)? = c["name"], case .string(let type)? = c["type"],
                case .object(let expected)? = c["expected"], let value = expected["value"]
            else {
                XCTFail("malformed case \(canonicalJSON(test))")
                continue
            }
            let message = "resolution::\(name)"
            types.insert(type)
            do {
                let got = try resolveCase(c)
                XCTAssert(
                    sameJSON(got.value, value),
                    "\(message): value \(canonicalJSON(got.value)) != \(canonicalJSON(value))")
                XCTAssertEqual(got.variant.map(JSONValue.string) ?? .null, expected["variant"], "\(message): variant")
                XCTAssertEqual(got.reason.map(JSONValue.string) ?? .null, expected["reason"], "\(message): reason")
                XCTAssertEqual(
                    got.errorCode.map(JSONValue.string) ?? .null, expected["error_code"], "\(message): error_code")
            } catch {
                XCTFail("\(message): \(error)")
            }
        }
        XCTAssertEqual(types, ["boolean", "string", "integer", "number", "object"])
    }

    func testRefusesSchemaVersion2() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("resolution.json")
        try Data(#"{"schema_version": 2, "kind": "resolution", "tests": []}"#.utf8).write(to: url)
        XCTAssertThrowsError(try loadCaseFile(url)) {
            XCTAssertEqual("\($0)", "resolution.json: unsupported schema_version 2")
        }
    }
}
