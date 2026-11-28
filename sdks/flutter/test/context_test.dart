import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/context.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

void main() {
  group('toRequestContext', () {
    test('writes a non-empty targeting key as key', () {
      final ctx = EvaluationContext(
        targetingKey: 'u1',
        attributes: {'plan': 'pro'},
      );
      expect(toRequestContext(ctx), {'key': 'u1', 'plan': 'pro'});
    });

    test('replaces a key attribute with the targeting key', () {
      final ctx = EvaluationContext(
        targetingKey: 'u1',
        attributes: {'key': 'other'},
      );
      expect(toRequestContext(ctx), {'key': 'u1'});
    });

    test('keeps a key attribute without a targeting key', () {
      expect(toRequestContext(EvaluationContext(attributes: {'key': 'u2'})), {
        'key': 'u2',
      });
    });

    test('drops an empty targeting key', () {
      final ctx = EvaluationContext(
        targetingKey: '',
        attributes: {'plan': 'free'},
      );
      expect(toRequestContext(ctx), {'plan': 'free'});
    });

    test('maps the empty context to an empty body', () {
      expect(toRequestContext(EvaluationContext.empty), isEmpty);
    });

    test('writes dates as UTC with milliseconds', () {
      final ctx = EvaluationContext(
        attributes: {
          'at': DateTime.parse('2026-11-10T11:15:02+02:00'),
          'local': DateTime.utc(2026, 11, 10, 9, 15, 2, 5, 999).toLocal(),
          'nested': {
            'list': [DateTime.parse('2026-11-10T09:15:02.5Z')],
          },
          'early': DateTime.utc(7, 1, 2),
        },
      );
      expect(toRequestContext(ctx), {
        'at': '2026-11-10T09:15:02.000Z',
        'local': '2026-11-10T09:15:02.005Z',
        'nested': {
          'list': ['2026-11-10T09:15:02.500Z'],
        },
        'early': '0007-01-02T00:00:00.000Z',
      });
    });

    test('turns integral doubles into integers', () {
      final body = toRequestContext(
        EvaluationContext(
          attributes: {
            'whole': 3.0,
            'neg': -7.0,
            'ratio': 1.5,
            'big': 1e20,
            'list': [2.0],
          },
        ),
      );
      expect(body['whole'], isA<int>());
      expect(body['whole'], 3);
      expect(body['neg'], -7);
      expect(body['ratio'], 1.5);
      // Out of the exact-integer range: kept as the number it is.
      expect(body['big'], 1e20);
      expect((body['list'] as List).single, isA<int>());
    });

    test('copies nested structures', () {
      final body = toRequestContext(
        EvaluationContext(
          attributes: {
            'device': {
              'os': 'ios',
              'tags': [
                'beta',
                {'level': 2},
              ],
            },
            'team': null,
          },
        ),
      );
      expect(body, {
        'device': {
          'os': 'ios',
          'tags': [
            'beta',
            {'level': 2},
          ],
        },
        'team': null,
      });
    });
  });

  group('canonicalJson', () {
    test('sorts keys by code point, not UTF-16 unit', () {
      expect(
        canonicalJson({'😀': 1, '～': 2, 'é': 3, 'Z': 4, 'a': 5}),
        '{"Z":4,"a":5,"é":3,"～":2,"😀":1}',
      );
    });

    test('a key that is a prefix of another sorts first', () {
      expect(canonicalJson({'ab': 1, 'a': 2}), '{"a":2,"ab":1}');
    });

    test('sorts nested keys and has no whitespace', () {
      expect(
        canonicalJson({
          'b': [
            {'y': 1, 'x': 2},
            <Object?>[],
          ],
          'a': <String, Object?>{},
        }),
        '{"a":{},"b":[{"x":2,"y":1},[]]}',
      );
    });

    test('escapes only quote, backslash and control characters', () {
      expect(
        canonicalJson(
          'q " b \\ n \n t \t \b\f\r \u0001 \u001f \u007f café 😀  ',
        ),
        '"q \\" b \\\\ n \\n t \\t \\b\\f\\r \\u0001 \\u001f \u007f café 😀  "',
      );
    });

    test('writes integral numbers as integers', () {
      expect(
        canonicalJson([3.0, -7, 0, -0.0, 1.5, 1e21, 4503599627370496.0]),
        '[3,-7,0,0,1.5,1e+21,4503599627370496]',
      );
    });

    test('writes booleans and null', () {
      expect(canonicalJson([true, false, null]), '[true,false,null]');
    });

    test('refuses values JSON cannot hold', () {
      expect(() => canonicalJson(double.nan), throwsArgumentError);
      expect(() => canonicalJson(double.infinity), throwsArgumentError);
      expect(() => canonicalJson(DateTime(2026)), throwsArgumentError);
      expect(() => canonicalJson({1: 'a'}), throwsArgumentError);
    });
  });

  group('contextHash', () {
    test('is the SHA-256 of the canonical JSON', () {
      expect(
        contextHash({'plan': 'pro', 'key': 'user-1'}),
        'e38a15b496693e013a31fba1d1c8804c3c06f1d0686e0152549c8c05609babbe',
      );
    });
  });

  group('targetingKeyOf', () {
    test('is key when it is a string', () {
      expect(targetingKeyOf({'key': 'u1'}), 'u1');
    });

    for (final body in [
      <String, Object?>{},
      {'key': 1},
      {'key': null},
    ]) {
      test('is null for $body', () {
        expect(targetingKeyOf(body), isNull);
      });
    }
  });
}
