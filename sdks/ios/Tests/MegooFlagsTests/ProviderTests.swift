import Combine
import Foundation
import OpenFeature
import XCTest

@testable import MegooFlags

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private let key = "mf_client_secret123"
private let base = URL(string: "https://flags.example.com")!

private func user(_ targetingKey: String) -> ImmutableContext {
    ImmutableContext(targetingKey: targetingKey, structure: ImmutableStructure(attributes: [:]))
}

/// An evaluate body with flag f.
private func body(value: String = "true", variant: String = "on") -> String {
    #"{"results":[{"key":"f","value":\#(value),"variant":"\#(variant)","reason":"TARGETING_MATCH","error_code":null,"error_message":null}]}"#
}

/// Hands a value to a Task; OpenFeatureAPI is not Sendable but serialises
/// its state on its own queues.
private struct Unchecked<T>: @unchecked Sendable {
    let value: T
}

/// Keeps the provider's events as "ready", "error <code>",
/// "configurationChanged a,b", "stale", "reconciling", "contextChanged".
private final class Events: @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []
    private var subscription: AnyCancellable?

    init(_ provider: MegooFlagsProvider) {
        subscription = provider.observe().sink { [weak self] e in self?.add(e) }
    }

    var lines: [String] { lock.locked { kept } }

    private func add(_ e: ProviderEvent) {
        let line: String
        switch e {
        case .ready: line = "ready"
        case .error(let d): line = "error \(d?.errorCode.map { "\($0)" } ?? "nil")"
        case .configurationChanged(let d):
            line = "configurationChanged \((d?.flagsChanged ?? []).joined(separator: ","))"
        case .stale: line = "stale"
        case .reconciling: line = "reconciling"
        case .contextChanged: line = "contextChanged"
        }
        lock.locked { kept.append(line) }
    }
}

final class ProviderTests: XCTestCase {
    private var transport: FakeTransport!
    private var logger: LineLogger!
    private var cacheDir: URL!
    private var api: OpenFeatureAPI!
    private var provider: MegooFlagsProvider?

    override func setUp() {
        transport = FakeTransport()
        logger = LineLogger()
        cacheDir = FileManager.default.temporaryDirectory.appendingPathComponent("megooflags-provider-\(UUID())")
        api = OpenFeatureAPI()
    }

    override func tearDown() async throws {
        // Answer the last exposures instead of waiting 5 s for them.
        transport.holdData = false
        await provider?.shutdown()
        try? FileManager.default.removeItem(at: cacheDir)
    }

    private func make(bootstrap: String? = nil, offline: Bool = false) throws -> MegooFlagsProvider {
        let config = MegooFlagsConfig(
            sdkKey: key, baseURL: offline ? nil : base, bootstrap: bootstrap, offline: offline, logger: logger)
        let p = try MegooFlagsProvider(
            config: config, transport: transport, cacheDirectory: cacheDir, notificationCenter: NotificationCenter(),
            backgroundNames: nil)
        provider = p
        return p
    }

    private var client: Client { api.getClient() }

    private func streamOpened() async {
        let transport = transport!
        await waitUntil("stream") { transport.connections.count == 1 }
    }

    private var eventRequests: [URLRequest] { transport.requests.filter { $0.url!.path == "/sdk/v1/events" } }

    func testRefusesAServerKey() {
        XCTAssertThrowsError(try MegooFlagsProvider(config: MegooFlagsConfig(sdkKey: "mf_server_x", baseURL: base))) {
            XCTAssertEqual(
                "\($0)", "megooflags: a server key must never ship in an app; use a client key (mf_client_…)")
        }
    }

    func testMetadataAndHooks() throws {
        let p = try make()
        XCTAssertEqual(p.metadata.name, "MegooFlags")
        XCTAssertTrue(p.hooks.isEmpty)
        XCTAssertEqual(p.status, .notReady)
    }

    func testServesTheServerValueAfterSetProviderAndWait() async throws {
        transport.replyData(200, body())
        let p = try make()
        let events = Events(p)
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(p.status, .ready)
        let details = client.getBooleanDetails(key: "f", defaultValue: false)
        XCTAssertEqual(details.value, true)
        XCTAssertEqual(details.variant, "on")
        XCTAssertEqual(details.reason, "TARGETING_MATCH")
        XCTAssertNil(details.errorCode)
        XCTAssertEqual(transport.requests.first?.url?.path, "/sdk/v1/evaluate")
        await waitUntil("ready") { events.lines == ["ready"] }
    }

