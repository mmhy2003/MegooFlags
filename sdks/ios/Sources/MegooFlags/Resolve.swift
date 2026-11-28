// Stored evaluate results mapped to OpenFeature resolution details
// (spec/client/README.md, resolution cases).
import OpenFeature

/// The OpenFeature read a value is resolved for.
enum FlagKind: Sendable {
    /// Bool.
    case boolean
    /// String.
    case string
    /// Int64: integral numbers within Int64's range.
    case integer
    /// Double: any number.
    case double
    /// Value: .structure from an object, .list from an array.
    case object
}

/// errorCode maps a server error code to OpenFeature's; unknown codes are
/// GENERAL.
private func errorCode(_ code: String) -> ErrorCode {
    switch code {
    case "FLAG_NOT_FOUND": return .flagNotFound
    case "PARSE_ERROR": return .parseError
    case "TYPE_MISMATCH": return .typeMismatch
    case "TARGETING_KEY_MISSING": return .targetingKeyMissing
    case "INVALID_CONTEXT": return .invalidContext
    default: return .general
    }
}

/// read converts value for kind, or nil when it has another type.
private func read(_ value: JSONValue, as kind: FlagKind) -> Any? {
    switch (kind, value) {
    case (.boolean, .bool(let b)): return b
    case (.string, .string(let s)): return s
    case (.integer, .int(let i)): return i
    // -2^63 is exact as a double; 2^63 is the first double above Int64.max.
    case (.integer, .double(let d)) where d.rounded(.towardZero) == d && d >= -0x1p63 && d < 0x1p63: return Int64(d)
    case (.double, .int(let i)): return Double(i)
    case (.double, .double(let d)): return d
    case (.object, .object), (.object, .array): return toValue(value)
    default: return nil
    }
}

private func toValue(_ v: JSONValue) -> Value {
    switch v {
    case .null: return .null
    case .bool(let b): return .boolean(b)
    case .int(let i): return .integer(i)
    case .double(let d): return .double(d)
    case .string(let s): return .string(s)
    case .array(let a): return .list(a.map(toValue))
    case .object(let o): return .structure(o.mapValues(toValue))
    }
}

private func failed<T>(_ defaultValue: T, _ code: ErrorCode, _ message: String? = nil) -> ProviderEvaluation<T> {
    ProviderEvaluation(value: defaultValue, reason: Reason.error.rawValue, errorCode: code, errorMessage: message)
}

/// resolve reads flag from the stored results as kind; T is the kind's
/// Swift type (Bool, String, Int64, Double, Value). Errors return the
/// default with reason ERROR; a null variant returns the default with the
/// stored reason; values from the cache have reason CACHED. No flag
/// metadata.
func resolve<T>(_ stored: StoredResults?, flag: String, as kind: FlagKind, default defaultValue: T)
    -> ProviderEvaluation<T>
{
    guard let stored else { return failed(defaultValue, .providerNotReady) }
    guard let r = stored.results[flag] else { return failed(defaultValue, .flagNotFound) }
    if let code = r.errorCode {
        return failed(defaultValue, errorCode(code), r.errorMessage)
    }
    guard let variant = r.variant else {
        return ProviderEvaluation(value: defaultValue, reason: r.reason)
    }
    guard let value = read(r.value, as: kind) as? T else { return failed(defaultValue, .typeMismatch) }
    return ProviderEvaluation(
        value: value, variant: variant, reason: stored.source == .cache ? Reason.cached.rawValue : r.reason)
}
