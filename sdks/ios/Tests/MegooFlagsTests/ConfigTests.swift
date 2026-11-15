import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!

private struct RecordingLogger: MegooFlagsLogger {
    func log(level: MegooFlagsLogLevel, message: String) {}
}

/// assertConfigError checks that building config throws exactly message and
/// that the message never contains the key's secret part.
private func assertConfigError(
    _ config: MegooFlagsConfig, _ message: String, file: StaticString = #filePath, line: UInt = #line
) {
    XCTAssertThrowsError(try ResolvedConfig(config), file: file, line: line) { error in
        guard let error = error as? MegooFlagsConfigError else {
            return XCTFail("unexpected error \(error)", file: file, line: line)
        }
        XCTAssertEqual(error.description, message, file: file, line: line)
        XCTAssertFalse(error.description.contains("secret123"), file: file, line: line)
    }
}

final class ConfigTests: XCTestCase {
    // MARK: sdkKey

    func testRefusesAServerKey() {
        assertConfigError(
            MegooFlagsConfig(sdkKey: "mf_server_secret123", baseURL: base),
            "megooflags: a server key must never ship in an app; use a client key (mf_client_…)")
    }

    func testRefusesAnyOtherKey() {
        for bad in ["", "secret123", "MF_CLIENT_secret123", "mf_secret123"] {
            assertConfigError(
                MegooFlagsConfig(sdkKey: bad, baseURL: base), "megooflags: sdkKey must be a client key (mf_client_…)")
        }
    }

    // MARK: baseURL

    func testRequiresABaseURLUnlessOffline() throws {
        assertConfigError(MegooFlagsConfig(sdkKey: key), "megooflags: baseURL is required unless offline")
        XCTAssertNil(try ResolvedConfig(MegooFlagsConfig(sdkKey: key, offline: true)).baseURL)
    }

    func testRefusesANonHTTPBaseURL() {
        for bad in ["ftp://flags.example.com", "file:///tmp/flags", "flags.example.com", "https://"] {
            assertConfigError(
                MegooFlagsConfig(sdkKey: key, baseURL: URL(string: bad)!),
                "megooflags: baseURL must be an http or https URL")
        }
    }

    func testRemovesTrailingSlashesFromTheBaseURL() throws {
        let c = try ResolvedConfig(
            MegooFlagsConfig(sdkKey: key, baseURL: URL(string: "http://localhost:8000/flags//")!))
        XCTAssertEqual(c.baseURL?.absoluteString, "http://localhost:8000/flags")
    }

    // MARK: defaults and clamps

    func testFillsTheDefaults() throws {
        let c = try ResolvedConfig(MegooFlagsConfig(sdkKey: key, baseURL: base))
        XCTAssertEqual(c.sdkKey, key)
        XCTAssertEqual(c.baseURL, base)
        XCTAssertNil(c.bootstrap)
        XCTAssertFalse(c.disableCache)
        XCTAssertFalse(c.offline)
        XCTAssertEqual(c.initTimeout, 5)
        XCTAssertFalse(c.disableStream)
        XCTAssertEqual(c.pollInterval, 30)
        XCTAssertEqual(c.flushInterval, 30)
        XCTAssertFalse(c.sendContext)
        XCTAssert(c.logger is OSLogLogger)
    }

    func testKeepsTheGivenOptions() throws {
        let session = URLSessionConfiguration.ephemeral
        let c = try ResolvedConfig(
            MegooFlagsConfig(
                sdkKey: key, baseURL: base, disableCache: true, offline: true, initTimeout: 2, disableStream: true,
                pollInterval: 45, flushInterval: 20, sendContext: true, logger: RecordingLogger(),
                sessionConfiguration: session))
        XCTAssert(c.disableCache)
        XCTAssert(c.offline)
        XCTAssertEqual(c.initTimeout, 2)
        XCTAssert(c.disableStream)
        XCTAssertEqual(c.pollInterval, 45)
        XCTAssertEqual(c.flushInterval, 20)
        XCTAssert(c.sendContext)
        XCTAssert(c.logger is RecordingLogger)
        XCTAssert(c.sessionConfiguration === session)
    }

    func testRequiresAPositiveInitTimeout() {
        for bad in [0, -1, Double.nan] {
            assertConfigError(
                MegooFlagsConfig(sdkKey: key, baseURL: base, initTimeout: bad),
                "megooflags: initTimeout must be greater than 0")
        }
    }

