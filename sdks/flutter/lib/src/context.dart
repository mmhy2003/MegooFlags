// The evaluate request context: the OpenFeature context mapped to JSON, its
// canonical form and the hash that keys the cache (spec/client/README.md).
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'sha256.dart';

/// 2^53: below it every integer is exact as a double, on the VM and the web.
const _exactIntegerLimit = 9007199254740992.0;

/// toRequestContext copies every attribute as JSON. A non-empty targeting
/// key is written as key, replacing a key attribute; dates become UTC RFC
/// 3339 with milliseconds; integral doubles become integers.
Map<String, Object?> toRequestContext(EvaluationContext ctx) {
  final body = <String, Object?>{
    for (final e in ctx.attributes.entries) e.key: _toJson(e.value),
  };
  final targetingKey = ctx.targetingKey;
  if (targetingKey != null && targetingKey.isNotEmpty) {
    body['key'] = targetingKey;
  }
  return body;
}

Object? _toJson(Object? v) => switch (v) {
  DateTime() => _formatUtcMillis(v),
  double() when _isExactInteger(v) => v.toInt(),
  List() => [for (final x in v) _toJson(x)],
  Map() => {for (final e in v.entries) e.key as String: _toJson(e.value)},
  _ => v,
};

bool _isExactInteger(double v) =>
    v.isFinite && v == v.truncateToDouble() && v.abs() < _exactIntegerLimit;

/// _formatUtcMillis writes [t] as yyyy-MM-ddTHH:mm:ss.SSSZ in UTC
/// (toIso8601String would add microseconds when it has them).
String _formatUtcMillis(DateTime t) {
  final u = t.toUtc();
  String pad(int n, int width) => n.toString().padLeft(width, '0');
  return '${pad(u.year, 4)}-${pad(u.month, 2)}-${pad(u.day, 2)}'
      'T${pad(u.hour, 2)}:${pad(u.minute, 2)}:${pad(u.second, 2)}'
      '.${pad(u.millisecond, 3)}Z';
}

/// canonicalJson writes [v] without whitespace, object keys sorted by code
/// point and integral numbers (|x| < 2^53) as integers. Strings escape only
/// `"`, `\` and U+0000 to U+001F. A value JSON cannot hold (NaN, infinity,
/// a non-string map key, any other type) throws ArgumentError.
String canonicalJson(Object? v) {
  final out = StringBuffer();
  _write(out, v);
  return out.toString();
}

void _write(StringBuffer out, Object? v) {
  switch (v) {
    case null:
      out.write('null');
    case bool():
      out.write(v ? 'true' : 'false');
    case String():
      _writeString(out, v);
    case int():
      // On the web an int is a JavaScript number: toString matches JSON.
      out.write(v.toString());
    case double():
      if (!v.isFinite) {
        throw ArgumentError.value(v, 'value', 'not a JSON number');
      }
      out.write(_isExactInteger(v) ? v.toInt().toString() : v.toString());
    case List():
      out.write('[');
      for (var i = 0; i < v.length; i++) {
        if (i > 0) out.write(',');
        _write(out, v[i]);
      }
      out.write(']');
    case Map():
      final keys = [
        for (final k in v.keys)
          k is String ? k : throw ArgumentError.value(k, 'key', 'not a string'),
      ]..sort(_compareCodePoints);
      out.write('{');
      for (var i = 0; i < keys.length; i++) {
        if (i > 0) out.write(',');
        _writeString(out, keys[i]);
        out.write(':');
        _write(out, v[keys[i]]);
      }
      out.write('}');
    default:
      throw ArgumentError.value(
        v.runtimeType,
        'value',
        'not a JSON value type',
      );
  }
}

const _shortEscapes = {
  0x22: r'\"',
  0x5c: r'\\',
  0x08: r'\b',
  0x0c: r'\f',
  0x0a: r'\n',
  0x0d: r'\r',
  0x09: r'\t',
};

void _writeString(StringBuffer out, String s) {
  out.write('"');
  var from = 0;
  for (var i = 0; i < s.length; i++) {
    final c = s.codeUnitAt(i);
    if (c >= 0x20 && c != 0x22 && c != 0x5c) continue;
    out.write(s.substring(from, i));
    out.write(_shortEscapes[c] ?? '\\u${c.toRadixString(16).padLeft(4, '0')}');
    from = i + 1;
  }
  out.write(s.substring(from));
  out.write('"');
}

/// _compareCodePoints orders strings by Unicode code point, not UTF-16 unit.
int _compareCodePoints(String a, String b) {
  final ia = a.runes.iterator;
  final ib = b.runes.iterator;
  while (true) {
    final hasA = ia.moveNext();
    final hasB = ib.moveNext();
    if (!hasA || !hasB) return (hasA ? 1 : 0) - (hasB ? 1 : 0);
    final d = ia.current - ib.current;
    if (d != 0) return d;
  }
}

/// contextHash is the lowercase hex SHA-256 of canonicalJson(body).
String contextHash(Map<String, Object?> body) => sha256Hex(canonicalJson(body));

/// targetingKeyOf is body.key when it is a string, else null.
String? targetingKeyOf(Map<String, Object?> body) {
  final key = body['key'];
  return key is String ? key : null;
}
