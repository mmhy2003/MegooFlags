// The evaluated results the provider reads from, and what changed between
// two of them. The store holds one StoredResults or none and swaps it under
// a lock.
import Foundation

/// Where the stored results came from.
enum Source: String, Sendable, Codable {
    case bootstrap, cache, network
}

struct StoredResults: Equatable, Sendable {
    let results: [String: EvaluateResult]
    let etag: String?
    let source: Source
    let contextHash: String
    let targetingKey: String?

    /// Keys results by flag key; keys are unique (parseResults refuses
    /// duplicates), and a later result would win.
    init(results: [EvaluateResult], etag: String?, source: Source, contextHash: String, targetingKey: String?) {
        self.results = Dictionary(results.map { ($0.key, $0) }, uniquingKeysWith: { $1 })
        self.etag = etag
        self.source = source
        self.contextHash = contextHash
        self.targetingKey = targetingKey
    }
}

final class Store: @unchecked Sendable {
    private let lock = NSLock()
    private var current: StoredResults?

    func get() -> StoredResults? {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    func set(_ s: StoredResults?) {
        lock.lock()
        defer { lock.unlock() }
        current = s
    }
}

/// changedFlags lists, sorted, the keys whose value (JSON equality) or
/// variant differs between a and b, plus the keys present in only one.
func changedFlags(_ a: StoredResults, _ b: StoredResults) -> [String] {
    var changed = a.results.compactMap { key, ra -> String? in
        guard let rb = b.results[key], ra.variant == rb.variant, sameJSON(ra.value, rb.value) else { return key }
        return nil
    }
    changed += b.results.keys.filter { a.results[$0] == nil }
    return changed.sorted()
}

/// sameJSON is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays
/// element by element, objects key by key, {} is not [].
func sameJSON(_ a: JSONValue, _ b: JSONValue) -> Bool {
    switch (a, b) {
    case (.int(let x), .double(let y)), (.double(let y), .int(let x)): return Double(x) == y
    case (.array(let x), .array(let y)): return x.count == y.count && zip(x, y).allSatisfy { sameJSON($0, $1) }
    case (.object(let x), .object(let y)):
        return x.count == y.count && x.allSatisfy { k, v in y[k].map { sameJSON(v, $0) } ?? false }
    default: return a == b
    }
}
