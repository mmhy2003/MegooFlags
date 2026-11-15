// The evaluated results the provider reads from, their parsing, and what
// changed between two of them. The store holds one StoredResults or none and
// swaps it with one assignment.
import 'dart:convert';

/// One evaluated flag, as POST /sdk/v1/evaluate returns it; [value] is a
/// decoded JSON value.
class EvaluateResult {
  const EvaluateResult({
    required this.key,
    required this.value,
    required this.variant,
    required this.reason,
    this.errorCode,
    this.errorMessage,
  });

  final String key;
  final Object? value;
  final String? variant;
  final String reason;
  final String? errorCode;
  final String? errorMessage;
}

/// parseResults reads an evaluate response body (`{"results": [...]}`) and
/// returns its results. It throws a FormatException whose message is
/// `<prefix>: <path>: <why>`; extra members are dropped, a missing error_message is null and duplicate
/// keys are refused.
List<EvaluateResult> parseResults(String text, String prefix) {
  Never fail(String why) => throw FormatException('$prefix: $why');
  final Object? body;
  try {
    body = jsonDecode(text);
  } on FormatException {
    fail('not valid JSON');
  }
  if (body is! Map<String, Object?>) fail('not an object');
  if (!body.containsKey('results')) fail('results: missing');
  final items = body['results'];
  if (items is! List<Object?>) fail('results: not an array');
  final seen = <String>{};
  return [
    for (final (i, item) in items.indexed)
      _parseResult(item, 'results[$i]', seen, fail),
  ];
}

EvaluateResult _parseResult(
  Object? item,
  String path,
  Set<String> seen,
  Never Function(String why) fail,
) {
  if (item is! Map<String, Object?>) fail('$path: not an object');
  Object? member(String name) {
    if (!item.containsKey(name)) fail('$path.$name: missing');
    return item[name];
  }

  String? nullableString(String name, Object? v) => v == null || v is String
      ? v as String?
      : fail('$path.$name: not a string or null');

  final key = member('key');
  if (key is! String) fail('$path.key: not a string');
  final value = member('value');
  final variant = nullableString('variant', member('variant'));
  final reason = member('reason');
  if (reason is! String) fail('$path.reason: not a string');
  final errorCode = nullableString('error_code', member('error_code'));
  final errorMessage = nullableString('error_message', item['error_message']);
  if (!seen.add(key)) fail('$path.key: duplicate key');
  return EvaluateResult(
    key: key,
    value: value,
    variant: variant,
    reason: reason,
    errorCode: errorCode,
    errorMessage: errorMessage,
  );
}

/// Where the stored results came from.
enum Source { bootstrap, cache, network }

class StoredResults {
  const StoredResults({
    required this.results,
    required this.etag,
    required this.source,
    required this.contextHash,
    required this.targetingKey,
  });

  /// Keys [results] by flag key; keys are unique (parseResults refuses
  /// duplicates), a later one would win.
  StoredResults.fromList(
    List<EvaluateResult> results, {
    required String? etag,
    required Source source,
    required String contextHash,
    required String? targetingKey,
  }) : this(
         results: {for (final r in results) r.key: r},
         etag: etag,
         source: source,
         contextHash: contextHash,
         targetingKey: targetingKey,
       );

  final Map<String, EvaluateResult> results;
  final String? etag;
  final Source source;
  final String contextHash;
  final String? targetingKey;
}

/// Store holds the current results; replacing [current] is the swap (Dart
/// runs one isolate's code on one thread, so readers see the old or the new
/// results, never a mix).
class Store {
  StoredResults? current;
}

/// changedFlags lists, sorted, the keys whose value (JSON equality) or
/// variant differs, plus the keys present in only one of [old] and [next].
List<String> changedFlags(StoredResults old, StoredResults next) {
  bool same(EvaluateResult a, EvaluateResult? b) =>
      b != null && a.variant == b.variant && sameJson(a.value, b.value);
  return [
    for (final MapEntry(:key, value: a) in old.results.entries)
      if (!same(a, next.results[key])) key,
    for (final key in next.results.keys)
      if (!old.results.containsKey(key)) key,
  ]..sort();
}

/// sameJson is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays
/// element by element, objects key by key, {} is not [].
bool sameJson(Object? a, Object? b) {
  if (a == null || b == null) return a == b;
  if (a is num && b is num) return a == b;
  if (a is List || b is List) {
    if (a is! List || b is! List || a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (!sameJson(a[i], b[i])) return false;
    }
    return true;
  }
  if (a is Map || b is Map) {
    if (a is! Map || b is! Map || a.length != b.length) return false;
    return a.keys.every((k) => b.containsKey(k) && sameJson(a[k], b[k]));
  }
  return a.runtimeType == b.runtimeType && a == b;
}
