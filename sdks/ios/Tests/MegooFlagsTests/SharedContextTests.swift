// The language-neutral context cases every client SDK must pass
// (spec/client/README.md).
import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

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
}
