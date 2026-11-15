// The evaluate request context: the OpenFeature context mapped to JSON, its
// canonical form and the hash that keys the cache (spec/client/README.md).
import CryptoKit
import Foundation
import OpenFeature

/// toRequestContext copies every attribute as JSON. A non-empty targeting
/// key is written as key, replacing a key attribute; dates become UTC RFC
/// 3339 with milliseconds; integral doubles become integers.
func toRequestContext(_ ctx: EvaluationContext?) -> [String: JSONValue] {
    guard let ctx else { return [:] }
    var body = ctx.asMap().mapValues(toJSON)
    let targetingKey = ctx.getTargetingKey()
    if !targetingKey.isEmpty {
        body["key"] = .string(targetingKey)
    }
    return body
}

private func toJSON(_ v: Value) -> JSONValue {
    switch v {
    case .boolean(let b): return .bool(b)
    case .string(let s): return .string(s)
    case .integer(let i): return .int(i)
    case .double(let d): return exactInteger(d).map(JSONValue.int) ?? .double(d)
    case .date(let d): return .string(formatDate(d))
    case .list(let l): return .array(l.map(toJSON))
    case .structure(let s): return .object(s.mapValues(toJSON))
    case .null: return .null
    }
}

/// formatDate writes d as UTC RFC 3339 with exactly three fraction digits.
private func formatDate(_ d: Date) -> String {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = TimeZone(identifier: "UTC")
    f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
    return f.string(from: d)
}

/// contextHash is the lowercase hex SHA-256 of canonicalJSON(body).
func contextHash(_ body: [String: JSONValue]) -> String {
    SHA256.hash(data: Data(canonicalJSON(.object(body)).utf8))
        .map { String(format: "%02x", $0) }
        .joined()
}

/// targetingKey(of:) is body.key when it is a string, else nil.
func targetingKey(of body: [String: JSONValue]) -> String? {
    if case .string(let key)? = body["key"] {
        return key
    }
    return nil
}

/// exactInteger is d as an integer when it is integral and |d| < 2^53.
private func exactInteger(_ d: Double) -> Int64? {
    d.rounded(.towardZero) == d && abs(d) < 9_007_199_254_740_992 ? Int64(d) : nil
}

/// canonicalJSON writes value without whitespace, object keys sorted by
/// Unicode code point, strings escaping only `"`, `\` and control
/// characters, and integral numbers (|x| < 2^53) as integers. Other doubles
/// use Swift's shortest round-trip form; NaN and infinities are null.
func canonicalJSON(_ value: JSONValue) -> String {
    var out = ""
    write(value, to: &out)
    return out
}

private func write(_ value: JSONValue, to out: inout String) {
    switch value {
    case .null:
        out += "null"
    case .bool(let b):
        out += b ? "true" : "false"
    case .int(let i):
        out += String(i)
    case .double(let d):
        if !d.isFinite {
            out += "null"
        } else if let i = exactInteger(d) {
            out += String(i)
        } else {
            out += "\(d)"
        }
    case .string(let s):
        writeString(s, to: &out)
    case .array(let a):
        out += "["
        for (i, v) in a.enumerated() {
            if i > 0 { out += "," }
            write(v, to: &out)
        }
        out += "]"
    case .object(let o):
        out += "{"
        for (i, k) in o.keys.sorted(by: codePointLess).enumerated() {
            if i > 0 { out += "," }
            writeString(k, to: &out)
            out += ":"
            write(o[k]!, to: &out)
        }
        out += "}"
    }
}

/// codePointLess orders strings by Unicode scalar value; String's own `<`
/// does not.
private func codePointLess(_ a: String, _ b: String) -> Bool {
    a.unicodeScalars.lexicographicallyPrecedes(b.unicodeScalars) { $0.value < $1.value }
}

private func writeString(_ s: String, to out: inout String) {
    out += "\""
    for u in s.unicodeScalars {
        switch u {
        case "\"": out += "\\\""
        case "\\": out += "\\\\"
        case "\u{8}": out += "\\b"
        case "\u{c}": out += "\\f"
        case "\n": out += "\\n"
        case "\r": out += "\\r"
        case "\t": out += "\\t"
        case "\u{0}"..."\u{1f}":
            let hex = String(u.value, radix: 16)
            out += "\\u" + String(repeating: "0", count: 4 - hex.count) + hex
        default: out.unicodeScalars.append(u)
        }
    }
    out += "\""
}
