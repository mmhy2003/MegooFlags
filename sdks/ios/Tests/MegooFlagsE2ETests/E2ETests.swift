// Runs MegooFlagsProvider through OpenFeatureAPI against a running MegooFlags
// stack: evaluation, toggles over the stream, a context change, exposures
// counted by the server and the cache serving values with the API
// unreachable. Set MEGOOFLAGS_E2E_URL (for example http://localhost:8080) and
// MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it.
import Combine
import Foundation
import MegooFlags
import OpenFeature
import XCTest

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif
#if canImport(Glibc)
    import Glibc
#endif

private let env = ProcessInfo.processInfo.environment
private let base = (env["MEGOOFLAGS_E2E_URL"] ?? "").replacingOccurrences(
    of: "/+$", with: "", options: .regularExpression)
private let token = env["MEGOOFLAGS_API_TOKEN"] ?? ""

/// Keeps every log line as "<level>: <message>".
private final class CapturingLogger: MegooFlagsLogger, @unchecked Sendable {
    private let lock = NSLock()
    private var kept: [String] = []

    func log(level: MegooFlagsLogLevel, message: String) {
        lock.lock()
        defer { lock.unlock() }
        kept.append("\(level): \(message)")
    }

    func count(_ text: String) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return kept.filter { $0.contains(text) }.count
    }
}

/// Counts the provider's configuration-changed events.
private final class ChangeCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var changes = 0
    private var subscription: AnyCancellable?

    init(_ provider: MegooFlagsProvider) {
        subscription = provider.observe().sink { [weak self] event in
            if case .configurationChanged = event { self?.add() }
        }
    }

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return changes
    }

    private func add() {
        lock.lock()
        defer { lock.unlock() }
        changes += 1
    }
}

private struct APIError: Error, CustomStringConvertible {
    let description: String
}

/// api calls the management API with the API token and returns the status
/// and the JSON object of the body (empty when there is none).
private func api(_ method: String, _ path: String, _ body: [String: Any]? = nil) async throws -> (
    Int, [String: Any]
) {
    var request = URLRequest(url: URL(string: base + path)!, timeoutInterval: 10)
    request.httpMethod = method
    request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    if let body {
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
    }
    let (data, response) = try await URLSession.shared.data(for: request)
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    let json = data.isEmpty ? [:] : (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    return (status, json)
}

/// mustAPI calls api and fails unless the status is want.
@discardableResult
private func mustAPI(_ want: Int, _ method: String, _ path: String, _ body: [String: Any]? = nil) async throws
    -> [String: Any]
{
    let (status, json) = try await api(method, path, body)
    guard status == want else {
        throw APIError(description: "\(method) \(path): status \(status), want \(want): \(json)")
    }
    return json
}

/// until polls check every interval seconds until it holds or timeout passes.
private func until(
    _ what: String, timeout: TimeInterval, interval: TimeInterval = 0.05, _ check: () async throws -> Bool
) async throws {
    let end = Date().addingTimeInterval(timeout)
    while try await !check() {
        guard Date() < end else { throw APIError(description: "\(what): not within \(timeout) s") }
        try await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
    }
}

/// closedPort returns a local port nothing listens on: it binds a socket to
/// port 0, reads the port the system chose and closes the socket.
private func closedPort() throws -> Int {
    #if canImport(Glibc)
        let fd = socket(AF_INET, Int32(SOCK_STREAM.rawValue), 0)
    #else
        let fd = socket(AF_INET, SOCK_STREAM, 0)
    #endif
    guard fd >= 0 else { throw APIError(description: "socket failed") }
    defer { close(fd) }
    var addr = sockaddr_in()
    #if canImport(Darwin)
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    #endif
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    var size = socklen_t(MemoryLayout<sockaddr_in>.size)
    let bound = withUnsafeMutablePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(fd, $0, size) == 0 && getsockname(fd, $0, &size) == 0
        }
    }
    guard bound else { throw APIError(description: "bind failed") }
    return Int(UInt16(bigEndian: addr.sin_port))
}

private func user(_ targetingKey: String) -> ImmutableContext {
    ImmutableContext(targetingKey: targetingKey, structure: ImmutableStructure(attributes: [:]))
}

final class E2ETests: XCTestCase {
    private var project: String?
    private var providers: [MegooFlagsProvider] = []

    override func setUpWithError() throws {
        try XCTSkipIf(base.isEmpty || token.isEmpty, "set MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN")
    }

    override func tearDown() async throws {
        for provider in providers {
            await provider.shutdown()
        }
        if let project {
            _ = try? await api("DELETE", "/api/projects/\(project)")
        }
    }

