import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!
private let resultJSON =
    #"{"key":"banner","value":"blue","variant":"blue","reason":"TARGETING_MATCH","error_code":null}"#
private let result = EvaluateResult(
    key: "banner", value: .string("blue"), variant: "blue", reason: "TARGETING_MATCH", errorCode: nil,
    errorMessage: nil)

final class EvaluatorTests: XCTestCase {
    private var transport: FakeTransport!
    private var evaluator: Evaluator!

    override func setUp() {
        transport = FakeTransport()
        evaluator = Evaluator(baseURL: base, sdkKey: key, transport: transport)
    }

    /// failure returns the error evaluate throws.
    private func failure(_ body: [String: JSONValue] = [:]) async -> (any Error)? {
        do {
            let outcome = try await evaluator.evaluate(body: body, etag: nil)
            XCTFail("no error, got \(outcome)")
            return nil
        } catch {
            return error
        }
    }

    func testReturnsTheResultsAndTheETagOfA200() async throws {
        transport.replyData(200, #"{"results":[\#(resultJSON)]}"#, headers: ["ETag": "\"e1\""])
        let outcome = try await evaluator.evaluate(body: [:], etag: nil)
        XCTAssertEqual(outcome, .ok([result], etag: "\"e1\""))
    }

    func testReturnsANilETagWhenTheResponseHasNone() async throws {
        transport.replyData(200, #"{"results":[]}"#)
        let outcome = try await evaluator.evaluate(body: [:], etag: nil)
        XCTAssertEqual(outcome, .ok([], etag: nil))
    }

    func testPostsTheContextWithoutFlagsWithTheKeyAsABearerToken() async throws {
        transport.replyData(200, #"{"results":[]}"#)
        _ = try await evaluator.evaluate(body: ["key": .string("user-1"), "plan": .string("pro")], etag: nil)
        let r = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(r.url?.absoluteString, "https://flags.example.com/sdk/v1/evaluate")
        XCTAssertEqual(r.httpMethod, "POST")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Authorization"), "Bearer \(key)")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(r.value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertEqual(r.value(forHTTPHeaderField: "User-Agent"), "megooflags-ios/\(megooFlagsVersion)")
        XCTAssertNil(r.value(forHTTPHeaderField: "If-None-Match"))
        XCTAssertEqual(r.cachePolicy, .reloadIgnoringLocalCacheData)
        let body = try JSONDecoder().decode(JSONValue.self, from: XCTUnwrap(r.httpBody))
        XCTAssertEqual(body, .object(["context": .object(["key": .string("user-1"), "plan": .string("pro")])]))
    }

    func testSendsIfNoneMatchWhenAnETagIsGiven() async throws {
        transport.replyData(200, #"{"results":[]}"#)
        _ = try await evaluator.evaluate(body: [:], etag: "\"e1\"")
        XCTAssertEqual(transport.requests.first?.value(forHTTPHeaderField: "If-None-Match"), "\"e1\"")
    }

    func testReportsA304AsNotModified() async throws {
        transport.replyData(304, "", headers: ["ETag": "\"e1\""])
        let outcome = try await evaluator.evaluate(body: [:], etag: "\"e1\"")
        XCTAssertEqual(outcome, .notModified(etag: "\"e1\""))
    }

    func testThrowsRetryAfterWithTheRetryAfterSecondsOn429() async {
        transport.replyData(429, "", headers: ["Retry-After": "7"])
        let error = await failure()
        XCTAssertEqual(error as? SDKError, .retryAfter(7))
    }

    func testThrowsFatalOn401And403() async {
        for status in [401, 403] {
            transport.replyData(status)
            let error = await failure()
            XCTAssertEqual(error as? SDKError, .fatal(status))
        }
    }

    func testThrowsHTTPOn503() async {
        transport.replyData(503)
        let error = await failure()
        XCTAssertEqual(error as? SDKError, .http(503))
    }

    func testThrowsInvalidResponseOnAnUnreadableBody() async {
        for body in ["<html>", "{}", #"{"results":[{"key":1}]}"#] {
            transport.replyData(200, body)
            let error = await failure()
            guard case .invalidResponse? = error as? SDKError else {
                XCTFail("\(body): \(String(describing: error))")
                continue
            }
            let message = "\(error!)"
            XCTAssertTrue(message.hasPrefix("megooflags: "), message)
            XCTAssertFalse(message.contains("secret123"), message)
        }
    }

    func testPassesANetworkErrorThrough() async {
        transport.failData(URLError(.notConnectedToInternet))
        let error = await failure()
        XCTAssertEqual((error as? URLError)?.code, .notConnectedToInternet)
    }
}
