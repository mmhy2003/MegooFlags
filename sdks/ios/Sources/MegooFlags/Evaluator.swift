// Remote evaluation: posts the request context to /sdk/v1/evaluate and
// returns the evaluated results, or notModified when the ETag still matches.
import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

enum EvaluateOutcome: Equatable, Sendable {
    case ok([EvaluateResult], etag: String?)
    case notModified(etag: String?)
}

struct Evaluator: Sendable {
    let baseURL: URL
    let sdkKey: String
    let transport: any HTTPTransport

    /// evaluate sends body as the context, with If-None-Match when etag is
    /// set. It throws the statusError of an unexpected status,
    /// SDKError.invalidResponse for an unreadable 200 body, and the
    /// transport's error on a network failure or a cancellation.
    func evaluate(body: [String: JSONValue], etag: String?) async throws -> EvaluateOutcome {
        var request = makeRequest(
            baseURL: baseURL, path: "evaluate", sdkKey: sdkKey, method: "POST", accept: "application/json",
            etag: etag, body: Data(canonicalJSON(.object(["context": .object(body)])).utf8))
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, response) = try await transport.data(for: request)
        let header = { (name: String) in response.value(forHTTPHeaderField: name) }
        if let error = statusError(status: response.statusCode, header: header) {
            throw error
        }
        if response.statusCode == 304 {
            return .notModified(etag: header("ETag"))
        }
        do {
            return .ok(try parseResults(data, prefix: "evaluate"), etag: header("ETag"))
        } catch let error as MegooFlagsConfigError {
            throw SDKError.invalidResponse(error.description)
        }
    }
}
