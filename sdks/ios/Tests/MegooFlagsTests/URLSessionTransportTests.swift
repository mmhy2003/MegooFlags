import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// Answers every request with status 200, header X-Test: 1 and the chunks
/// "one" and "two"; the path /body sends the single chunk "body", and /open
/// never finishes. The Content-Type matters: without one, Darwin's URLSession
/// holds back the first bytes to sniff the MIME type, so a short chunk on a
/// connection that stays open would never be delivered.
private final class StubProtocol: URLProtocol, @unchecked Sendable {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let response = HTTPURLResponse(
            url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
            headerFields: ["X-Test": "1", "Content-Type": "text/event-stream"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        if request.url?.path == "/body" {
            client?.urlProtocol(self, didLoad: Data("body".utf8))
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        client?.urlProtocol(self, didLoad: Data("one".utf8))
        if request.url?.path == "/open" {
            return
        }
        client?.urlProtocol(self, didLoad: Data("two".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

/// Collects what a stream delivers.
private final class Delivered: @unchecked Sendable {
    private let lock = NSLock()
    private var status: Int?
    private var bytes = Data()
    private var completion: [(any Error)?] = []

    var response: Int? { lock.locked { status } }
    var data: String { lock.locked { String(decoding: bytes, as: UTF8.self) } }
    var completed: [(any Error)?] { lock.locked { completion } }

    func open(_ transport: URLSessionTransport, _ url: String) -> any CancelHandle {
        transport.stream(
            for: URLRequest(url: URL(string: url)!),
            onResponse: { r in self.lock.locked { self.status = r.statusCode } },
            onData: { d in self.lock.locked { self.bytes.append(d) } },
            onComplete: { e in self.lock.locked { self.completion.append(e) } })
    }
}

final class URLSessionTransportTests: XCTestCase {
    private func transport() -> (URLSessionTransport, URLSessionConfiguration) {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        config.timeoutIntervalForRequest = 10
        return (URLSessionTransport(configuration: config), config)
    }

    func testDataReturnsTheBodyAndTheResponse() async throws {
        let (t, _) = transport()
        let (data, response) = try await t.data(for: URLRequest(url: URL(string: "https://flags.example.com/body")!))
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "body")
        XCTAssertEqual(response.statusCode, 200)
        XCTAssertEqual(response.value(forHTTPHeaderField: "X-Test"), "1")
    }

    func testStreamForwardsTheResponseTheChunksAndTheEnd() async {
        let (t, _) = transport()
        let d = Delivered()
        _ = d.open(t, "https://flags.example.com/x")
        await waitUntil("completion") { d.completed.count == 1 }
        XCTAssertEqual(d.response, 200)
        XCTAssertEqual(d.data, "onetwo")
        XCTAssertNil(d.completed.first ?? nil)
    }

    func testCancelEndsTheStreamWithAnError() async {
        let (t, _) = transport()
        let d = Delivered()
        let handle = d.open(t, "https://flags.example.com/open")
        await waitUntil("the first chunk") { d.data == "one" }
        handle.cancel()
        await waitUntil("completion") { d.completed.count == 1 }
        XCTAssertNotNil(d.completed.first ?? nil)
    }

    func testTheStreamSessionHasA45SecondIdleTimeoutAndNoCache() {
        let (t, config) = transport()
        XCTAssertEqual(t.streamConfiguration.timeoutIntervalForRequest, 45)
        XCTAssertEqual(t.streamConfiguration.requestCachePolicy, .reloadIgnoringLocalCacheData)
        XCTAssertEqual(config.timeoutIntervalForRequest, 10)
        XCTAssertNotEqual(config.requestCachePolicy, .reloadIgnoringLocalCacheData)
    }
}
