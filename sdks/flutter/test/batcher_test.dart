import 'dart:async';
import 'dart:convert';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:megooflags/src/batcher.dart';
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/exposures.dart';
import 'package:megooflags/src/transport.dart';
import 'package:megooflags/src/version.dart';

import 'backoff_test.dart' show FixedRandom;

const key = 'mf_client_secret123';
const base = 'https://flags.example.com';
const user1 = {'key': 'user-1'};

/// A fake events server: it answers each request from its queue (the last
/// answer repeats). An answer is a status, a (status, headers) record,
/// 'network' (the request throws) or 'hang' (it never answers).
class FakeEvents {
  FakeEvents(this._answers);

  final List<Object> _answers;
  final calls = <http.Request>[];

  late final client = MockClient((request) {
    calls.add(request);
    final a = _answers.length > 1 ? _answers.removeAt(0) : _answers.single;
    return switch (a) {
      'network' => Future.error(http.ClientException('connection refused')),
      'hang' => Completer<http.Response>().future,
      int status => Future.value(http.Response('', status)),
      (int status, Map<String, String> headers) => Future.value(
        http.Response('', status, headers: headers),
      ),
      _ => throw ArgumentError(a),
    };
  });

  List<Map<String, Object?>> exposuresOf(http.Request r) => [
    for (final e in (jsonDecode(r.body) as Map)['exposures'] as List)
      e as Map<String, Object?>,
  ];

  List<int> get sizes => [for (final c in calls) exposuresOf(c).length];
}

