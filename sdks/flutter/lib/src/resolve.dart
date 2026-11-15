// Stored evaluate results mapped to OpenFeature resolution details
// (spec/client/README.md, resolution cases).
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'store.dart';

/// The type a flag is read as; the names are the spec's.
enum FlagKind { boolean, string, integer, number, object }

/// 2^63: the first double above the largest 64-bit int; -2^63 is exact.
const _int64Limit = 9223372036854775808.0;

/// 2^53: on the web an int is a JavaScript number, exact only below it.
const _webIntLimit = 9007199254740992.0;

/// errorCodeOf maps a server error code to OpenFeature's; unknown codes are
/// general.
ErrorCode _errorCodeOf(String code) => switch (code) {
  'FLAG_NOT_FOUND' => ErrorCode.flagNotFound,
  'PARSE_ERROR' => ErrorCode.parseError,
  'TYPE_MISMATCH' => ErrorCode.typeMismatch,
  'TARGETING_KEY_MISSING' => ErrorCode.targetingKeyMissing,
  'INVALID_CONTEXT' => ErrorCode.invalidContext,
  _ => ErrorCode.general,
};

ResolutionDetails<T> _failed<T>(T defaultValue, ErrorCode code, [String? m]) =>
    ResolutionDetails(
      value: defaultValue,
      reason: 'ERROR',
      errorCode: code,
      errorMessage: m,
    );

/// resolve reads [flag] from [stored] as [kind]; [T] is the kind's Dart type
/// (bool, String, int, double, `Map<String, Object?>`). Errors return
/// [defaultValue] with reason ERROR; a null variant returns it with the
/// stored reason; values from the cache have reason CACHED. No flag metadata.
/// Integer reads accept integral numbers within 64 bits, or below 2^53 when
/// [web]; number reads any number; object reads objects only (never arrays).
ResolutionDetails<T> resolve<T>(
  StoredResults? stored,
  String flag,
  FlagKind kind,
  T defaultValue, {
  bool web = kIsWeb,
}) {
  if (stored == null) return _failed(defaultValue, ErrorCode.providerNotReady);
  final r = stored.results[flag];
  if (r == null) return _failed(defaultValue, ErrorCode.flagNotFound);
  final code = r.errorCode;
  if (code != null) {
    return _failed(defaultValue, _errorCodeOf(code), r.errorMessage);
  }
  final variant = r.variant;
  if (variant == null) {
    return ResolutionDetails(value: defaultValue, reason: r.reason);
  }
  final v = r.value;
  final Object? value = switch (kind) {
    FlagKind.boolean => v is bool ? v : null,
    FlagKind.string => v is String ? v : null,
    FlagKind.integer => _integral(v, web),
    FlagKind.number => v is num ? v.toDouble() : null,
    FlagKind.object => v is Map<String, Object?> ? v : null,
  };
  if (value == null) return _failed(defaultValue, ErrorCode.typeMismatch);
  return ResolutionDetails(
    value: value as T,
    variant: variant,
    reason: stored.source == Source.cache ? 'CACHED' : r.reason,
  );
}

/// _integral is [v] as an int when it is a whole number within the platform's
/// int range, else null. On the web every integral double is an int, so the
/// range is checked on the double.
int? _integral(Object? v, bool web) {
  if (v is! num) return null;
  if (!web && v is int) return v;
  final d = v.toDouble();
  if (!d.isFinite || d != d.truncateToDouble()) return null;
  final inRange = web
      ? d.abs() < _webIntLimit
      : d >= -_int64Limit && d < _int64Limit;
  return inRange ? d.toInt() : null;
}
