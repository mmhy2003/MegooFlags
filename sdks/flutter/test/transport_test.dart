import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/megooflags.dart';
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/transport.dart';

const key = 'mf_client_x';
final now = DateTime.utc(2026, 11, 14, 12);

String? Function(String) header([Map<String, String> h = const {}]) =>
    (name) => h.entries
        .where((e) => e.key.toLowerCase() == name.toLowerCase())
        .map((e) => e.value)
        .firstOrNull;

const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/// httpDate writes [t] as an IMF-fixdate.
String httpDate(DateTime t) {
  final u = t.toUtc();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${_days[u.weekday - 1]}, ${two(u.day)} ${_months[u.month - 1]} '
      '${u.year} ${two(u.hour)}:${two(u.minute)}:${two(u.second)} GMT';
}

void main() {
  group('buildHeaders', () {
    test('sends the key as a bearer token and the SDK User-Agent', () {
      expect(buildHeaders(sdkKey: key, accept: 'application/json'), {
        'Authorization': 'Bearer mf_client_x',
        'Accept': 'application/json',
        'User-Agent': 'megooflags-flutter/$megooFlagsVersion',
      });
    });

    test('skips the User-Agent on the web, which cannot set it', () {
      expect(buildHeaders(sdkKey: key, accept: 'application/json', web: true), {
        'Authorization': 'Bearer mf_client_x',
        'Accept': 'application/json',
      });
    });

    test('adds Content-Type only for a JSON body', () {
      expect(
        buildHeaders(
          sdkKey: key,
          accept: 'application/json',
          json: true,
        )['Content-Type'],
        'application/json',
      );
      expect(
        buildHeaders(sdkKey: key, accept: 'application/json'),
        isNot(contains('Content-Type')),
      );
    });

    test('adds If-None-Match only when an ETag is given', () {
      expect(
        buildHeaders(
          sdkKey: key,
          accept: 'application/json',
          etag: '"e1"',
        )['If-None-Match'],
        '"e1"',
      );
      expect(
        buildHeaders(sdkKey: key, accept: 'application/json', etag: null),
        isNot(contains('If-None-Match')),
      );
    });

    test('adds Last-Event-ID only when one is given', () {
      final h = buildHeaders(
        sdkKey: key,
        accept: 'text/event-stream',
        lastEventId: '42',
      );
      expect(h['Accept'], 'text/event-stream');
      expect(h['Last-Event-ID'], '42');
      expect(
        buildHeaders(sdkKey: key, accept: 'text/event-stream'),
        isNot(contains('Last-Event-ID')),
      );
    });
  });

  group('statusError', () {
    for (final status in [200, 202, 204, 304]) {
      test('$status is not an error', () {
        expect(statusError(status, header(), now: now), isNull);
      });
    }

    for (final status in [401, 403]) {
      test('$status is fatal', () {
        final err = statusError(status, header(), now: now);
        expect(err, isA<FatalException>());
        expect((err as FatalException).status, status);
        expect(err.message, 'megooflags: SDK key refused ($status)');
      });
    }

    test('429 carries integer Retry-After seconds', () {
      final err = statusError(429, header({'Retry-After': '7'}), now: now);
      expect(err, isA<RetryAfterException>());
      expect((err as RetryAfterException).wait, const Duration(seconds: 7));
      expect(err.message, 'megooflags: rate limited, retry after 7s');
    });

    test('429 reads the header case-insensitively through the reader', () {
      final err = statusError(429, header({'retry-after': '3'}), now: now);
      expect((err as RetryAfterException).wait, const Duration(seconds: 3));
    });

    test('429 reads an HTTP-date Retry-After', () {
      final at = httpDate(now.add(const Duration(seconds: 30)));
      final err = statusError(429, header({'Retry-After': at}), now: now);
      expect((err as RetryAfterException).wait, const Duration(seconds: 30));
    });

    test('429 without Retry-After waits 60 s', () {
      final err = statusError(429, header(), now: now);
      expect((err as RetryAfterException).wait, const Duration(seconds: 60));
    });

    test('uses the current time by default', () {
      final at = httpDate(DateTime.now().add(const Duration(seconds: 90)));
      final err = statusError(429, header({'Retry-After': at}));
      final wait = (err as RetryAfterException).wait;
      expect(wait, greaterThanOrEqualTo(const Duration(seconds: 88)));
      expect(wait, lessThanOrEqualTo(const Duration(seconds: 90)));
    });

    for (final status in [404, 413, 422, 500, 503]) {
      test('$status is an HTTP error', () {
        final err = statusError(status, header(), now: now);
        expect(err, isA<HttpStatusException>());
        expect((err as HttpStatusException).status, status);
        expect(err.message, 'megooflags: HTTP $status');
      });
    }

    test('the other failures have fixed messages', () {
      expect(const FatalException(null).message, 'megooflags: SDK key refused');
      expect(
        const RevokedException().message,
        'megooflags: SDK key refused (revoked)',
      );
      expect(
        const InvalidResponseException('bad JSON').message,
        'megooflags: invalid response: bad JSON',
      );
    });

    test('toString is the message', () {
      expect(const HttpStatusException(500).toString(), 'megooflags: HTTP 500');
    });

    test('never puts the key in a message', () {
      final errors = <SdkException?>[
        const FatalException(401),
        const FatalException(null),
        const RevokedException(),
        statusError(429, header(), now: now),
        statusError(500, header(), now: now),
      ];
      for (final err in errors) {
        expect(err!.message, isNot(contains('mf_client_')));
      }
    });
  });

  group('parseRetryAfter', () {
    final cases = <String, int>{
      '5': 5,
      '0': 0,
      '-1': 0,
      ' 7 ': 7,
      httpDate(now.add(const Duration(seconds: 30))): 30,
      httpDate(now.subtract(const Duration(hours: 1))): 0,
      'Sunday, 14-Nov-26 12:00:30 GMT': 30,
      'Sat Nov 14 12:00:30 2026': 30,
    };
    cases.forEach((value, want) {
      test('"$value" is $want s', () {
        expect(parseRetryAfter(value, now), Duration(seconds: want));
      });
    });

    test('rounds a partial second of an HTTP date up', () {
      final at = httpDate(now.add(const Duration(seconds: 30)));
      expect(
        parseRetryAfter(at, now.subtract(const Duration(milliseconds: 500))),
        const Duration(seconds: 31),
      );
    });

    for (final value in [
      'abc',
      '',
      '1.5',
      '5s',
      'Sat, 14 Nov 2026 12:00:30',
      'Sat, 31 Feb 2026 12:00:30 GMT',
      'Sat, 14 Nov 2026 24:00:30 GMT',
      null,
    ]) {
      test('"$value" is 60 s', () {
        expect(parseRetryAfter(value, now), const Duration(seconds: 60));
      });
    }
  });

  group('defaultLogger', () {
    test('writes warn and error, keeps debug and info silent', () {
      final seen = <(String, String, int)>[];
      final logger = developerLogger(
        (message, {String name = '', int level = 0}) =>
            seen.add((message, name, level)),
      );
      logger(MegooFlagsLogLevel.debug, 'megooflags: stream connected');
      logger(MegooFlagsLogLevel.info, 'megooflags: hello');
      logger(MegooFlagsLogLevel.warn, 'megooflags: stream failed: x');
      logger(MegooFlagsLogLevel.error, 'megooflags: boom');
      expect(seen, [
        ('megooflags: stream failed: x', 'megooflags', 900),
        ('megooflags: boom', 'megooflags', 1000),
      ]);
    });

    test('is a MegooFlagsLogger', () {
      const MegooFlagsLogger logger = defaultLogger;
      expect(
        () => logger(MegooFlagsLogLevel.debug, 'megooflags: quiet'),
        returnsNormally,
      );
    });
  });
}