void main() {
  late List<String> lines;
  late Exposures exposures;
  late List<FatalException> fatal;
  Batcher? batcher;

  void logger(MegooFlagsLogLevel level, String message) =>
      lines.add('${level.name} $message');

  Batcher make(FakeEvents server) => batcher = Batcher(
    server.client,
    baseUrl: base,
    sdkKey: key,
    exposures: exposures,
    flushInterval: const Duration(seconds: 30),
    logger: logger,
    onFatal: fatal.add,
    random: FixedRandom(1),
  );

  /// fill records [n] distinct exposures.
  void fill(int n, [String prefix = 'f']) {
    for (var i = 0; i < n; i++) {
      exposures.record('$prefix$i', 'on', 'STATIC', user1);
    }
  }

  /// run runs [body] in fake time, then closes the batcher.
  void run(void Function(FakeAsync async) body) => fakeAsync((async) {
    lines = [];
    fatal = [];
    exposures = Exposures(
      sendContext: false,
      logger: logger,
      now: () => DateTime.utc(2026, 11, 10, 9, 15, 2),
    );
    batcher = null;
    body(async);
    unawaited(batcher?.close(timeout: Duration.zero));
    async.elapse(const Duration(seconds: 5));
  });

  void flush(FakeAsync async) {
    unawaited(batcher!.flush());
    async.flushMicrotasks();
  }

  test('flushes after the interval', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server).start();
      fill(3);

      async.elapse(const Duration(milliseconds: 29999));
      expect(server.calls, isEmpty);
      async.elapse(const Duration(milliseconds: 1));

      expect(server.sizes, [3]);
      expect(exposures.size, 0);
    });
  });

  test('posts exposures only, with the key', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server);
      fill(1);

      flush(async);

      final call = server.calls.single;
      expect(call.method, 'POST');
      expect(call.url.toString(), '$base/sdk/v1/events');
      expect(call.headers, {
        'Authorization': 'Bearer $key',
        'Accept': 'application/json',
        'Content-Type': startsWith('application/json'),
        'User-Agent': 'megooflags-flutter/$megooFlagsVersion',
      });
      expect((jsonDecode(call.body) as Map).keys, ['exposures']);
      expect(server.exposuresOf(call), [
        {
          'flag_key': 'f0',
          'variant': 'on',
          'reason': 'STATIC',
          'timestamp': '2026-11-10T09:15:02.000Z',
        },
      ]);
    });
  });

  test('logs the count it sent after a 202', () {
    run((async) {
      make(FakeEvents([202]));
      fill(2);

      flush(async);

      expect(lines, contains('debug megooflags: sent 2 exposures'));
    });
  });

  test('does not send an empty batch', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server);

      flush(async);

      expect(server.calls, isEmpty);
    });
  });

  test('flushes at once when 1000 exposures are buffered', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server).start();

      fill(999);
      async.flushMicrotasks();
      expect(server.calls, isEmpty);
      fill(1, 'last');
      async.flushMicrotasks();

      expect(server.sizes, [1000]);
    });
  });

  test('sends at most 1000 exposures per batch', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server);
      fill(2500);

      flush(async);

      expect(server.sizes, [1000, 1000, 500]);
    });
  });

  test('keeps one send in flight', () {
    run((async) {
      final server = FakeEvents(['hang']);
      make(server);
      fill(1);

      unawaited(batcher!.flush());
      unawaited(batcher!.flush());
      async.flushMicrotasks();

      expect(server.calls, hasLength(1));
    });
  });

  test('splits a 413 batch of 1000 into 500 and 500', () {
    run((async) {
      final server = FakeEvents([413, 202, 202]);
      make(server);
      fill(1000);

      flush(async);

      expect(server.sizes, [1000, 500, 500]);
      expect(exposures.size, 0);
    });
  });

  test('splits recursively and drops a single item that is still 413', () {
    run((async) {
      final server = FakeEvents([413, 202, 413, 413, 202]);
      make(server);
      fill(3);

      flush(async);

      // [3] 413 → [1] 202, [2] 413 → [1] 413 dropped, [1] 202
      expect(server.sizes, [3, 1, 2, 1, 1]);
      expect(lines.where((l) => l.startsWith('warn ')), hasLength(1));
      expect(exposures.size, 0);
    });
  });

  test('drops a 422 batch with a warning', () {
    run((async) {
      final server = FakeEvents([422, 202]);
      make(server);
      fill(2);

      flush(async);
      flush(async);

      expect(server.sizes, [2]);
      expect(exposures.size, 0);
      expect(
        lines,
        contains(
          'warn megooflags: the server refused 2 exposures (HTTP 422); '
          'dropping them',
        ),
      );
    });
  });

  test('retries a 429 after Retry-After, keeping the items in front', () {
    run((async) {
      final server = FakeEvents([
        (429, {'retry-after': '7'}),
        202,
      ]);
      make(server);
      fill(2);

      flush(async);
      fill(1, 'later');
      expect(exposures.size, 3);

      flush(async); // still waiting: nothing sent
      async.elapse(const Duration(milliseconds: 6999));
      expect(server.calls, hasLength(1));
      async.elapse(const Duration(milliseconds: 1));

      expect(server.calls, hasLength(2));
      expect(
        [for (final e in server.exposuresOf(server.calls[1])) e['flag_key']],
        ['f0', 'f1', 'later0'],
      );
    });
  });

  test('retries a 503 with a backoff from 1 s', () {
    run((async) {
      final server = FakeEvents([503, 503, 202]);
      make(server);
      fill(1);

      flush(async);
      async.elapse(const Duration(milliseconds: 999));
      expect(server.calls, hasLength(1));
      async.elapse(const Duration(milliseconds: 1));
      expect(server.calls, hasLength(2));
      // The second wait is 2 s (the jitter factor is fixed at 1).
      async.elapse(const Duration(milliseconds: 1999));
      expect(server.calls, hasLength(2));
      async.elapse(const Duration(milliseconds: 1));

      expect(server.calls, hasLength(3));
      expect(exposures.size, 0);
    });
  });

  test('retries after a network failure', () {
    run((async) {
      final server = FakeEvents(['network', 202]);
      make(server);
      fill(1);

      flush(async);
      expect(exposures.size, 1);
      async.elapse(const Duration(seconds: 1));

      expect(server.calls, hasLength(2));
      expect(exposures.size, 0);
    });
  });

  test('retries a request that takes longer than 10 s', () {
    run((async) {
      final server = FakeEvents(['hang', 202]);
      make(server);
      fill(1);

      flush(async);
      async.elapse(const Duration(seconds: 10));
      expect(exposures.size, 1);
      async.elapse(const Duration(seconds: 1));

      expect(server.sizes, [1, 1]);
      expect(exposures.size, 0);
    });
  });

  for (final status in [401, 403]) {
    test('calls onFatal on $status and stops', () {
      run((async) {
        final server = FakeEvents([status, 202]);
        make(server).start();
        fill(1);

        flush(async);
        fill(1, 'later');
        flush(async);
        async.elapse(const Duration(minutes: 2));

        expect([for (final e in fatal) e.status], [status]);
        expect(server.calls, hasLength(1));
      });
    });
  }

  test('close completes within 5 s when the server never answers', () {
    run((async) {
      final server = FakeEvents(['hang']);
      make(server).start();
      fill(1);

      var done = false;
      batcher!.close().then((_) => done = true);
      async.elapse(const Duration(milliseconds: 4999));
      expect(done, isFalse);
      async.elapse(const Duration(milliseconds: 1));

      expect(done, isTrue);
      expect(server.calls, hasLength(1));
    });
  });

  test('does not start another send while closing', () {
    run((async) {
      final server = FakeEvents(['hang']);
      make(server);
      fill(1);

      unawaited(batcher!.close());
      async.flushMicrotasks();
      fill(1, 'late');
      flush(async);

      expect(server.calls, hasLength(1));
    });
  });

  test('close flushes once; a second close returns at once', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server).start();
      fill(1);

      unawaited(batcher!.close());
      async.flushMicrotasks();
      expect(server.sizes, [1]);

      fill(1, 'late');
      var done = false;
      batcher!.close().then((_) => done = true);
      async.flushMicrotasks();

      expect(done, isTrue);
      async.elapse(const Duration(minutes: 1));
      expect(server.calls, hasLength(1));
    });
  });

  test('stop sends nothing more, even on close', () {
    run((async) {
      final server = FakeEvents([202]);
      make(server).start();
      fill(2);

      batcher!.stop();
      async.elapse(const Duration(minutes: 1));
      unawaited(batcher!.close());
      async.flushMicrotasks();

      expect(server.calls, isEmpty);
      expect(async.pendingTimers, isEmpty);
    });
  });
}
