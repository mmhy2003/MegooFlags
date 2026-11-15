import 'dart:async';
import 'dart:convert';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/cache.dart';
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/context.dart';
import 'package:megooflags/src/evaluator.dart';
import 'package:megooflags/src/poller.dart';
import 'package:megooflags/src/resolve.dart';
import 'package:megooflags/src/store.dart';
import 'package:megooflags/src/streamer.dart';
import 'package:megooflags/src/sync.dart';
import 'package:megooflags/src/transport.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'cache_test.dart' show MemoryStore;

const key = 'mf_client_secret123';
final user1 = EvaluationContext(targetingKey: 'user-1');

Map<String, Object?> result({
  String key = 'f',
  Object? value = true,
  String? variant = 'on',
  String reason = 'TARGETING_MATCH',
}) => {
  'key': key,
  'value': value,
  'variant': variant,
  'reason': reason,
  'error_code': null,
  'error_message': null,
};

EvaluateResult parsed(Map<String, Object?> r) => parseResults(
  jsonEncode({
    'results': [r],
  }),
  'test',
).single;

EvaluateOk ok(List<Map<String, Object?>> results, [String? etag = '"e1"']) =>
    EvaluateOk([for (final r in results) parsed(r)], etag);

String bootstrap(List<Map<String, Object?>> results) =>
    jsonEncode({'results': results});

void silent(MegooFlagsLogLevel level, String message) {}

/// One evaluate call; the test answers or fails it.
class Call {
  Call(this.body, this.etag);

  final Map<String, Object?> body;
  final String? etag;
  final _done = Completer<EvaluateOutcome>();

  void answer(EvaluateOutcome o) => _done.complete(o);

  void fail(Object e) => _done.completeError(e);
}

/// An evaluator whose answers the test gives.
class FakeEvaluator implements Evaluator {
  final calls = <Call>[];

  Call get last => calls.last;

  @override
  Future<EvaluateOutcome> evaluate(Map<String, Object?> body, String? etag) {
    final c = Call(body, etag);
    calls.add(c);
    return c._done.future;
  }
}

class FakeStreamer implements Streamer {
  int starts = 0;
  int pauses = 0;
  int resumes = 0;
  int stops = 0;

  @override
  String? get lastEventId => null;

  @override
  void start() => starts++;

  @override
  void pause() => pauses++;

  @override
  void resume() => resumes++;

  @override
  void stop() => stops++;
}

/// What a future did: still pending, done, or failed with [error].
class Outcome {
  bool done = false;
  Object? error;

  bool get settled => done || error != null;
}

Outcome watch(Future<void> f) {
  final o = Outcome();
  f.then(
    (_) {
      o.done = true;
    },
    onError: (Object e) {
      o.error = e;
    },
  );
  return o;
}

class Harness {
  Harness({
    String? bootstrap,
    bool offline = false,
    bool disableStream = false,
    MemoryStore? storage,
  }) : storage = storage ?? MemoryStore() {
    config = MegooFlagsConfig(
      sdkKey: key,
      baseUrl: 'https://flags.example.com',
      bootstrap: bootstrap,
      offline: offline,
      disableStream: disableStream,
      logger: silent,
    );
    cache = Cache(key, this.storage, silent);
    sync = Sync(
      config: config,
      store: store,
      cache: cache,
      evaluator: evaluator,
      newStreamer: (hooks) {
        this.hooks = hooks;
        return streamer;
      },
      events: SyncEvents(
        ready: () => events.add('ready'),
        stale: () => events.add('stale'),
        changed: (flags) => events.add(['changed', flags]),
        fatal: (e) => events.add(['fatal', e]),
        error: (e) => events.add(['error', e]),
      ),
      onFatalStop: () => fatalStops++,
    );
  }

  final MemoryStore storage;
  late final MegooFlagsConfig config;
  late final Cache cache;
  late final Sync sync;
  final store = Store();
  final evaluator = FakeEvaluator();
  final streamer = FakeStreamer();
  final events = <Object>[];
  late StreamerHooks hooks;
  int fatalStops = 0;

  ResolutionDetails<bool> read([String flag = 'f']) =>
      resolve(store.current, flag, FlagKind.boolean, false);
}

Future<void> saveCacheFor(
  MemoryStore storage,
  EvaluationContext ctx,
  List<Map<String, Object?>> results,
  String? etag,
) => Cache(key, storage, silent).save(
  StoredResults.fromList(
    [for (final r in results) parsed(r)],
    etag: etag,
    source: Source.network,
    contextHash: contextHash(toRequestContext(ctx)),
    targetingKey: null,
  ),
);

