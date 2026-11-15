// The language-neutral context cases every client SDK must pass
// (spec/client/README.md).
import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

/// The repository's spec/client directory, found from this file's path.
private let clientDir = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()  // MegooFlagsTests
    .deletingLastPathComponent()  // Tests
    .deletingLastPathComponent()  // ios
    .deletingLastPathComponent()  // sdks
    .deletingLastPathComponent()  // repository root
    .appendingPathComponent("spec/client")

private struct CaseFileError: Error, CustomStringConvertible {
    let description: String
}

/// loadCaseFile reads a case file and refuses any schema_version other than 1.
private func loadCaseFile(_ url: URL) throws -> [String: JSONValue] {
    let value = try JSONDecoder().decode(JSONValue.self, from: Data(contentsOf: url))
    guard case .object(let file) = value else {
        throw CaseFileError(description: "\(url.lastPathComponent): not an object")
    }
    guard file["schema_version"] == .int(1) else {
        let version = file["schema_version"].map(canonicalJSON) ?? "missing"
        throw CaseFileError(description: "\(url.lastPathComponent): unsupported schema_version \(version)")
    }
    return file
}

/// toValue builds an OpenFeature value; {"$date": s} becomes a date.
private func toValue(_ v: JSONValue) -> Value {
    switch v {
    case .null: return .null
    case .bool(let b): return .boolean(b)
    case .int(let i): return .integer(i)
    case .double(let d): return .double(d)
    case .string(let s): return .string(s)
    case .array(let a): return .list(a.map(toValue))
    case .object(let o):
        if o.count == 1, case .string(let s)? = o["$date"], let date = ISO8601DateFormatter().date(from: s) {
            return .date(date)
        }
        return .structure(o.mapValues(toValue))
    }
}

/// toContext builds the case's EvaluationContext: targetingKey is the
/// targeting key, every other member an attribute.
private func toContext(_ o: [String: JSONValue]) -> EvaluationContext {
    var attributes = o
    var targetingKey = ""
    if case .string(let s)? = attributes.removeValue(forKey: "targetingKey") {
        targetingKey = s
    }
    return ImmutableContext(
        targetingKey: targetingKey, structure: ImmutableStructure(attributes: attributes.mapValues(toValue)))
}

/// sameJSON is JSON equality: 1 == 1.0, but true != 1 and "1" != 1.
private func sameJSON(_ a: JSONValue, _ b: JSONValue) -> Bool {
    switch (a, b) {
    case (.int(let x), .double(let y)), (.double(let y), .int(let x)): return Double(x) == y
    case (.array(let x), .array(let y)): return x.count == y.count && zip(x, y).allSatisfy { sameJSON($0, $1) }
    case (.object(let x), .object(let y)):
        return x.count == y.count && x.allSatisfy { k, v in y[k].map { sameJSON(v, $0) } ?? false }
    default: return a == b
    }
}

final class SharedContextTests: XCTestCase {
    func testSharedContextCases() throws {
        let file = try loadCaseFile(clientDir.appendingPathComponent("context.json"))
        guard case .array(let tests)? = file["tests"] else {
            return XCTFail("context.json has no tests")
        }
        XCTAssertGreaterThan(tests.count, 0)
        for test in tests {
            guard case .object(let c) = test, case .string(let name)? = c["name"],
                case .object(let ctx)? = c["context"], let expected = c["body"],
                case .string(let canonical)? = c["canonical"], case .string(let hash)? = c["context_hash"]
            else {
                XCTFail("malformed case \(canonicalJSON(test))")
                continue
            }
            let message = "context::\(name)"
            let body = toRequestContext(toContext(ctx))
            XCTAssert(
                sameJSON(.object(body), expected),
                "\(message): body \(canonicalJSON(.object(body))) != \(canonicalJSON(expected))")
            XCTAssertEqual(canonicalJSON(.object(body)), canonical, message)
            XCTAssertEqual(contextHash(body), hash, message)
        }
    }

    func testRefusesSchemaVersion2() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("case.json")
        try Data(#"{"schema_version": 2, "kind": "context", "tests": []}"#.utf8).write(to: url)
        XCTAssertThrowsError(try loadCaseFile(url)) {
            XCTAssertEqual("\($0)", "case.json: unsupported schema_version 2")
        }
    }

    func testBuildsDatesFromDateObjects() {
        let ctx = toContext(["a": .array([.object(["$date": .string("2026-11-10T09:15:02Z")])])])
        XCTAssertEqual(ctx.getValue(key: "a"), .list([.date(Date(timeIntervalSince1970: 1_794_302_102))]))
        XCTAssertEqual(ctx.getTargetingKey(), "")
    }

    func testSameJSON() {
        XCTAssert(sameJSON(.int(1), .double(1)))
        XCTAssertFalse(sameJSON(.bool(true), .int(1)))
        XCTAssertFalse(sameJSON(.string("1"), .int(1)))
        XCTAssertFalse(sameJSON(.object([:]), .array([])))
        XCTAssertFalse(sameJSON(.object(["a": .int(1)]), .object(["a": .int(1), "b": .int(2)])))
    }
}
