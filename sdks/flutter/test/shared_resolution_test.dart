// The language-neutral resolution cases every client SDK must pass
// (spec/client/README.md). integer cases run through the int read, number
// cases through the double read.
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/resolve.dart';
import 'package:megooflags/src/store.dart' hide sameJson;
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'shared_context_test.dart' show clientDir, loadCaseFile, sameJson;

/// The spec's name for each OpenFeature error code.
const _errorCodeNames = {
  ErrorCode.providerNotReady: 'PROVIDER_NOT_READY',
  ErrorCode.flagNotFound: 'FLAG_NOT_FOUND',
  ErrorCode.parseError: 'PARSE_ERROR',
  ErrorCode.typeMismatch: 'TYPE_MISMATCH',
  ErrorCode.targetingKeyMissing: 'TARGETING_KEY_MISSING',
  ErrorCode.invalidContext: 'INVALID_CONTEXT',
  ErrorCode.providerFatal: 'PROVIDER_FATAL',
  ErrorCode.general: 'GENERAL',
};

/// resolveCase resolves one case's flag with its type and default.
ResolutionDetails<Object?> resolveCase(Map<String, Object?> c) {
  final flag = c['flag']! as String;
  final source = Source.values.byName(c['source']! as String);
  final results = c['results'];
  final store = results == null
      ? null
      : StoredResults.fromList(
          parseResults(
            jsonEncode({'results': results}),
            'megooflags: invalid results',
          ),
          etag: null,
          source: source,
          contextHash: '',
          targetingKey: null,
        );
  final def = c['default'];
  return switch (FlagKind.values.byName(c['type']! as String)) {
    FlagKind.boolean => resolve(store, flag, FlagKind.boolean, def! as bool),
    FlagKind.string => resolve(store, flag, FlagKind.string, def! as String),
    FlagKind.integer => resolve(store, flag, FlagKind.integer, def! as int),
    FlagKind.number => resolve(
      store,
      flag,
      FlagKind.number,
      (def! as num).toDouble(),
    ),
    FlagKind.object => resolve(
      store,
      flag,
      FlagKind.object,
      def! as Map<String, Object?>,
    ),
  };
}

void main() {
  group('shared resolution cases', () {
    final file = loadCaseFile('$clientDir/resolution.json');
    final tests = (file['tests']! as List).cast<Map<String, Object?>>();

    test('has cases of every type', () {
      expect(tests.map((c) => c['type']).toSet(), {
        'boolean',
        'string',
        'integer',
        'number',
        'object',
      });
    });

    for (final c in tests) {
      test('resolution::${c['name']}', () {
        final expected = c['expected']! as Map<String, Object?>;
        final got = resolveCase(c);
        expect(
          sameJson(got.value, expected['value']),
          isTrue,
          reason:
              'value ${jsonEncode(got.value)} != '
              '${jsonEncode(expected['value'])}',
        );
        expect(got.variant, expected['variant'], reason: 'variant');
        expect(got.reason, expected['reason'], reason: 'reason');
        expect(
          _errorCodeNames[got.errorCode],
          expected['error_code'],
          reason: 'error_code',
        );
      });
    }
  });

  test('the resolution runner refuses schema_version 2', () {
    final dir = Directory.systemTemp.createTempSync('megooflags-');
    addTearDown(() => dir.deleteSync(recursive: true));
    final path = '${dir.path}/resolution.json';
    File(path).writeAsStringSync(
      '{"schema_version": 2, "kind": "resolution", "tests": []}',
    );
    expect(() => loadCaseFile(path), throwsFormatException);
  });
}
