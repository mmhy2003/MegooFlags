// The language-neutral context cases every client SDK must pass
// (spec/client/README.md).
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/context.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

const clientDir = '../../spec/client';

/// loadCaseFile refuses any schema_version other than 1.
Map<String, Object?> loadCaseFile(String path) {
  final f = jsonDecode(File(path).readAsStringSync()) as Map<String, Object?>;
  if (f['schema_version'] != 1) {
    throw FormatException(
      '${path.split('/').last}: unsupported schema_version '
      '${jsonEncode(f['schema_version'])}',
    );
  }
  return f;
}

/// fromCaseJson turns every {"$date": s} into DateTime.parse(s).
Object? fromCaseJson(Object? value) {
  if (value is List) return value.map(fromCaseJson).toList();
  if (value is Map<String, Object?>) {
    if (value.length == 1 && value.containsKey(r'$date')) {
      return DateTime.parse(value[r'$date']! as String);
    }
    return value.map((k, v) => MapEntry(k, fromCaseJson(v)));
  }
  return value;
}

/// contextOf builds an EvaluationContext: targetingKey is the targeting key,
/// every other member an attribute.
EvaluationContext contextOf(Map<String, Object?> json) {
  final attributes = Map.of(fromCaseJson(json)! as Map<String, Object?>);
  final targetingKey = attributes.remove('targetingKey') as String?;
  return EvaluationContext(targetingKey: targetingKey, attributes: attributes);
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

void main() {
  group('shared context cases', () {
    final file = loadCaseFile('$clientDir/context.json');
    final tests = (file['tests']! as List).cast<Map<String, Object?>>();

    test('has cases', () {
      expect(tests, isNotEmpty);
    });

    for (final c in tests) {
      test('context::${c['name']}', () {
        final body = toRequestContext(
          contextOf(c['context']! as Map<String, Object?>),
        );
        expect(
          sameJson(body, c['body']),
          isTrue,
          reason: 'body ${jsonEncode(body)} != ${jsonEncode(c['body'])}',
        );
        expect(canonicalJson(body), c['canonical']);
        expect(contextHash(body), c['context_hash']);
      });
    }
  });

  group('shared context runner', () {
    test('refuses a case file with schema_version 2', () {
      final dir = Directory.systemTemp.createTempSync('megooflags-');
      addTearDown(() => dir.deleteSync(recursive: true));
      final path = '${dir.path}/case.json';
      File(path).writeAsStringSync(
        '{"schema_version": 2, "kind": "context", "tests": []}',
      );
      expect(
        () => loadCaseFile(path),
        throwsA(
          isA<FormatException>().having(
            (e) => e.message,
            'message',
            contains('unsupported schema_version 2'),
          ),
        ),
      );
    });

    test(r'builds dates from $date and splits off the targeting key', () {
      final ctx = contextOf({
        'targetingKey': 'u1',
        'a': [
          {r'$date': '2026-11-10T09:15:02Z'},
        ],
      });
      expect(ctx.targetingKey, 'u1');
      expect(ctx.attributes, {
        'a': [DateTime.utc(2026, 11, 10, 9, 15, 2)],
      });
    });

    final pairs = <(Object?, Object?, bool)>[
      (1, 1.0, true),
      (true, 1, false),
      ('1', 1, false),
      (<String, Object?>{}, <Object?>[], false),
      ({'a': 1}, {'a': 1, 'b': 2}, false),
      ({'a': 1}, {'b': 1}, false),
      ([1, 2], [1, 2], true),
    ];
    for (final (a, b, want) in pairs) {
      test('sameJson($a, $b) is $want', () {
        expect(sameJson(a, b), want);
      });
    }
  });
}