    func testRaisesThePollIntervalTo15Seconds() throws {
        XCTAssertEqual(
            try ResolvedConfig(MegooFlagsConfig(sdkKey: key, baseURL: base, pollInterval: 1)).pollInterval, 15)
        XCTAssertEqual(
            try ResolvedConfig(MegooFlagsConfig(sdkKey: key, baseURL: base, pollInterval: 15)).pollInterval, 15)
    }

    func testClampsTheFlushIntervalTo10To60Seconds() throws {
        let flush = { (s: TimeInterval) in
            try ResolvedConfig(MegooFlagsConfig(sdkKey: key, baseURL: base, flushInterval: s)).flushInterval
        }
        XCTAssertEqual(try flush(1), 10)
        XCTAssertEqual(try flush(10), 10)
        XCTAssertEqual(try flush(60), 60)
        XCTAssertEqual(try flush(600), 60)
    }

    // MARK: bootstrap

    func testParsesTheBootstrap() throws {
        let json = """
            {"results": [
              {"key": "a", "value": {"x": [1, 1.5]}, "variant": "on", "reason": "TARGETING_MATCH",
               "error_code": null, "error_message": null, "extra": 1},
              {"key": "b", "value": null, "variant": null, "reason": "ERROR", "error_code": "GENERAL"}
            ]}
            """
        let c = try ResolvedConfig(MegooFlagsConfig(sdkKey: key, baseURL: base, bootstrap: json))
        XCTAssertEqual(
            c.bootstrap,
            [
                EvaluateResult(
                    key: "a", value: .object(["x": .array([.int(1), .double(1.5)])]), variant: "on",
                    reason: "TARGETING_MATCH", errorCode: nil, errorMessage: nil),
                EvaluateResult(
                    key: "b", value: .null, variant: nil, reason: "ERROR", errorCode: "GENERAL", errorMessage: nil),
            ])
    }

    func testRefusesAnInvalidBootstrap() {
        let item = #""key": "a", "value": 1, "variant": "v", "reason": "STATIC", "error_code": null"#
        let cases: [(String, String)] = [
            ("not json", "not valid JSON"),
            ("[]", "not an object"),
            (#"{"results": {}}"#, "results: not an array"),
            (#"{}"#, "results: missing"),
            (#"{"results": [1]}"#, "results[0]: not an object"),
            (
                #"{"results": [{"value": 1, "variant": "v", "reason": "STATIC", "error_code": null}]}"#,
                "results[0].key: missing"
            ),
            (
                #"{"results": [{"key": 1, "value": 1, "variant": "v", "reason": "STATIC", "error_code": null}]}"#,
                "results[0].key: not a string"
            ),
            (
                #"{"results": [{"key": "a", "variant": "v", "reason": "STATIC", "error_code": null}]}"#,
                "results[0].value: missing"
            ),
            (
                #"{"results": [{"key": "a", "value": 1, "reason": "STATIC", "error_code": null}]}"#,
                "results[0].variant: missing"
            ),
            (
                #"{"results": [{"key": "a", "value": 1, "variant": 2, "reason": "STATIC", "error_code": null}]}"#,
                "results[0].variant: not a string or null"
            ),
            (
                #"{"results": [{"key": "a", "value": 1, "variant": "v", "reason": null, "error_code": null}]}"#,
                "results[0].reason: not a string"
            ),
            (
                #"{"results": [{"key": "a", "value": 1, "variant": "v", "reason": "STATIC"}]}"#,
                "results[0].error_code: missing"
            ),
            (
                #"{"results": [{"key": "a", "value": 1, "variant": "v", "reason": "STATIC", "error_code": 5}]}"#,
                "results[0].error_code: not a string or null"
            ),
            ("{\"results\": [{\(item), \"error_message\": false}]}", "results[0].error_message: not a string or null"),
            ("{\"results\": [{\(item)}, {\(item)}]}", "results[1].key: duplicate key"),
        ]
        for (json, why) in cases {
            assertConfigError(
                MegooFlagsConfig(sdkKey: key, baseURL: base, bootstrap: json), "megooflags: invalid bootstrap: \(why)")
        }
    }

    // MARK: EvaluateResult storage

    func testEvaluateResultRoundTripsWithNullsKept() throws {
        let r = EvaluateResult(
            key: "a", value: .null, variant: nil, reason: "DISABLED", errorCode: nil, errorMessage: nil)
        let data = try JSONEncoder().encode(r)
        let object = try JSONDecoder().decode(JSONValue.self, from: data)
        XCTAssertEqual(
            object,
            .object([
                "key": .string("a"), "value": .null, "variant": .null, "reason": .string("DISABLED"),
                "error_code": .null, "error_message": .null,
            ]))
        XCTAssertEqual(try JSONDecoder().decode(EvaluateResult.self, from: data), r)
    }
}
