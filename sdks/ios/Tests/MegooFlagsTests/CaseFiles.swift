// Loading the shared client cases (spec/client/README.md) and building
// OpenFeature values from them.
import Foundation
import OpenFeature

@testable import MegooFlags

/// The repository's spec/client directory, found from this file's path.
let clientDir = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()  // MegooFlagsTests
    .deletingLastPathComponent()  // Tests
    .deletingLastPathComponent()  // ios
    .deletingLastPathComponent()  // sdks
    .deletingLastPathComponent()  // repository root
    .appendingPathComponent("spec/client")

struct CaseFileError: Error, CustomStringConvertible {
    let description: String
}

/// loadCaseFile reads a case file and refuses any schema_version other than 1.
func loadCaseFile(_ url: URL) throws -> [String: JSONValue] {
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
func toValue(_ v: JSONValue) -> Value {
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
