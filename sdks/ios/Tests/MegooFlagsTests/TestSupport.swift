// Helpers shared by the cache, evaluator and streamer tests: a logger that
// keeps its lines, a fake HTTPTransport and polling waits.
import Foundation
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// A logger that keeps "<level> <message>" lines.
final class LineLogger: MegooFlagsLogger, @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []

    func log(level: MegooFlagsLogLevel, message: String) {
        lock.lock()
        defer { lock.unlock() }
        kept.append("\(level) \(message)")
    }

    var lines: [String] {
        lock.lock()
        defer { lock.unlock() }
        return kept
    }
}

extension NSLock {
    /// locked runs body holding the lock (NSLock.withLock needs iOS 16).
    func locked<T>(_ body: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try body()
    }
}

/// waitUntil polls condition every millisecond for up to two seconds.
func waitUntil(
    _ what: String = "condition", file: StaticString = #filePath, line: UInt = #line,
    _ condition: @Sendable () async -> Bool
) async {
    for _ in 0..<2000 {
        if await condition() { return }
        try? await Task.sleep(nanoseconds: 1_000_000)
    }
    XCTFail("timed out waiting for \(what)", file: file, line: line)
}

/// settle gives running tasks time to do what they would do next.
func settle() async {
    try? await Task.sleep(nanoseconds: 30_000_000)
}

func httpResponse(_ request: URLRequest, _ status: Int, _ headers: [String: String] = [:]) -> HTTPURLResponse {
    HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
}

/// One stream the fake transport opened. The test sends lines and ends it;
/// after cancel nothing more is delivered, as with URLSession.
final class FakeConnection: CancelHandle, @unchecked Sendable {
    let request: URLRequest
    private let lock = NSLock()
    private var onResponse: (@Sendable (HTTPURLResponse) -> Void)?
    private var onData: (@Sendable (Data) -> Void)?
    private var onComplete: (@Sendable ((any Error)?) -> Void)?
    private var wasCancelled = false
    private let honorsCancel: Bool

    init(
        request: URLRequest, honorsCancel: Bool, onResponse: @escaping @Sendable (HTTPURLResponse) -> Void,
        onData: @escaping @Sendable (Data) -> Void, onComplete: @escaping @Sendable ((any Error)?) -> Void
    ) {
        self.request = request
        self.honorsCancel = honorsCancel
        self.onResponse = onResponse
        self.onData = onData
        self.onComplete = onComplete
    }

    var cancelled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return wasCancelled
    }

    func header(_ name: String) -> String? { request.value(forHTTPHeaderField: name) }

    func respond(_ status: Int, _ headers: [String: String] = [:]) {
        let call = lock.locked { onResponse }
        call?(httpResponse(request, status, headers))
    }

    func send(_ text: String) {
        let call = lock.locked { onData }
        call?(Data(text.utf8))
    }

    /// complete ends the stream, with error or cleanly.
    func complete(_ error: (any Error)? = nil) {
        let call = lock.locked {
            let c = onComplete
            onResponse = nil
            onData = nil
            onComplete = nil
            return c
        }
        call?(error)
    }

    func cancel() {
        lock.locked { wasCancelled = true }
        if honorsCancel {
            complete(URLError(.cancelled))
        }
    }
}

/// A scripted HTTPTransport: data(for:) answers from dataReplies (or
/// throws), stream(for:...) answers from streamReplies, 200 when empty,
/// unless autoRespond is false.
final class FakeTransport: HTTPTransport, @unchecked Sendable {
    enum DataReply {
        case response(Int, [String: String], String)
        case failure(any Error)
    }

    private let lock = NSLock()
    private var dataReplies: [DataReply] = []
    private var streamReplies: [(Int, [String: String])] = []
    private var dataRequests: [URLRequest] = []
    private var opened: [FakeConnection] = []
    private var answers = true
    private var cancels = true

    var autoRespond: Bool {
        get { lock.locked { answers } }
        set { lock.locked { answers = newValue } }
    }

    /// When false, a cancelled connection still delivers what the test sends.
    var honorsCancel: Bool {
        get { lock.locked { cancels } }
        set { lock.locked { cancels = newValue } }
    }

    func replyData(_ status: Int, _ body: String = "", headers: [String: String] = [:]) {
        lock.locked { dataReplies.append(.response(status, headers, body)) }
    }

    func failData(_ error: any Error) {
        lock.locked { dataReplies.append(.failure(error)) }
    }

    func replyStream(_ status: Int, _ headers: [String: String] = [:]) {
        lock.locked { streamReplies.append((status, headers)) }
    }

    var requests: [URLRequest] { lock.locked { dataRequests } }
    var connections: [FakeConnection] { lock.locked { opened } }
    var last: FakeConnection { connections.last! }

    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let reply = lock.locked {
            dataRequests.append(request)
            return dataReplies.isEmpty ? .response(200, [:], #"{"results":[]}"#) : dataReplies.removeFirst()
        }
        switch reply {
        case .response(let status, let headers, let body):
            return (Data(body.utf8), httpResponse(request, status, headers))
        case .failure(let error):
            throw error
        }
    }

    func stream(
        for request: URLRequest, onResponse: @escaping @Sendable (HTTPURLResponse) -> Void,
        onData: @escaping @Sendable (Data) -> Void, onComplete: @escaping @Sendable ((any Error)?) -> Void
    ) -> any CancelHandle {
        let (c, reply) = lock.locked {
            let c = FakeConnection(
                request: request, honorsCancel: cancels, onResponse: onResponse, onData: onData,
                onComplete: onComplete)
            opened.append(c)
            let reply = streamReplies.isEmpty ? (200, [String: String]()) : streamReplies.removeFirst()
            return (c, answers ? reply : nil)
        }
        if let reply {
            c.respond(reply.0, reply.1)
        }
        return c
    }
}