    /// setUpProject creates project sdkios-<8 hex> with environment e2e, the
    /// boolean flag e2e-flag turned on (false for vip-user, true for everyone
    /// else) and a client key; it returns the flag's environment path and the
    /// key.
    private func setUpProject() async throws -> (flagPath: String, sdkKey: String) {
        let project = "sdkios-\(UUID().uuidString.lowercased().prefix(8))"
        let flagPath = "/api/projects/\(project)/flags/e2e-flag/environments/e2e"
        try await mustAPI(
            201, "POST", "/api/projects",
            ["key": project, "name": project, "environments": [["key": "e2e", "name": "E2E"]]])
        self.project = project
        try await mustAPI(
            201, "POST", "/api/projects/\(project)/flags", ["key": "e2e-flag", "name": "E2E", "type": "boolean"])
        try await mustAPI(200, "POST", "\(flagPath)/toggle", ["enabled": true])
        let config = try await mustAPI(200, "GET", flagPath)
        try await mustAPI(
            200, "PUT", flagPath,
            [
                "version": config["version"] ?? NSNull(), "enabled": true,
                "targets": [["variation": "false", "values": ["vip-user"]]], "rules": [],
                "fallthrough": ["variation": "true"], "prerequisites": [],
            ])
        let created = try await mustAPI(
            201, "POST", "/api/projects/\(project)/environments/e2e/sdk-keys", ["name": "ios-e2e", "kind": "client"])
        guard let sdkKey = created["key"] as? String, sdkKey.hasPrefix("mf_client_") else {
            throw APIError(description: "POST sdk-keys returned no client key")
        }
        return (flagPath, sdkKey)
    }

    func testEvaluatesFollowsTogglesAndContextSendsExposuresAndServesTheCache() async throws {
        let (flagPath, sdkKey) = try await setUpProject()
        let logger = CapturingLogger()
        let openFeature = OpenFeatureAPI()
        let client = openFeature.getClient()
        let provider = try MegooFlagsProvider(
            config: MegooFlagsConfig(sdkKey: sdkKey, baseURL: URL(string: base), flushInterval: 10, logger: logger))
        providers.append(provider)
        let changes = ChangeCounter(provider)
        await openFeature.setProviderAndWait(provider: provider, initialContext: user("user-1"))

        // Check 1: the server evaluates the flag for the context.
        XCTAssertEqual(provider.status, .ready)
        XCTAssertEqual(client.getBooleanValue(key: "e2e-flag", defaultValue: false), true)

        // Check 2: toggles arrive over the stream within 5 s.
        try await until("the stream connected", timeout: 5) { logger.count("megooflags: stream connected") > 0 }
        try await mustAPI(200, "POST", "\(flagPath)/toggle", ["enabled": false])
        try await until("e2e-flag false after the toggle", timeout: 5) {
            !client.getBooleanValue(key: "e2e-flag", defaultValue: true)
        }
        try await until("a configuration-changed event", timeout: 5) { changes.count > 0 }
        try await mustAPI(200, "POST", "\(flagPath)/toggle", ["enabled": true])
        try await until("e2e-flag true after the second toggle", timeout: 5) {
            client.getBooleanValue(key: "e2e-flag", defaultValue: false)
        }

        // Check 3: a new targeting key is evaluated by the server.
        await openFeature.setEvaluationContextAndWait(evaluationContext: user("vip-user"))
        let vip = client.getBooleanDetails(key: "e2e-flag", defaultValue: true)
        XCTAssertEqual(vip.value, false)
        XCTAssertEqual(vip.reason, "TARGETING_MATCH")

        // Check 4: exposures are sent at shutdown and counted.
        for _ in 0..<5 {
            _ = client.getBooleanValue(key: "e2e-flag", defaultValue: true)
        }
        await provider.shutdown()
        XCTAssertGreaterThan(logger.count("megooflags: sent "), 0)
        try await until("evaluations total >= 1", timeout: 10, interval: 0.5) {
            let out = try await mustAPI(200, "GET", "\(flagPath)/evaluations?range=24h")
            return ((out["total"] as? NSNumber)?.intValue ?? 0) >= 1
        }

        // Check 5: with the API unreachable, a new provider serves the values
        // the first one left in the same default cache directory.
        let offlineLogger = CapturingLogger()
        let offlineAPI = OpenFeatureAPI()
        let offline = try MegooFlagsProvider(
            config: MegooFlagsConfig(
                sdkKey: sdkKey, baseURL: URL(string: "http://127.0.0.1:\(try closedPort())"), initTimeout: 1,
                logger: offlineLogger))
        providers.append(offline)
        await offlineAPI.setProviderAndWait(provider: offline, initialContext: user("vip-user"))
        // The background refresh fails; the cached values stay. Shutting down
        // after it, during the retry wait, also keeps teardown clear of a
        // swift-corelibs-foundation deadlock (cancelling a URLSession request
        // while it completes) when the test runs on Linux.
        try await until("the evaluate failed", timeout: 5) { offlineLogger.count("megooflags: evaluate failed") > 0 }
        let cached = offlineAPI.getClient().getBooleanDetails(key: "e2e-flag", defaultValue: true)
        XCTAssertEqual(cached.value, false)
        XCTAssertEqual(cached.reason, "CACHED")
    }
}
