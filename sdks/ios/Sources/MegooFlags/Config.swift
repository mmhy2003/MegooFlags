// The provider's configuration, its validation and defaults, and the
// evaluate result shape shared by bootstrap, the cache and the evaluate
// response. Messages never contain the SDK key.
import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// How a MegooFlagsProvider connects and behaves. Times are in seconds.
public struct MegooFlagsConfig {
    /// A client SDK key (mf_client_…).
    public var sdkKey: String
    /// The MegooFlags server; required unless offline.
    public var baseURL: URL?
    /// The body of an evaluate response ({"results": [...]}) as JSON text,
    /// served until the cache or the network has values.
    public var bootstrap: String?
    public var disableCache: Bool
    /// No network and no events; needs bootstrap or a cache entry.
    public var offline: Bool
    public var initTimeout: TimeInterval
    /// Poll instead of following the stream.
    public var disableStream: Bool
    /// Raised to 15 seconds when smaller.
    public var pollInterval: TimeInterval
    /// Clamped to 10…60 seconds.
    public var flushInterval: TimeInterval
    /// Send the context with exposure events.
    public var sendContext: Bool
    /// nil: unified logging (OSLogLogger).
    public var logger: (any MegooFlagsLogger)?
    public var sessionConfiguration: URLSessionConfiguration

    public init(
        sdkKey: String, baseURL: URL? = nil, bootstrap: String? = nil, disableCache: Bool = false,
        offline: Bool = false, initTimeout: TimeInterval = 5, disableStream: Bool = false,
        pollInterval: TimeInterval = 30, flushInterval: TimeInterval = 30, sendContext: Bool = false,
        logger: (any MegooFlagsLogger)? = nil, sessionConfiguration: URLSessionConfiguration = .default
    ) {
        self.sdkKey = sdkKey
        self.baseURL = baseURL
        self.bootstrap = bootstrap
        self.disableCache = disableCache
        self.offline = offline
        self.initTimeout = initTimeout
        self.disableStream = disableStream
        self.pollInterval = pollInterval
        self.flushInterval = flushInterval
        self.sendContext = sendContext
        self.logger = logger
        self.sessionConfiguration = sessionConfiguration
    }
}

/// An invalid MegooFlagsConfig; the provider's initialiser throws it.
public struct MegooFlagsConfigError: Error, CustomStringConvertible {
    public let description: String
}

/// One evaluated flag, as POST /sdk/v1/evaluate returns it.
struct EvaluateResult: Equatable, Sendable, Codable {
    let key: String
    let value: JSONValue
    let variant: String?
    let reason: String
    let errorCode: String?
    let errorMessage: String?

    enum CodingKeys: String, CodingKey {
        case key, value, variant, reason
        case errorCode = "error_code"
        case errorMessage = "error_message"
    }

    init(key: String, value: JSONValue, variant: String?, reason: String, errorCode: String?, errorMessage: String?) {
        self.key = key
        self.value = value
        self.variant = variant
        self.reason = reason
        self.errorCode = errorCode
        self.errorMessage = errorMessage
    }

    /// Decoding requires variant and error_code (string or null); a missing
    /// error_message is nil.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        value = try c.decode(JSONValue.self, forKey: .value)
        variant = try c.decode(String?.self, forKey: .variant)
        reason = try c.decode(String.self, forKey: .reason)
        errorCode = try c.decode(String?.self, forKey: .errorCode)
        errorMessage = try c.decodeIfPresent(String.self, forKey: .errorMessage)
    }

    /// Encoding writes nil fields as null, so stored results decode again.
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(key, forKey: .key)
        try c.encode(value, forKey: .value)
        try c.encode(variant, forKey: .variant)
        try c.encode(reason, forKey: .reason)
        try c.encode(errorCode, forKey: .errorCode)
        try c.encode(errorMessage, forKey: .errorMessage)
    }
}

private struct EvaluateBody: Decodable {
    let results: [EvaluateResult]
}

