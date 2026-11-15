import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:megooflags/megooflags.dart';
import 'package:megooflags/src/config.dart' show defaultLogger;
import 'package:megooflags/src/store.dart';

const key = 'mf_client_secret123';
const base = 'https://flags.example.com';

/// throwsConfigError expects an ArgumentError whose message is [message] and
/// never contains the key.
Matcher throwsConfigError(String message) => throwsA(
  isA<ArgumentError>()
      .having((e) => e.message, 'message', message)
      .having((e) => e.toString(), 'toString', isNot(contains('secret123'))),
);

void main() {
  group('sdkKey', () {
    test('refuses a server key', () {
      expect(
        () => MegooFlagsConfig(sdkKey: 'mf_server_secret123', baseUrl: base),
        throwsConfigError(
          'megooflags: a server key must never ship in an app; use a client '
          'key (mf_client_…)',
        ),
      );
    });

    for (final bad in [
      '',
      'secret123',
      'mf_secret123',
      'MF_CLIENT_secret123',
    ]) {
      test('refuses "$bad"', () {
        expect(
          () => MegooFlagsConfig(sdkKey: bad, baseUrl: base),
          throwsConfigError(
            'megooflags: sdkKey must be a client key (mf_client_…)',
          ),
        );
      });
    }

    test('accepts a client key', () {
      expect(MegooFlagsConfig(sdkKey: key, baseUrl: base).sdkKey, key);
    });
  });

  group('baseUrl', () {
    test('is required unless offline', () {
      expect(
        () => MegooFlagsConfig(sdkKey: key),
        throwsConfigError('megooflags: baseUrl is required unless offline'),
      );
      expect(MegooFlagsConfig(sdkKey: key, offline: true).baseUrl, isNull);
    });

    for (final bad in [
      'ftp://flags.example.com',
      'flags.example.com',
      'https://',
      'http:///path',
      '',
      'not a url at all',
    ]) {
      test('refuses "$bad"', () {
        expect(
          () => MegooFlagsConfig(sdkKey: key, baseUrl: bad),
          throwsConfigError('megooflags: baseUrl must be an http or https URL'),
        );
      });
    }

    test('removes trailing slashes', () {
      expect(
        MegooFlagsConfig(
          sdkKey: key,
          baseUrl: 'http://localhost:8000//',
        ).baseUrl,
        'http://localhost:8000',
      );
      expect(
        MegooFlagsConfig(
          sdkKey: key,
          baseUrl: 'HTTPS://h.example/api/',
        ).baseUrl,
        'HTTPS://h.example/api',
      );
    });

    test('is kept when offline', () {
      expect(
        MegooFlagsConfig(sdkKey: key, baseUrl: '$base/', offline: true).baseUrl,
        base,
      );
    });
  });

  group('defaults', () {
    test('match B4', () {
      final c = MegooFlagsConfig(sdkKey: key, baseUrl: base);
      expect(c.bootstrap, isNull);
      expect(c.disableCache, isFalse);
      expect(c.offline, isFalse);
      expect(c.initTimeout, const Duration(seconds: 5));
      expect(c.disableStream, isFalse);
      expect(c.pollInterval, const Duration(seconds: 30));
      expect(c.flushInterval, const Duration(seconds: 30));
      expect(c.sendContext, isFalse);
      expect(c.logger, same(defaultLogger));
      expect(c.httpClient, isNull);
    });

    test('keeps the given options', () {
      final client = http.Client();
      addTearDown(client.close);
      void logger(MegooFlagsLogLevel level, String message) {}
      final c = MegooFlagsConfig(
        sdkKey: key,
        baseUrl: base,
        disableCache: true,
        offline: true,
        initTimeout: const Duration(milliseconds: 1500),
        disableStream: true,
        pollInterval: const Duration(seconds: 45),
        flushInterval: const Duration(seconds: 20),
        sendContext: true,
        logger: logger,
        httpClient: client,
      );
      expect(c.disableCache, isTrue);
      expect(c.offline, isTrue);
      expect(c.initTimeout, const Duration(milliseconds: 1500));
      expect(c.disableStream, isTrue);
      expect(c.pollInterval, const Duration(seconds: 45));
      expect(c.flushInterval, const Duration(seconds: 20));
      expect(c.sendContext, isTrue);
      expect(c.logger, same(logger));
      expect(c.httpClient, same(client));
    });
  });

  group('initTimeout', () {
    for (final bad in [Duration.zero, const Duration(seconds: -1)]) {
      test('refuses $bad', () {
        expect(
          () => MegooFlagsConfig(sdkKey: key, baseUrl: base, initTimeout: bad),
          throwsConfigError('megooflags: initTimeout must be greater than 0'),
        );
      });
    }
  });

  group('clamps', () {
    test('raises pollInterval to 15 seconds', () {
      final c = MegooFlagsConfig(
        sdkKey: key,
        baseUrl: base,
        pollInterval: const Duration(seconds: 1),
      );
      expect(c.pollInterval, const Duration(seconds: 15));
    });

    test('clamps flushInterval to 10…60 seconds', () {
      Duration flush(Duration d) => MegooFlagsConfig(
        sdkKey: key,
        baseUrl: base,
        flushInterval: d,
      ).flushInterval;
      expect(flush(const Duration(seconds: 1)), const Duration(seconds: 10));
      expect(flush(const Duration(minutes: 5)), const Duration(seconds: 60));
      expect(flush(const Duration(seconds: 10)), const Duration(seconds: 10));
      expect(flush(const Duration(seconds: 60)), const Duration(seconds: 60));
    });
  });

  group('bootstrap', () {
    MegooFlagsConfig withBootstrap(String json) =>
        MegooFlagsConfig(sdkKey: key, baseUrl: base, bootstrap: json);

    test('parses the results of an evaluate response', () {
      final c = withBootstrap('''
        {"results": [
          {"key": "a", "value": {"x": [1, 2.5]}, "variant": "on",
           "reason": "TARGETING_MATCH", "error_code": null,
           "error_message": null, "extra": 1},
          {"key": "b", "value": null, "variant": null, "reason": "ERROR",
           "error_code": "TYPE_MISMATCH", "error_message": "wrong"},
          {"key": "c", "value": 3, "variant": "three", "reason": "DEFAULT",
           "error_code": null}
        ], "other": true}
      ''');
      final r = c.bootstrap!;
      expect(r, hasLength(3));
      expect(r[0].key, 'a');
      expect(r[0].value, {
        'x': [1, 2.5],
      });
      expect(r[0].variant, 'on');
      expect(r[0].reason, 'TARGETING_MATCH');
      expect(r[0].errorCode, isNull);
      expect(r[0].errorMessage, isNull);
      expect(r[1].variant, isNull);
      expect(r[1].errorCode, 'TYPE_MISMATCH');
      expect(r[1].errorMessage, 'wrong');
      expect(r[2].value, 3);
      expect(r[2].errorMessage, isNull);
    });

    test('accepts an empty results array', () {
      expect(withBootstrap('{"results": []}').bootstrap, isEmpty);
    });

    final invalid = <String, String>{
      '{': 'not valid JSON',
      '[]': 'not an object',
      '{}': 'results: missing',
      '{"results": {}}': 'results: not an array',
      '{"results": [1]}': 'results[0]: not an object',
      '{"results": [{"value": 1, "variant": null, "reason": "R", '
              '"error_code": null}]}':
          'results[0].key: missing',
      '{"results": [{"key": 1, "value": 1, "variant": null, "reason": "R", '
              '"error_code": null}]}':
          'results[0].key: not a string',
      '{"results": [{"key": "a", "variant": null, "reason": "R", '
              '"error_code": null}]}':
          'results[0].value: missing',
      '{"results": [{"key": "a", "value": 1, "reason": "R", '
              '"error_code": null}]}':
          'results[0].variant: missing',
      '{"results": [{"key": "a", "value": 1, "variant": 2, "reason": "R", '
              '"error_code": null}]}':
          'results[0].variant: not a string or null',
      '{"results": [{"key": "a", "value": 1, "variant": null, '
              '"error_code": null}]}':
          'results[0].reason: missing',
      '{"results": [{"key": "a", "value": 1, "variant": null, '
              '"reason": null, "error_code": null}]}':
          'results[0].reason: not a string',
      '{"results": [{"key": "a", "value": 1, "variant": null, '
              '"reason": "R"}]}':
          'results[0].error_code: missing',
      '{"results": [{"key": "a", "value": 1, "variant": null, '
              '"reason": "R", "error_code": 5}]}':
          'results[0].error_code: not a string or null',
      '{"results": [{"key": "a", "value": 1, "variant": null, '
              '"reason": "R", "error_code": null, "error_message": 5}]}':
          'results[0].error_message: not a string or null',
      '{"results": ['
              '{"key": "a", "value": 1, "variant": null, "reason": "R", '
              '"error_code": null}, '
              '{"key": "a", "value": 2, "variant": null, "reason": "R", '
              '"error_code": null}]}':
          'results[1].key: duplicate key',
    };
    invalid.forEach((json, why) {
      test('refuses $json', () {
        expect(
          () => withBootstrap(json),
          throwsConfigError('megooflags: invalid bootstrap: $why'),
        );
      });
    });
  });

  test('parseResults uses the given prefix', () {
    expect(
      () => parseResults('[]', 'evaluate'),
      throwsA(
        isA<FormatException>().having(
          (e) => e.message,
          'message',
          'evaluate: not an object',
        ),
      ),
    );
  });
}