/// started gives a Sync for user1 whose first refresh answered f=true.
Harness started(FakeAsync async, {bool disableStream = false}) {
  final t = Harness(
    bootstrap: bootstrap([result()]),
    disableStream: disableStream,
  );
  watch(t.sync.start(user1));
  async.flushMicrotasks();
  t.evaluator.last.answer(ok([result()]));
  async.flushMicrotasks();
  return t;
}

const s = Duration(seconds: 1);
Duration ms(int n) => Duration(milliseconds: n);

void main() {
  /// run runs [body] in fake time and stops what it started.
  void run(void Function(FakeAsync async) body) => fakeAsync(body);

  group('Sync.start', () {
    test('returns at once with bootstrap values, then a refresh replaces '
        'them', () {
      run((async) {
        final t = Harness(
          bootstrap: bootstrap([
            result(value: false, variant: 'off', reason: 'STATIC'),
          ]),
        );

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();

        expect(start.done, isTrue);
        expect(t.read().value, false);
        expect(t.read().variant, 'off');
        expect(t.read().reason, 'STATIC');
        expect(t.store.current!.source, Source.bootstrap);
        expect(t.evaluator.calls, hasLength(1));
        expect(t.evaluator.last.body, {'key': 'user-1'});
        expect(t.streamer.starts, 0);

        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();

        expect(t.read().value, true);
        expect(t.read().variant, 'on');
        expect(t.read().reason, 'TARGETING_MATCH');
        expect(t.store.current!.source, Source.network);
        expect(t.events, [
          [
            'changed',
            ['f'],
          ],
        ]);
        expect(t.storage.values[t.cache.key], contains(r'"etag":"\"e1\""'));
        expect(t.streamer.starts, 1);
      });
    });

    test('serves a matching cache entry over bootstrap, sends its ETag and '
        'a 304 makes it fresh', () {
      run((async) {
        final storage = MemoryStore();
        saveCacheFor(storage, user1, [
          result(value: false, variant: 'off'),
        ], '"c1"');
        async.flushMicrotasks();
        final t = Harness(
          bootstrap: bootstrap([result(reason: 'STATIC')]),
          storage: storage,
        );

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();

        expect(start.done, isTrue);
        expect(t.read().value, false);
        expect(t.read().reason, 'CACHED');
        expect(t.store.current!.targetingKey, 'user-1');
        expect(t.evaluator.last.etag, '"c1"');

        t.evaluator.last.answer(const EvaluateNotModified('"c1"'));
        async.flushMicrotasks();

        expect(t.read().value, false);
        expect(t.read().variant, 'off');
        expect(t.read().reason, 'TARGETING_MATCH');
        expect(t.store.current!.source, Source.network);
        expect(t.events, isEmpty);
      });
    });

    test('ignores a cache entry for another context', () {
      run((async) {
        final storage = MemoryStore();
        saveCacheFor(storage, EvaluationContext(targetingKey: 'user-2'), [
          result(),
        ], '"c1"');
        async.flushMicrotasks();
        final t = Harness(storage: storage);

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();

        expect(start.settled, isFalse);
        expect(t.store.current, isNull);
        expect(t.evaluator.last.etag, isNull);
        t.sync.stop();
        async.flushMicrotasks();
      });
    });

    test('waits for the first evaluate when there are no values', () {
      run((async) {
        final t = Harness();

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();
        expect(start.settled, isFalse);
        expect(t.read().errorCode, ErrorCode.providerNotReady);

        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();

        expect(start.done, isTrue);
        expect(t.read().value, true);
        expect(t.events, isEmpty);
        expect(t.streamer.starts, 1);
      });
    });

    test('fails after initTimeout and emits ready when values arrive '
        'later', () {
      run((async) {
        final t = Harness();

        final start = watch(t.sync.start(user1));
        async.elapse(ms(4999));
        expect(start.settled, isFalse);
        async.elapse(ms(1));
        expect(start.error, isA<SyncException>());
        expect(
          (start.error! as SyncException).message,
          'megooflags: no flag values within 5s',
        );
        expect(t.events, isEmpty);

        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();

        expect(t.read().value, true);
        expect(t.events, ['ready']);
      });
    });

    test('fails with a fatal error on 401 and retries nothing', () {
      run((async) {
        final t = Harness();

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();
        t.evaluator.last.fail(const FatalException(401));
        async.flushMicrotasks();
        async.elapse(const Duration(minutes: 5));

        expect(start.error, isA<FatalException>());
        expect(t.evaluator.calls, hasLength(1));
        expect(t.streamer.starts, 0);
        expect(t.fatalStops, 1);
        expect(t.events, isEmpty);
      });
    });

    test('fails offline without bootstrap or cache', () {
      run((async) {
        final t = Harness(offline: true);

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();

        expect(
          (start.error! as SyncException).message,
          'megooflags: offline mode needs bootstrap values or a cache entry',
        );
      });
    });

    test('starts offline from bootstrap without any network', () {
      run((async) {
        final t = Harness(offline: true, bootstrap: bootstrap([result()]));

        final start = watch(t.sync.start(user1));
        final refresh = watch(t.sync.refresh());
        async.elapse(const Duration(minutes: 2));

        expect(start.done, isTrue);
        expect(refresh.done, isTrue);
        expect(t.read().value, true);
        expect(t.evaluator.calls, isEmpty);
        expect(t.streamer.starts, 0);
      });
    });
  });

  group('Sync.refresh', () {
    test('runs at most one evaluate in flight and one queued', () {
      run((async) {
        final t = started(async);
        expect(t.evaluator.calls, hasLength(1));

        t.hooks.onChanged('1');
        t.hooks.onChanged('2');
        t.hooks.onChanged('3');
        async.flushMicrotasks();
        expect(t.evaluator.calls, hasLength(2));

        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();
        expect(t.evaluator.calls, hasLength(3));
        t.evaluator.last.answer(ok([result()]));
        async.elapse(const Duration(minutes: 2));

        expect(t.evaluator.calls, hasLength(3));
      });
    });

    test('a call while one is queued shares its run', () {
      run((async) {
        final t = started(async);
        t.hooks.onChanged('1');
        async.flushMicrotasks();

        final a = watch(t.sync.refresh());
        final b = watch(t.sync.refresh());
        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();
        expect(a.settled || b.settled, isFalse);
        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();

        expect(a.done && b.done, isTrue);
        expect(t.evaluator.calls, hasLength(3));
      });
    });

    test('sends the stored ETag and emits no change when nothing '
        'changed', () {
      run((async) {
        final t = started(async);

        t.hooks.onChanged('1');
        async.flushMicrotasks();
        expect(t.evaluator.last.etag, '"e1"');
        t.evaluator.last.answer(ok([result()], '"e2"'));
        async.flushMicrotasks();

        expect(t.store.current!.etag, '"e2"');
        expect(t.events, isEmpty);
      });
    });

    test('retries a failure with backoff from 1 s', () {
      run((async) {
        final t = started(async);

        final refresh = watch(t.sync.refresh());
        async.flushMicrotasks();
        const err = HttpStatusException(500);
        t.evaluator.last.fail(err);
        async.flushMicrotasks();
        expect(refresh.error, err);
        expect(t.events, [
          ['error', err],
        ]);

        async.elapse(ms(999));
        expect(t.evaluator.calls, hasLength(2));
        async.elapse(ms(1));
        expect(t.evaluator.calls, hasLength(3));

        t.evaluator.last.fail(StateError('network'));
        async.elapse(ms(1599));
        expect(t.evaluator.calls, hasLength(3));
        async.elapse(ms(401));
        expect(t.evaluator.calls, hasLength(4));

        t.evaluator.last.answer(ok([result()]));
        async.elapse(const Duration(minutes: 5));
        expect(t.evaluator.calls, hasLength(4));
      });
    });

    test('waits Retry-After after a 429', () {
      run((async) {
        final t = started(async);

        t.hooks.onChanged('1');
        async.flushMicrotasks();
        t.evaluator.last.fail(const RetryAfterException(Duration(seconds: 7)));
        async.elapse(ms(6999));
        expect(t.evaluator.calls, hasLength(2));
        async.elapse(ms(1));

        expect(t.evaluator.calls, hasLength(3));
      });
    });

    test('stops everything on a fatal answer after start', () {
      run((async) {
        final t = started(async);

        t.hooks.onChanged('1');
        async.flushMicrotasks();
        const err = FatalException(403);
        t.evaluator.last.fail(err);
        async.elapse(const Duration(minutes: 5));

        expect(t.events, [
          ['fatal', err],
        ]);
        expect(t.streamer.stops, 1);
        expect(t.fatalStops, 1);
        expect(t.evaluator.calls, hasLength(2));
        expect(t.read().value, true);
        final after = watch(t.sync.refresh());
        async.flushMicrotasks();
        expect(after.error, err);
        expect(t.evaluator.calls, hasLength(2));
      });
    });

    test('stops everything when the stream reports the key revoked', () {
      run((async) {
        final t = started(async);

        const err = RevokedException();
        t.hooks.onFatal(err);
        t.hooks.onChanged('1');
        async.elapse(const Duration(minutes: 5));

        expect(t.events, [
          ['fatal', err],
        ]);
        expect(t.fatalStops, 1);
        expect(t.evaluator.calls, hasLength(1));
      });
    });

    test('drops a 304 when there are no values for the context', () {
      run((async) {
        final t = Harness();

        final start = watch(t.sync.start(user1));
        async.flushMicrotasks();
        t.evaluator.last.answer(const EvaluateNotModified('"x"'));
        async.flushMicrotasks();

        expect(start.error, isA<InvalidResponseException>());
        expect(t.store.current, isNull);
        t.sync.stop();
        async.flushMicrotasks();
      });
    });
  });

  group('Sync.setContext', () {
    test('clears the store and the cache entry at once when the targeting '
        'key changes', () {
      run((async) {
        final t = started(async);
        expect(t.storage.values.containsKey(t.cache.key), isTrue);

        final change = watch(
          t.sync.setContext(EvaluationContext(targetingKey: 'user-2')),
        );

        expect(t.read().errorCode, ErrorCode.providerNotReady);
        async.flushMicrotasks();
        expect(t.storage.values.containsKey(t.cache.key), isFalse);
        expect(t.evaluator.last.body, {'key': 'user-2'});
        expect(t.evaluator.last.etag, isNull);
        expect(change.settled, isFalse);

        t.evaluator.last.answer(ok([result(value: false, variant: 'off')]));
        async.flushMicrotasks();

        expect(change.done, isTrue);
        expect(t.read().value, false);
        expect(t.sync.body, {'key': 'user-2'});
        expect(t.events, isEmpty);
      });
    });

    test('keeps serving the old values when only attributes change', () {
      run((async) {
        final t = started(async);

        final change = watch(
          t.sync.setContext(
            EvaluationContext(
              targetingKey: 'user-1',
              attributes: {'plan': 'pro'},
            ),
          ),
        );

        expect(t.read().value, true);
        async.flushMicrotasks();
        expect(t.evaluator.last.body, {'key': 'user-1', 'plan': 'pro'});
        expect(t.evaluator.last.etag, isNull);
        t.evaluator.last.answer(ok([result(value: false, variant: 'off')]));
        async.flushMicrotasks();

        expect(change.done, isTrue);
        expect(t.read().value, false);
        expect(t.events, [
          [
            'changed',
            ['f'],
          ],
        ]);
      });
    });

    test('ignores a late answer for the old context', () {
      run((async) {
        final t = started(async);
        t.hooks.onChanged('1');
        async.flushMicrotasks();
        final old = t.evaluator.last;

        final change = watch(
          t.sync.setContext(
            EvaluationContext(
              targetingKey: 'user-1',
              attributes: {'plan': 'pro'},
            ),
          ),
        );
        async.flushMicrotasks();
        expect(t.evaluator.calls, hasLength(3));
        old.answer(ok([result(key: 'late')], '"old"'));
        async.flushMicrotasks();

        expect(t.store.current!.results.containsKey('late'), isFalse);
        expect(t.evaluator.calls, hasLength(3));
        expect(t.evaluator.last.etag, isNull);
        t.evaluator.last.answer(ok([result(value: false, variant: 'off')]));
        async.flushMicrotasks();

        expect(change.done, isTrue);
        expect(t.read().value, false);
        expect(t.store.current!.results.containsKey('late'), isFalse);
      });
    });

    test('fails when the evaluate for the new context fails, and keeps '
        'retrying', () {
      run((async) {
        final t = started(async);

        final change = watch(
          t.sync.setContext(EvaluationContext(targetingKey: 'user-2')),
        );
        async.flushMicrotasks();
        t.evaluator.last.fail(const HttpStatusException(503));
        async.flushMicrotasks();
        expect(change.error, isA<HttpStatusException>());
        async.elapse(s);
        t.evaluator.last.answer(ok([result()]));
        async.flushMicrotasks();

        expect(t.evaluator.calls, hasLength(3));
        expect(t.read().value, true);
      });
    });
  });

  group('Sync stream fallback', () {
    test('polls after two stream failures, stops on reconnect, and emits '
        'stale then ready', () {
      run((async) {
        final t = started(async);
        final err = StateError('x');
        t.hooks.onConnected();

        t.hooks.onFailure(1, err);
        async.elapse(const Duration(seconds: 60));
        expect(t.evaluator.calls, hasLength(1));
        expect(t.events, isEmpty);

        t.hooks.onFailure(2, err);
        expect(t.events, ['stale']);
        async.elapse(const Duration(seconds: 30));
        expect(t.evaluator.calls, hasLength(2));
        t.evaluator.last.answer(ok([result()]));
        t.hooks.onFailure(3, err);
        async.elapse(const Duration(seconds: 30));
        expect(t.evaluator.calls, hasLength(3));
        t.evaluator.last.answer(ok([result()]));

        t.hooks.onConnected();
        async.elapse(const Duration(minutes: 2));

        expect(t.evaluator.calls, hasLength(3));
        expect(t.events, ['stale', 'ready']);
      });
    });

    test('emits no stale for failures before the stream ever connected', () {
      run((async) {
        final t = started(async);

        t.hooks.onFailure(2, StateError('x'));
        t.hooks.onConnected();

        expect(t.events, isEmpty);
      });
    });

    test('polls from the start with disableStream', () {
      run((async) {
        final t = started(async, disableStream: true);
        expect(t.streamer.starts, 0);

        async.elapse(const Duration(seconds: 30));
        expect(t.evaluator.calls, hasLength(2));
        t.evaluator.last.answer(ok([result()]));
        async.elapse(const Duration(seconds: 30));

        expect(t.evaluator.calls, hasLength(3));
        t.sync.stop();
      });
    });

    test('pause stops the stream and the poller; resume restarts what '
        'ran', () {
      run((async) {
        final t = started(async);
        t.hooks.onFailure(2, StateError('x'));

        t.sync.pause();
        async.elapse(const Duration(seconds: 60));
        expect(t.streamer.pauses, 1);
        expect(t.evaluator.calls, hasLength(1));

        t.sync.resume();
        async.elapse(const Duration(seconds: 30));

        expect(t.streamer.resumes, 1);
        expect(t.evaluator.calls, hasLength(2));
        t.sync.stop();
      });
    });

    test('stop ends the stream, the poller and the retries', () {
      run((async) {
        final t = started(async);
        t.hooks.onFailure(2, StateError('x'));
        watch(t.sync.refresh());
        async.flushMicrotasks();
        t.evaluator.last.fail(const HttpStatusException(500));
        async.flushMicrotasks();

        t.sync.stop();
        async.elapse(const Duration(minutes: 5));

        expect(t.streamer.stops, 1);
        expect(t.evaluator.calls, hasLength(2));
        expect(async.pendingTimers, isEmpty);
        final after = watch(t.sync.refresh());
        async.flushMicrotasks();
        expect(after.error, isA<SyncException>());
      });
    });
  });

  group('Poller', () {
    test('calls tick every interval while running', () {
      fakeAsync((async) {
        var ticks = 0;
        final p = Poller(const Duration(seconds: 30), () async => ticks++);

        p.start();
        p.start();
        expect(p.running, isTrue);
        async.elapse(const Duration(seconds: 60));
        expect(ticks, 2);

        p.stop();
        expect(p.running, isFalse);
        async.elapse(const Duration(seconds: 60));
        expect(ticks, 2);
      });
    });

    test('never ticks more often than every 15 s', () {
      fakeAsync((async) {
        var ticks = 0;
        final p = Poller(s, () async => ticks++);

        p.start();
        async.elapse(ms(14999));
        expect(ticks, 0);
        async.elapse(ms(1));
        expect(ticks, 1);
        p.stop();
      });
    });

    test('survives a tick that fails', () {
      fakeAsync((async) {
        var ticks = 0;
        final p = Poller(const Duration(seconds: 15), () async {
          ticks++;
          throw StateError('boom');
        });

        p.start();
        async.elapse(const Duration(seconds: 30));

        expect(ticks, 2);
        p.stop();
      });
    });
  });
}
