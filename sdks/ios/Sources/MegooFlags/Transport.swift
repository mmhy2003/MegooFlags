// HTTP helpers shared by the evaluate, stream and events requests: request
// building, status mapping and Retry-After parsing. The SDK key travels in
// the Authorization header only, and no message ever contains it.
import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// The ways an SDK request fails.
enum SDKError: Error, Equatable, Sendable, CustomStringConvertible, LocalizedError {
    /// The server refused the SDK key (401 or 403); retrying cannot succeed.
    case fatal(Int?)
    /// The stream's revoked event: the key was revoked.
    case revoked
    /// A 429; the wait, in seconds, the server asked for.
    case retryAfter(TimeInterval)
    /// Any other unexpected status.
    case http(Int)
    /// A response the SDK could not read.
    case invalidResponse(String)

    var description: String {
        switch self {
        case .fatal(let status?): return "megooflags: SDK key refused (\(status))"
        case .fatal(nil): return "megooflags: SDK key refused"
        case .revoked: return "megooflags: SDK key refused (revoked)"
        case .retryAfter(let seconds): return "megooflags: rate limited, retry after \(Int(seconds))s"
        case .http(let status): return "megooflags: HTTP \(status)"
        case .invalidResponse(let why): return "megooflags: invalid response: \(why)"
        }
    }

    var errorDescription: String? { description }
}

/// makeRequest builds a request to `<baseURL>/sdk/v1/<path>` (trailing
/// slashes of baseURL removed) with the SDK headers: the key as a bearer
/// token, Accept, User-Agent, and Content-Type when there is a body,
/// If-None-Match and Last-Event-ID when given.
func makeRequest(
    baseURL: URL, path: String, sdkKey: String, method: String, accept: String, etag: String? = nil,
    lastEventID: String? = nil, body: Data? = nil
) -> URLRequest {
    var base = baseURL.absoluteString
    while base.hasSuffix("/") {
        base.removeLast()
    }
    // A valid URL followed by "/sdk/v1/" and an ASCII path is a valid URL.
    var r = URLRequest(url: URL(string: "\(base)/sdk/v1/\(path)")!)
    r.httpMethod = method
    r.setValue("Bearer \(sdkKey)", forHTTPHeaderField: "Authorization")
    r.setValue(accept, forHTTPHeaderField: "Accept")
    r.setValue("megooflags-ios/\(megooFlagsVersion)", forHTTPHeaderField: "User-Agent")
    if let body {
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
    }
    if let etag {
        r.setValue(etag, forHTTPHeaderField: "If-None-Match")
    }
    if let lastEventID {
        r.setValue(lastEventID, forHTTPHeaderField: "Last-Event-ID")
    }
    return r
}

/// statusError maps a response status to an error: nil for 2xx and 304.
/// header reads a response header; now is the current time, for an
/// HTTP-date Retry-After.
func statusError(status: Int, header: (String) -> String?, now: Date = Date()) -> SDKError? {
    switch status {
    case 200..<300, 304: return nil
    case 401, 403: return .fatal(status)
    case 429: return .retryAfter(parseRetryAfter(header("Retry-After"), now: now))
    default: return .http(status)
    }
}

/// The wait, in seconds, when Retry-After is missing or invalid.
private let defaultRetryAfter: TimeInterval = 60

/// parseRetryAfter reads a Retry-After value as delta-seconds or an
/// HTTP-date and returns whole seconds (a partial second rounds up). A
/// negative number or a date in the past is 0; a missing or invalid value
/// is 60.
func parseRetryAfter(_ value: String?, now: Date) -> TimeInterval {
    let v = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    if match(#"^[+-]?[0-9]+$"#, v) != nil, let seconds = Double(v) {
        return max(seconds, 0)
    }
    guard let at = parseHTTPDate(v) else {
        return defaultRetryAfter
    }
    return max((at.timeIntervalSince(now)).rounded(.up), 0)
}

private let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

// The three HTTP-date forms of RFC 9110 §5.6.7.
private let imfFixdate = #"^[A-Z][a-z]{2}, ([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$"#
private let rfc850 = #"^[A-Z][a-z]+, ([0-9]{2})-([A-Z][a-z]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$"#
private let asctime = #"^[A-Z][a-z]{2} ([A-Z][a-z]{2}) ([ 0-9][0-9]) ([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})$"#

private func parseHTTPDate(_ v: String) -> Date? {
    if let m = match(imfFixdate, v) {
        return utc(year: m[2], month: m[1], day: m[0], hour: m[3], minute: m[4], second: m[5])
    }
    if let m = match(rfc850, v) {
        // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx.
        let yy = Int(m[2]) ?? 0
        return utc(
            year: String(yy >= 69 ? 1900 + yy : 2000 + yy), month: m[1], day: m[0], hour: m[3], minute: m[4],
            second: m[5])
    }
    if let m = match(asctime, v) {
        return utc(year: m[5], month: m[0], day: m[1], hour: m[2], minute: m[3], second: m[4])
    }
    return nil
}

/// match returns the capture groups of pattern in s, or nil without a match.
private func match(_ pattern: String, _ s: String) -> [String]? {
    // The patterns are constants that compile.
    let re = try! NSRegularExpression(pattern: pattern)
    guard let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else {
        return nil
    }
    return (1..<m.numberOfRanges).map { i in
        Range(m.range(at: i), in: s).map { String(s[$0]) } ?? ""
    }
}

private func utc(year: String, month: String, day: String, hour: String, minute: String, second: String) -> Date? {
    let trim = { (s: String) in Int(s.trimmingCharacters(in: .whitespaces)) }
    guard let mon = months.firstIndex(of: month), let y = trim(year), let d = trim(day), let h = trim(hour),
        let mi = trim(minute), let s = trim(second), h <= 23, mi <= 59, s <= 59
    else {
        return nil
    }
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "UTC")!
    let parts = DateComponents(year: y, month: mon + 1, day: d, hour: h, minute: mi, second: s)
    guard let date = cal.date(from: parts) else {
        return nil
    }
    // A day the month does not have rolls over; refuse it.
    let back = cal.dateComponents([.month, .day], from: date)
    return back.month == mon + 1 && back.day == d ? date : nil
}
