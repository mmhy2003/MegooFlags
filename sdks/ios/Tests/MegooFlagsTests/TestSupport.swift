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

/// A data request held by FakeTransport until the test answers or fails it;
/// a cancelled request fails with URLError(.cancelled), as with URLSession.
final class HeldRequest: @unchecked Sendable {
    typealias Reply = Result<(Data, HTTPURLResponse), any Error>

    let request: URLRequest
    private let lock = NSLock()
    private var continuation: CheckedContinuation<(Data, HTTPURLResponse), any Error>?
    private var reply: Reply?
    private var done = false

    init(request: URLRequest) {
        self.request = request
    }

    /// The If-None-Match header.
    var etag: String? { request.value(forHTTPHeaderField: "If-None-Match") }

    /// The body as JSON.
    var body: JSONValue? {
        request.httpBody.flatMap { try? JSONDecoder().decode(JSONValue.self, from: $0) }
    }

    /// The body's context member.
    var context: JSONValue? {
        guard case .object(let o)? = body else { return nil }
        return o["context"]
    }

    func answer(_ status: Int, _ body: String = "", headers: [String: String] = [:]) {
        complete(.success((Data(body.utf8), httpResponse(request, status, headers))))
    }

    func fail(_ error: any Error) {
        complete(.failure(error))
    }

    func attach(_ c: CheckedContinuation<(Data, HTTPURLResponse), any Error>) {
        let ready: Reply? = lock.locked {
            if reply == nil { continuation = c }
            return reply
        }
        if let ready {
            c.resume(with: ready)
        }
    }

    /// complete delivers the first reply only.
    private func complete(_ r: Reply) {
        let c: CheckedContinuation<(Data, HTTPURLResponse), any Error>? = lock.locked {
            guard !done else { return nil }
            done = true
            let c = continuation
            continuation = nil
            if c == nil {
                reply = r
            }
            return c
        }
        c?.resume(with: r)
    }
}

/// Simulated time for code with an injected sleep. It starts at
/// 2026-11-10T09:15:02Z; a sleep waits until the test advances the clock past
/// its deadline, or until its task is cancelled.
final class ManualClock: @unchecked Sendable {
    private struct Sleeper {
        let id: Int
        let deadline: Double
        let continuation: CheckedContinuation<Void, any Error>
    }

    private let origin = Date(timeIntervalSince1970: 1_794_302_102)
    private let lock = NSLock()
    private var elapsed: Double = 0
    private var sleepers: [Sleeper] = []
    private var cancelled = Set<Int>()
    private var nextID = 0
    private var waits: [Double] = []

    var now: Date { lock.locked { origin + elapsed } }
    /// Every wait asked for, in order.
    var sleeps: [Double] { lock.locked { waits } }
    /// The sleeps still waiting.
    var pending: Int { lock.locked { sleepers.count } }

    func sleep(_ seconds: Double) async throws {
        let id = lock.locked {
            nextID += 1
            waits.append(seconds)
            return nextID
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, any Error>) in
                let now: Result<Void, any Error>? = lock.locked {
                    if cancelled.remove(id) != nil { return .failure(CancellationError()) }
                    if seconds <= 0 { return .success(()) }
                    sleepers.append(Sleeper(id: id, deadline: elapsed + seconds, continuation: c))
                    return nil
                }
                if let now {
                    c.resume(with: now)
                }
            }
        } onCancel: {
            let c: CheckedContinuation<Void, any Error>? = lock.locked {
                if let i = sleepers.firstIndex(where: { $0.id == id }) {
                    return sleepers.remove(at: i).continuation
                }
                cancelled.insert(id)
                return nil
            }
            c?.resume(throwing: CancellationError())
        }
    }

    /// advance moves time forward, waking the sleeps whose deadline passes in
    /// deadline order and giving each woken task time to run before the next.
    func advance(_ seconds: Double) async {
        let target = lock.locked { elapsed + seconds }
        while true {
            let due: Sleeper? = lock.locked {
                guard let i = sleepers.indices.min(by: { sleepers[$0].deadline < sleepers[$1].deadline }),
                    sleepers[i].deadline <= target + 1e-9
                else { return nil }
                let s = sleepers.remove(at: i)
                elapsed = max(elapsed, s.deadline)
                return s
            }
            guard let due else { break }
            due.continuation.resume()
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        lock.locked { elapsed = target }
        await settle()
    }

    /// sleepers waits until n sleeps are waiting.
    func sleepers(_ n: Int, file: StaticString = #filePath, line: UInt = #line) async {
        await waitUntil("\(n) sleeps", file: file, line: line) { self.pending >= n }
    }
}

/// A scripted HTTPTransport: data(for:) answers from dataReplies (or
/// throws), or holds each request until the test answers it (holdData);
/// stream(for:...) answers from streamReplies, 200 when empty, unless
/// autoRespond is false.
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
    private var holding = false
    private var heldRequests: [HeldRequest] = []

    /// When true, data(for:) waits for the test to answer each request.
    var holdData: Bool {
        get { lock.locked { holding } }
        set { lock.locked { holding = newValue } }
    }

    /// The requests held so far, answered or not, in order.
    var held: [HeldRequest] { lock.locked { heldRequests } }

    /// heldCount waits until n requests were held.
    func heldCount(_ n: Int, file: StaticString = #filePath, line: UInt = #line) async {
        await waitUntil("\(n) requests", file: file, line: line) { self.held.count >= n }
    }

    var autoRespond: Bool {
        get { lock.locked { answers } }
        set { lock.locked { answers = newValue } }
    }

    /// When false, a cancelled connection or held request still delivers
    /// what the test sends.
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
        let hold: (HeldRequest, Bool)? = lock.locked {
            guard holding else { return nil }
            let h = HeldRequest(request: request)
            dataRequests.append(request)
            heldRequests.append(h)
            return (h, cancels)
        }
        if case (let h, let cancels)? = hold {
            return try await withTaskCancellationHandler {
                try await withCheckedThrowingContinuation { h.attach($0) }
            } onCancel: {
                if cancels {
                    h.fail(URLError(.cancelled))
                }
            }
        }
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