    func testEveryTypeReads() async throws {
        transport.replyData(
            200,
            #"{"results":[{"key":"s","value":"x","variant":"a","reason":"STATIC","error_code":null},"#
                + #"{"key":"i","value":3,"variant":"a","reason":"STATIC","error_code":null},"#
                + #"{"key":"d","value":1.5,"variant":"a","reason":"STATIC","error_code":null},"#
                + #"{"key":"o","value":{"k":true},"variant":"a","reason":"STATIC","error_code":null}]}"#)
        let p = try make()
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(client.getStringValue(key: "s", defaultValue: ""), "x")
        XCTAssertEqual(client.getIntegerValue(key: "i", defaultValue: 0), 3)
        XCTAssertEqual(client.getDoubleValue(key: "d", defaultValue: 0), 1.5)
        XCTAssertEqual(client.getObjectValue(key: "o", defaultValue: .null), .structure(["k": .boolean(true)]))
        XCTAssertEqual(client.getBooleanDetails(key: "missing", defaultValue: true).errorCode, .flagNotFound)
    }

    func testBootstrapIsReadyAtOnce() async throws {
        transport.holdData = true
        let p = try make(bootstrap: body(value: "false", variant: "off"))
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(p.status, .ready)
        XCTAssertEqual(client.getBooleanValue(key: "f", defaultValue: true), false)
    }

    func testAStreamChangeEmitsConfigurationChanged() async throws {
        transport.replyData(200, body())
        transport.replyData(200, body(value: "false", variant: "off"))
        let p = try make()
        let events = Events(p)
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        await streamOpened()
        transport.last.send("event: changed\nid: 1\ndata: {}\n\n")
        await waitUntil("change") { events.lines == ["ready", "configurationChanged f"] }
        XCTAssertEqual(client.getBooleanValue(key: "f", defaultValue: true), false)
    }

    func testAnotherTargetingKeyReconcilesAndServesDefaultsWhilePending() async throws {
        transport.holdData = true
        let p = try make()
        let events = Events(p)
        let api = Unchecked(value: self.api!)
        let start = Task { await api.value.setProviderAndWait(provider: p, initialContext: user("user-1")) }
        await transport.heldCount(1)
        transport.held[0].answer(200, body())
        await start.value
        XCTAssertEqual(client.getBooleanValue(key: "f", defaultValue: false), true)

        let change = Task { await api.value.setEvaluationContextAndWait(evaluationContext: user("user-2")) }
        await transport.heldCount(2)
        XCTAssertEqual(transport.held[1].context, .object(["key": .string("user-2")]))
        await waitUntil("reconciling") { p.status == .reconciling }
        let pending = client.getBooleanDetails(key: "f", defaultValue: true)
        XCTAssertEqual(pending.value, true)
        XCTAssertEqual(pending.errorCode, .providerNotReady)

        transport.held[1].answer(200, body(value: "false", variant: "off"))
        await change.value
        XCTAssertEqual(p.status, .ready)
        XCTAssertEqual(client.getBooleanValue(key: "f", defaultValue: true), false)
        await waitUntil("events") { events.lines == ["ready", "reconciling", "contextChanged"] }
    }

    func testAnotherTargetingKeyStopsServingTheOldValuesAtOnce() async throws {
        transport.replyData(200, body())
        let p = try make()
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(try p.getBooleanEvaluation(key: "f", defaultValue: false, context: nil).value, true)
        transport.holdData = true

        let change = p.onContextSet(oldContext: user("user-1"), newContext: user("user-2"))
        let pending = try p.getBooleanEvaluation(key: "f", defaultValue: false, context: nil)

        XCTAssertEqual(pending.value, false)
        XCTAssertEqual(pending.errorCode, .providerNotReady)
        await transport.heldCount(1)
        transport.held[0].answer(200, body(value: "false", variant: "off"))
        await change.value
    }

    func testAFailedContextChangeEmitsError() async throws {
        transport.replyData(200, body())
        transport.replyData(500)
        let p = try make()
        let events = Events(p)
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        await api.setEvaluationContextAndWait(evaluationContext: user("user-2"))
        XCTAssertEqual(p.status, .error)
        await waitUntil("events") { events.lines == ["ready", "reconciling", "error general"] }
    }

    func testARefusedKeyIsFatal() async throws {
        transport.replyData(401)
        let p = try make()
        let events = Events(p)
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(p.status, .fatal)
        await waitUntil("fatal") { events.lines == ["error providerFatal"] }
        XCTAssertEqual(transport.connections.count, 0)
    }

    func testOfflineWithoutValuesIsAnError() async throws {
        let p = try make(offline: true)
        let events = Events(p)
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        XCTAssertEqual(p.status, .error)
        await waitUntil("error") { events.lines == ["error general"] }
    }

    func testShutdownSendsTheExposures() async throws {
        transport.replyData(200, body())
        transport.replyData(202)
        let p = try make()
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        await streamOpened()
        _ = client.getBooleanValue(key: "f", defaultValue: false)
        _ = client.getBooleanValue(key: "f", defaultValue: false)
        _ = client.getBooleanValue(key: "missing", defaultValue: false)
        await p.shutdown()
        XCTAssertEqual(eventRequests.count, 1)
        let sent = try JSONDecoder().decode(JSONValue.self, from: eventRequests[0].httpBody ?? Data())
        guard case .object(let o) = sent, case .array(let items)? = o["exposures"], items.count == 1,
            case .object(let item) = items[0]
        else {
            return XCTFail("unexpected body \(sent)")
        }
        XCTAssertEqual(item["flag_key"], .string("f"))
        XCTAssertEqual(item["variant"], .string("on"))
        XCTAssertNil(item["context"])
        XCTAssertTrue(logger.lines.contains("debug megooflags: sent 1 exposures"))
        XCTAssertTrue(transport.last.cancelled)
    }

    func testShutdownTwiceIsSafe() async throws {
        transport.replyData(200, body())
        transport.replyData(202)
        let p = try make()
        await api.setProviderAndWait(provider: p, initialContext: user("user-1"))
        _ = client.getBooleanValue(key: "f", defaultValue: false)
        await p.shutdown()
        await p.shutdown()
        XCTAssertEqual(eventRequests.count, 1)
    }
}
