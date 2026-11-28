import Foundation
import XCTest

@testable import MegooFlags

private let key = "mf_client_secret123"
// 2026-11-14T12:00:00Z.
private let savedAt = Date(timeIntervalSince1970: 1_794_657_600)

private let results = [
    EvaluateResult(
        key: "banner", value: .string("blue"), variant: "blue", reason: "TARGETING_MATCH", errorCode: nil,
        errorMessage: nil),
    EvaluateResult(
        key: "gone", value: .bool(false), variant: nil, reason: "ERROR", errorCode: "FLAG_NOT_FOUND",
        errorMessage: "no such flag"),
]

private func stored(etag: String? = "\"e1\"", hash: String = "h1") -> StoredResults {
    StoredResults(results: results, etag: etag, source: .network, contextHash: hash, targetingKey: "user-1")
}

final class CacheTests: XCTestCase {
    private var dir: URL!
    private var logger: LineLogger!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("megooflags-tests-\(UUID().uuidString)")
        logger = LineLogger()
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    private func cache(_ directory: URL? = nil) -> Cache {
        Cache(sdkKey: key, directory: directory ?? dir, logger: logger, now: { savedAt })
    }

    private func writeEntry(_ text: String) throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data(text.utf8).write(to: cache().fileURL)
    }

    func testRoundTripsResultsETagAndContextHashWithSourceCache() {
        let c = cache()
        c.save(stored())
        let loaded = c.load(contextHash: "h1")
        XCTAssertEqual(
            loaded,
            StoredResults(results: results, etag: "\"e1\"", source: .cache, contextHash: "h1", targetingKey: nil))
        XCTAssertEqual(logger.lines, [])
    }

    func testKeepsANilETag() {
        let c = cache()
        c.save(stored(etag: nil))
        XCTAssertNil(c.load(contextHash: "h1")?.etag)
        XCTAssertNotNil(c.load(contextHash: "h1"))
    }

    func testWritesExactlyTheEntryFields() throws {
        let c = cache()
        c.save(stored(etag: nil))
        let entry = try JSONDecoder().decode(JSONValue.self, from: Data(contentsOf: c.fileURL))
        guard case .object(let o) = entry else { return XCTFail("not an object: \(entry)") }
        XCTAssertEqual(Set(o.keys), ["v", "context_hash", "etag", "results", "saved_at"])
        XCTAssertEqual(o["v"], .int(1))
        XCTAssertEqual(o["context_hash"], .string("h1"))
        XCTAssertEqual(o["etag"], .null)
        XCTAssertEqual(o["saved_at"], .string("2026-11-14T12:00:00.000Z"))
        guard case .array(let items)? = o["results"], case .object(let first)? = items.first else {
            return XCTFail("results: \(String(describing: o["results"]))")
        }
        XCTAssertEqual(items.count, 2)
        XCTAssertEqual(Set(first.keys), ["key", "value", "variant", "reason", "error_code", "error_message"])
    }

    func testCreatesTheDirectoryOnFirstWrite() {
        let nested = dir.appendingPathComponent("a/b")
        let c = cache(nested)
        XCTAssertFalse(FileManager.default.fileExists(atPath: nested.path))
        XCTAssertNil(c.load(contextHash: "h1"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: nested.path))
        c.save(stored())
        XCTAssertNotNil(c.load(contextHash: "h1"))
    }

    #if os(iOS) || os(macOS)
        func testTheFileIsExcludedFromBackup() throws {
            let c = cache()
            c.save(stored())
            let values = try c.fileURL.resourceValues(forKeys: [.isExcludedFromBackupKey])
            XCTAssertEqual(values.isExcludedFromBackup, true)
        }
    #endif

    func testReturnsNilForAnotherContextHash() {
        let c = cache()
        c.save(stored())
        XCTAssertNil(c.load(contextHash: "h2"))
    }

    func testReturnsNilWhenNothingIsStored() {
        XCTAssertNil(cache().load(contextHash: "h1"))
        XCTAssertEqual(logger.lines, [])
    }

    func testReturnsNilForAForeignVersion() throws {
        try writeEntry(#"{"v":2,"context_hash":"h1","etag":null,"results":[],"saved_at":"x"}"#)
        XCTAssertNil(cache().load(contextHash: "h1"))
    }

    func testReturnsNilForACorruptFile() throws {
        for text in [
            "{not json", "[]", #"{"v":1,"context_hash":"h1","etag":3,"results":[]}"#,
            #"{"v":1,"context_hash":"h1","results":[]}"#, #"{"v":1,"context_hash":"h1","etag":null}"#,
            #"{"v":1,"context_hash":"h1","etag":null,"results":[{"key":1}]}"#,
        ] {
            try writeEntry(text)
            XCTAssertNil(cache().load(contextHash: "h1"), text)
        }
        XCTAssertTrue(
            logger.lines.allSatisfy { $0.hasPrefix("debug megooflags: cache entry ignored: ") }, "\(logger.lines)")
    }

    func testAnUnwritableDirectoryLogsAWarningAndDoesNotThrow() throws {
        // A directory under a regular file can never be created, even as root.
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("file")
        try Data("x".utf8).write(to: file)
        let c = cache(file.appendingPathComponent("sub"))
        c.save(stored())
        XCTAssertNil(c.load(contextHash: "h1"))
        XCTAssertEqual(logger.lines.count, 1)
        XCTAssertTrue(logger.lines[0].hasPrefix("warn megooflags: cache write failed: "), logger.lines[0])
        XCTAssertFalse(logger.lines[0].contains("secret123"))
    }

    func testRemoveDeletesTheEntry() {
        let c = cache()
        c.save(stored())
        c.remove()
        XCTAssertNil(c.load(contextHash: "h1"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: c.fileURL.path))
        c.remove()
        XCTAssertEqual(logger.lines, [])
    }

    func testTheFileNameIsMegooflagsPlus16HexAndHasNoKey() {
        let c = cache()
        let name = c.fileURL.lastPathComponent
        XCTAssertNotNil(name.range(of: #"^megooflags-[0-9a-f]{16}\.json$"#, options: .regularExpression), name)
        XCTAssertFalse(c.fileURL.path.contains("secret123"))
        XCTAssertEqual(c.fileURL.deletingLastPathComponent().standardizedFileURL.path, dir.standardizedFileURL.path)
    }

    func testTheFileNameDiffersPerSDKKey() {
        let other = Cache(sdkKey: "mf_client_other", directory: dir, logger: logger)
        XCTAssertNotEqual(cache().fileURL, other.fileURL)
    }

    func testTheDefaultDirectoryIsApplicationSupportMegooflags() throws {
        let support = try XCTUnwrap(
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first)
        let c = Cache(sdkKey: key, logger: logger)
        XCTAssertEqual(
            c.fileURL.deletingLastPathComponent().standardizedFileURL.path,
            support.appendingPathComponent("megooflags").standardizedFileURL.path)
    }
}
