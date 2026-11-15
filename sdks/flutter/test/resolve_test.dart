import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/resolve.dart';
import 'package:megooflags/src/store.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

EvaluateResult served(Object? value, {String variant = 'v'}) => EvaluateResult(
  key: 'f',
  value: value,
  variant: variant,
  reason: 'TARGETING_MATCH',
);

StoredResults storeOf(List<EvaluateResult> results, {Source? source}) =>
    StoredResults.fromList(
      results,
      etag: null,
      source: source ?? Source.network,
      contextHash: 'h',
      targetingKey: null,
    );

/// expectError checks an error resolution: the default, no variant, reason
/// ERROR, [code] and [message].
void expectError<T>(
  ResolutionDetails<T> d,
  T defaultValue,
  ErrorCode code, [
  String? message,
]) {
  expect(d.value, defaultValue);
  expect(d.variant, isNull);
  expect(d.reason, 'ERROR');
  expect(d.errorCode, code);
  expect(d.errorMessage, message);
  expect(d.flagMetadata, isEmpty);
}

void main() {
  test('no stored results is PROVIDER_NOT_READY', () {
    expectError(
      resolve(null, 'f', FlagKind.boolean, true),
      true,
      ErrorCode.providerNotReady,
    );
  });

  test('a missing flag is FLAG_NOT_FOUND', () {
    expectError(
      resolve(storeOf([served(true)]), 'g', FlagKind.boolean, false),
      false,
      ErrorCode.flagNotFound,
    );
  });

  group('a stored error code', () {
    final codes = {
      'FLAG_NOT_FOUND': ErrorCode.flagNotFound,
      'PARSE_ERROR': ErrorCode.parseError,
      'TYPE_MISMATCH': ErrorCode.typeMismatch,
      'TARGETING_KEY_MISSING': ErrorCode.targetingKeyMissing,
      'INVALID_CONTEXT': ErrorCode.invalidContext,
      'GENERAL': ErrorCode.general,
      'SOMETHING_NEW': ErrorCode.general,
    };
    codes.forEach((stored, code) {
      test('$stored maps to $code with its message', () {
        final r = EvaluateResult(
          key: 'f',
          value: 'x',
          variant: 'v',
          reason: 'ERROR',
          errorCode: stored,
          errorMessage: 'why',
        );
        expectError(
          resolve(storeOf([r]), 'f', FlagKind.string, 'd'),
          'd',
          code,
          'why',
        );
      });
    });

    test('from the cache is still an error', () {
      final r = EvaluateResult(
        key: 'f',
        value: null,
        variant: null,
        reason: 'ERROR',
        errorCode: 'PARSE_ERROR',
      );
      expectError(
        resolve(storeOf([r], source: Source.cache), 'f', FlagKind.string, 'd'),
        'd',
        ErrorCode.parseError,
      );
    });
  });

  test('a null variant is the default with the stored reason', () {
    const r = EvaluateResult(
      key: 'f',
      value: null,
      variant: null,
      reason: 'DISABLED',
    );
    final d = resolve(storeOf([r]), 'f', FlagKind.string, 'd');
    expect(d.value, 'd');
    expect(d.variant, isNull);
    expect(d.reason, 'DISABLED');
    expect(d.errorCode, isNull);
    expect(d.errorMessage, isNull);
  });

  group('served values', () {
    test('keep the variant and the stored reason', () {
      final d = resolve(
        storeOf([served('blue', variant: 'b')]),
        'f',
        FlagKind.string,
        'red',
      );
      expect(d.value, 'blue');
      expect(d.variant, 'b');
      expect(d.reason, 'TARGETING_MATCH');
      expect(d.errorCode, isNull);
      expect(d.flagMetadata, isEmpty);
    });

    test('from the cache have reason CACHED', () {
      for (final source in Source.values) {
        final d = resolve(
          storeOf([served(true)], source: source),
          'f',
          FlagKind.boolean,
          false,
        );
        expect(
          d.reason,
          source == Source.cache ? 'CACHED' : 'TARGETING_MATCH',
          reason: '$source',
        );
      }
    });
  });

  group('boolean', () {
    test('reads a bool', () {
      expect(
        resolve(storeOf([served(false)]), 'f', FlagKind.boolean, true).value,
        false,
      );
    });
    for (final v in <Object?>['true', 1, null, <Object?>[]]) {
      test('refuses $v', () {
        expectError(
          resolve(storeOf([served(v)]), 'f', FlagKind.boolean, true),
          true,
          ErrorCode.typeMismatch,
        );
      });
    }
  });

  group('string', () {
    test('reads a string', () {
      expect(
        resolve(storeOf([served('')]), 'f', FlagKind.string, 'd').value,
        '',
      );
    });
    for (final v in <Object?>[5, true, null, <String, Object?>{}]) {
      test('refuses $v', () {
        expectError(
          resolve(storeOf([served(v)]), 'f', FlagKind.string, 'd'),
          'd',
          ErrorCode.typeMismatch,
        );
      });
    }
  });

  group('integer', () {
    int? read(Object? v, {bool web = false}) {
      final d = resolve(
        storeOf([served(v)]),
        'f',
        FlagKind.integer,
        7,
        web: web,
      );
      return d.errorCode == null ? d.value : null;
    }

    test('reads ints and integral doubles', () {
      expect(read(3), 3);
      expect(read(3.0), 3);
      expect(read(-2.0), -2);
      expect(read(9223372036854775807), 9223372036854775807);
      expect(read(-9223372036854775808.0), -9223372036854775808);
    });

    test('refuses fractions, values out of 64 bits and non-numbers', () {
      for (final v in <Object?>[
        1.5,
        1e20,
        9223372036854775808.0,
        double.nan,
        double.infinity,
        '3',
        true,
        null,
      ]) {
        expect(read(v), isNull, reason: '$v');
      }
    });

    test('on the web reads integers below 2^53 only', () {
      expect(read(9007199254740991, web: true), 9007199254740991);
      expect(read(-9007199254740991.0, web: true), -9007199254740991);
      expect(read(9007199254740992, web: true), isNull);
      expect(read(1e20, web: true), isNull);
      expect(read(1.5, web: true), isNull);
    });

    test('a mismatch returns the default', () {
      expectError(
        resolve(storeOf([served(1.5)]), 'f', FlagKind.integer, 7),
        7,
        ErrorCode.typeMismatch,
      );
    });
  });

  group('number', () {
    test('reads any number as a double', () {
      final d = resolve(storeOf([served(3)]), 'f', FlagKind.number, 0.5);
      expect(d.value, isA<double>());
      expect(d.value, 3.0);
      expect(
        resolve(storeOf([served(1e20)]), 'f', FlagKind.number, 0.5).value,
        1e20,
      );
    });
    for (final v in <Object?>[true, '1.5', null]) {
      test('refuses $v', () {
        expectError(
          resolve(storeOf([served(v)]), 'f', FlagKind.number, 0.5),
          0.5,
          ErrorCode.typeMismatch,
        );
      });
    }
  });

  group('object', () {
    test('reads an object', () {
      final value = <String, Object?>{
        'a': [1, 'b'],
        'c': null,
      };
      expect(
        resolve(
          storeOf([served(value)]),
          'f',
          FlagKind.object,
          <String, Object?>{},
        ).value,
        value,
      );
    });
    for (final v in <Object?>[
      [1, 2],
      'dark',
      1,
      null,
    ]) {
      test('refuses $v', () {
        const d = <String, Object?>{'theme': 'light'};
        expectError(
          resolve(storeOf([served(v)]), 'f', FlagKind.object, d),
          d,
          ErrorCode.typeMismatch,
        );
      });
    }
  });
}