/// parseResults reads an evaluate response body ({"results": [...]}) and
/// returns its results. It throws MegooFlagsConfigError("<prefix>: <path>:
/// <why>"); extra fields are dropped and duplicate keys are refused.
func parseResults(_ data: Data, prefix: String) throws -> [EvaluateResult] {
    let fail = { (path: [CodingKey], why: String) in
        MegooFlagsConfigError(description: path.isEmpty ? "\(prefix): \(why)" : "\(prefix): \(pathText(path)): \(why)")
    }
    let results: [EvaluateResult]
    do {
        results = try JSONDecoder().decode(EvaluateBody.self, from: data).results
    } catch DecodingError.keyNotFound(let key, let context) {
        throw fail(context.codingPath + [key], "missing")
    } catch DecodingError.typeMismatch(_, let context), DecodingError.valueNotFound(_, let context) {
        throw fail(context.codingPath, expectation(at: context.codingPath.last))
    } catch {
        throw fail([], "not valid JSON")
    }
    var seen = Set<String>()
    for (i, r) in results.enumerated() where !seen.insert(r.key).inserted {
        throw MegooFlagsConfigError(description: "\(prefix): results[\(i)].key: duplicate key")
    }
    return results
}

/// pathText writes a coding path as results[0].key.
private func pathText(_ path: [CodingKey]) -> String {
    path.reduce(into: "") { text, key in
        if let i = key.intValue {
            text += "[\(i)]"
        } else {
            text += text.isEmpty ? key.stringValue : ".\(key.stringValue)"
        }
    }
}

/// expectation is what the member at key should have been.
private func expectation(at key: CodingKey?) -> String {
    guard let key, key.intValue == nil else { return "not an object" }
    switch key.stringValue {
    case "results": return "not an array"
    case "variant", "error_code", "error_message": return "not a string or null"
    default: return "not a string"
    }
}

/// MegooFlagsConfig with every default filled and every value checked.
struct ResolvedConfig {
    let sdkKey: String
    /// Without trailing slashes; nil when offline without a base URL.
    let baseURL: URL?
    let bootstrap: [EvaluateResult]?
    let disableCache: Bool
    let offline: Bool
    let initTimeout: TimeInterval
    let disableStream: Bool
    let pollInterval: TimeInterval
    let flushInterval: TimeInterval
    let sendContext: Bool
    let logger: any MegooFlagsLogger
    let sessionConfiguration: URLSessionConfiguration

    static let minPollInterval: TimeInterval = 15
    static let flushIntervals: ClosedRange<TimeInterval> = 10...60

    init(_ c: MegooFlagsConfig) throws {
        if c.sdkKey.hasPrefix("mf_server_") {
            throw MegooFlagsConfigError(
                description: "megooflags: a server key must never ship in an app; use a client key (mf_client_…)")
        }
        guard c.sdkKey.hasPrefix("mf_client_") else {
            throw MegooFlagsConfigError(description: "megooflags: sdkKey must be a client key (mf_client_…)")
        }
        guard c.initTimeout > 0 else {
            throw MegooFlagsConfigError(description: "megooflags: initTimeout must be greater than 0")
        }
        sdkKey = c.sdkKey
        baseURL = try checkBaseURL(c.baseURL, offline: c.offline)
        bootstrap = try c.bootstrap.map { try parseResults(Data($0.utf8), prefix: "megooflags: invalid bootstrap") }
        disableCache = c.disableCache
        offline = c.offline
        initTimeout = c.initTimeout
        disableStream = c.disableStream
        pollInterval = max(c.pollInterval, Self.minPollInterval)
        flushInterval = min(max(c.flushInterval, Self.flushIntervals.lowerBound), Self.flushIntervals.upperBound)
        sendContext = c.sendContext
        logger = c.logger ?? OSLogLogger()
        sessionConfiguration = c.sessionConfiguration
    }
}

/// checkBaseURL requires an http or https URL with a host unless offline,
/// and removes trailing slashes.
private func checkBaseURL(_ url: URL?, offline: Bool) throws -> URL? {
    guard let url else {
        if offline { return nil }
        throw MegooFlagsConfigError(description: "megooflags: baseURL is required unless offline")
    }
    guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
        let host = url.host, !host.isEmpty
    else {
        throw MegooFlagsConfigError(description: "megooflags: baseURL must be an http or https URL")
    }
    var text = url.absoluteString
    while text.hasSuffix("/") {
        text.removeLast()
    }
    // Removing trailing slashes from a valid URL with a host keeps it valid.
    return URL(string: text)!
}
