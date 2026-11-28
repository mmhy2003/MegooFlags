// The last-known results on the device, one file per SDK key. A broken file
// or directory never fails an evaluation: reads return nil and write
// failures are logged.
import CryptoKit
import Foundation

final class Cache: Sendable {
    /// The entry file; its name never contains the SDK key itself.
    let fileURL: URL
    private let logger: any MegooFlagsLogger
    private let now: @Sendable () -> Date

    private static let version: Int64 = 1

    /// directory nil is Application Support/megooflags; it is created on
    /// the first write.
    init(
        sdkKey: String, directory: URL? = nil, logger: any MegooFlagsLogger,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        let hash = SHA256.hash(data: Data(sdkKey.utf8)).map { String(format: "%02x", $0) }.joined()
        let dir = directory ?? Self.defaultDirectory
        fileURL = dir.appendingPathComponent("megooflags-\(hash.prefix(16)).json")
        self.logger = logger
        self.now = now
    }

    private static var defaultDirectory: URL {
        let support =
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("megooflags")
    }

    /// load returns the entry for contextHash with source cache, or nil when
    /// it is missing, unreadable, of another version or another context.
    /// The entry does not record the targeting key: the caller knows it from
    /// the context whose hash matched.
    func load(contextHash: String) -> StoredResults? {
        let data: Data
        do {
            data = try Data(contentsOf: fileURL)
        } catch {
            if !FileManager.default.fileExists(atPath: fileURL.path) {
                return nil
            }
            logger.log(level: .debug, message: "megooflags: cache read failed: \(reason(error))")
            return nil
        }
        do {
            return try entry(data, contextHash: contextHash)
        } catch {
            logger.log(level: .debug, message: "megooflags: cache entry ignored: \(reason(error))")
            return nil
        }
    }

    private func entry(_ data: Data, contextHash: String) throws -> StoredResults {
        let fail = { (why: String) in MegooFlagsConfigError(description: why) }
        guard case .object(let o)? = try? JSONDecoder().decode(JSONValue.self, from: data) else {
            throw fail("not an object")
        }
        guard o["v"] == .int(Self.version) else {
            throw fail("version \(o["v"].map(canonicalJSON) ?? "missing")")
        }
        guard o["context_hash"] == .string(contextHash) else {
            throw fail("another context")
        }
        let etag: String?
        switch o["etag"] {
        case .null?: etag = nil
        case .string(let s)?: etag = s
        default: throw fail("etag: not a string or null")
        }
        let results = try parseResults(data, prefix: "invalid entry")
        return StoredResults(results: results, etag: etag, source: .cache, contextHash: contextHash, targetingKey: nil)
    }

    /// save writes s as the entry, atomically, excluded from backups; a
    /// failure is logged at warn.
    func save(_ s: StoredResults) {
        do {
            let entry = Entry(
                contextHash: s.contextHash, etag: s.etag, results: s.results.values.sorted { $0.key < $1.key },
                savedAt: formatDate(now()))
            let data = try JSONEncoder().encode(entry)
            try FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: fileURL, options: .atomic)
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            var url = fileURL
            try? url.setResourceValues(values)
        } catch {
            logger.log(level: .warn, message: "megooflags: cache write failed: \(reason(error))")
        }
    }

    /// remove deletes the entry; a failure other than a missing file is
    /// logged at warn.
    func remove() {
        do {
            try FileManager.default.removeItem(at: fileURL)
        } catch {
            if FileManager.default.fileExists(atPath: fileURL.path) {
                logger.log(level: .warn, message: "megooflags: cache remove failed: \(reason(error))")
            }
        }
    }
}

/// The file format: {"v": 1, "context_hash", "etag" (string or null),
/// "results", "saved_at"}.
private struct Entry: Encodable {
    let contextHash: String
    let etag: String?
    let results: [EvaluateResult]
    let savedAt: String

    enum CodingKeys: String, CodingKey {
        case v, etag, results
        case contextHash = "context_hash"
        case savedAt = "saved_at"
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(1, forKey: .v)
        try c.encode(contextHash, forKey: .contextHash)
        try c.encode(etag, forKey: .etag)
        try c.encode(results, forKey: .results)
        try c.encode(savedAt, forKey: .savedAt)
    }
}

/// reason describes a failure for a log line: the message of an SDK error,
/// else the error's domain and code (never a path or a key).
private func reason(_ error: any Error) -> String {
    if let e = error as? MegooFlagsConfigError {
        return e.description
    }
    let ns = error as NSError
    return "\(ns.domain) \(ns.code)"
}
