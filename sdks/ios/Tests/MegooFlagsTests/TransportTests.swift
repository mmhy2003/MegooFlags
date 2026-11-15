import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_x"
private let base = URL(string: "https://flags.example.com")!
// 2026-11-14T12:00:00Z, a Saturday.
private let now = Date(timeIntervalSince1970: 1_794_657_600)

private func headers(_ h: [String: String] = [:]) -> (String) -> String? {
    { name in h.first { $0.key.lowercased() == name.lowercased() }?.value }
}

final class TransportTests: XCTestCase {
    // MARK: makeRequest

    func testSendsTheKeyAsABearerTokenWithAUserAgent() {
        let r = makeRequest(baseURL: base, path: "evaluate", sdkKey: key, method: "GET", accept: "application/json")
        XCTAssertEqual(r.url?.absoluteString, "https://flags.example.com/sdk/v1/evaluate")
        XCTAssertEqual(r.httpMethod, "GET")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer mf_client_x")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(r.value(forHTTPHeaderField: "User-Agent"), "megooflags-ios/\(megooFlagsVersion)")
        XCTAssertNil(r.value(forHTTPHeaderField: "Content-Type"))
        XCTAssertNil(r.value(forHTTPHeaderField: "If-None-Match"))
        XCTAssertNil(r.value(forHTTPHeaderField: "Last-Event-ID"))
        XCTAssertNil(r.httpBody)
    }

    func testAddsContentTypeAndTheBodyForAPost() {
        let body = Data(#"{"context":{}}"#.utf8)
        let r = makeRequest(
            baseURL: base, path: "evaluate", sdkKey: key, method: "POST", accept: "application/json", body: body)
        XCTAssertEqual(r.httpMethod, "POST")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(r.httpBody, body)
    }

    func testAddsIfNoneMatchAndLastEventIDWhenGiven() {
        let r = makeRequest(
            baseURL: base, path: "stream", sdkKey: key, method: "GET", accept: "text/event-stream", etag: #""e1""#,
            lastEventID: "42")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "text/event-stream")
        XCTAssertEqual(r.value(forHTTPHeaderField: "If-None-Match"), #""e1""#)
        XCTAssertEqual(r.value(forHTTPHeaderField: "Last-Event-ID"), "42")
    }

    func testJoinsTheBaseURLWithoutDoubleSlashes() {
        for (b, path) in [
            ("https://flags.example.com", "evaluate"), ("https://flags.example.com/", "stream"),
            ("https://flags.example.com/base//", "events"),
        ] {
            let r = makeRequest(
                baseURL: URL(string: b)!, path: path, sdkKey: key, method: "GET", accept: "application/json")
            let trimmed = b.replacingOccurrences(of: "/+$", with: "", options: .regularExpression)
            XCTAssertEqual(r.url?.absoluteString, "\(trimmed)/sdk/v1/\(path)")
            XCTAssertFalse(r.url!.absoluteString.contains(key))
        }
    }

    // MARK: statusError

    func testSuccessAndNotModifiedAreNoError() {
        for status in [200, 202, 204, 304] {
            XCTAssertNil(statusError(status: status, header: headers(), now: now), "\(status)")
        }
    }

    func testUnauthorizedAndForbiddenAreFatal() {
        for status in [401, 403] {
            let err = statusError(status: status, header: headers(), now: now)
            XCTAssertEqual(err, .fatal(status))
            XCTAssertEqual(err?.description, "megooflags: SDK key refused (\(status))")
        }
    }

    func testTooManyRequestsCarriesIntegerRetryAfter() {
        let err = statusError(status: 429, header: headers(["Retry-After": "7"]), now: now)
        XCTAssertEqual(err, .retryAfter(7))
        XCTAssertEqual(err?.description, "megooflags: rate limited, retry after 7s")
    }

    func testTooManyRequestsReadsAnHTTPDate() {
        let err = statusError(status: 429, header: headers(["retry-after": "Sat, 14 Nov 2026 12:00:30 GMT"]), now: now)
        XCTAssertEqual(err, .retryAfter(30))
    }

    func testTooManyRequestsWithoutRetryAfterWaits60Seconds() {
        XCTAssertEqual(statusError(status: 429, header: headers(), now: now), .retryAfter(60))
    }

    func testUsesTheCurrentTimeByDefault() {
        let at = Date().addingTimeInterval(3600)
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "GMT")
        f.dateFormat = "EEE, dd MMM yyyy HH:mm:ss 'GMT'"
        guard case .retryAfter(let s) = statusError(status: 429, header: headers(["Retry-After": f.string(from: at)]))
        else {
            return XCTFail("not retryAfter")
        }
        XCTAssert((3599...3600).contains(s), "\(s)")
    }

    func testOtherStatusesAreHTTPErrors() {
        for status in [404, 413, 422, 500, 503] {
            let err = statusError(status: status, header: headers(), now: now)
            XCTAssertEqual(err, .http(status))
            XCTAssertEqual(err?.description, "megooflags: HTTP \(status)")
        }
    }

    func testRevokedAndInvalidResponseMessages() {
        XCTAssertEqual(SDKError.revoked.description, "megooflags: SDK key refused (revoked)")
        XCTAssertEqual(SDKError.fatal(nil).description, "megooflags: SDK key refused")
        XCTAssertEqual(
            SDKError.invalidResponse("not JSON").description, "megooflags: invalid response: not JSON")
    }

    func testNeverPutsTheKeyInAMessage() {
        let errors: [SDKError] = [
            .fatal(401), .fatal(403), .fatal(nil), .revoked, .retryAfter(60), .http(500), .invalidResponse("x"),
        ]
        for err in errors {
            XCTAssertFalse(err.description.contains("mf_client_"), err.description)
            XCTAssertFalse(String(describing: err).contains("mf_client_"))
            XCTAssertFalse(err.localizedDescription.contains("mf_client_"))
        }
    }

    // MARK: parseRetryAfter

    func testParsesRetryAfter() {
        let cases: [(String?, TimeInterval)] = [
            ("5", 5), ("0", 0), ("-1", 0), (" 7 ", 7),
            ("Sat, 14 Nov 2026 12:00:30 GMT", 30),
            ("Sat, 14 Nov 2026 11:00:00 GMT", 0),
            ("Saturday, 14-Nov-26 12:00:30 GMT", 30),
            ("Sat Nov 14 12:00:30 2026", 30),
            ("Sat Nov  4 12:00:30 2026", 0),
        ]
        for (value, want) in cases {
            XCTAssertEqual(parseRetryAfter(value, now: now), want, "\(String(describing: value))")
        }
    }

    func testRoundsAPartialSecondOfAnHTTPDateUp() {
        XCTAssertEqual(parseRetryAfter("Sat, 14 Nov 2026 12:00:30 GMT", now: now.addingTimeInterval(-0.5)), 31)
    }

    func testInvalidRetryAfterIs60Seconds() {
        for value in [
            "abc", "", "1.5", "5s", "Sat, 14 Nov 2026 12:00:30", "Sat, 31 Feb 2026 12:00:30 GMT",
            "Sat, 14 Nov 2026 24:00:30 GMT", nil,
        ] {
            XCTAssertEqual(parseRetryAfter(value, now: now), 60, "\(String(describing: value))")
        }
    }
}
