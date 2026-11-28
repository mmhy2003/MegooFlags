import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/exposures.dart';

const user1 = {'key': 'user-1', 'plan': 'pro'};

/// A clock the test moves; it starts at 2026-11-10T09:15:02Z.
class Clock {
  DateTime t = DateTime.utc(2026, 11, 10, 9, 15, 2);

  DateTime now() => t;

  void advance(Duration d) => t = t.add(d);
}

void main() {
  late Clock clock;
  late List<String> warnings;

  void logger(MegooFlagsLogLevel level, String message) {
    if (level == MegooFlagsLogLevel.warn) warnings.add(message);
  }

  Exposures make({
    bool sendContext = false,
    int maxKeys = 10000,
    int maxBuffer = 10000,
  }) => Exposures(
    sendContext: sendContext,
    logger: logger,
    now: clock.now,
    maxKeys: maxKeys,
    maxBuffer: maxBuffer,
  );

  List<String> flags(List<Exposure> items) => [
    for (final e in items) e.flagKey,
  ];

  setUp(() {
    clock = Clock();
    warnings = [];
  });

  test('records the same user, flag and variant once within the hour', () {
    final ex = make();

    ex.record('f', 'on', 'TARGETING_MATCH', user1);
    clock.advance(const Duration(minutes: 30));
    ex.record('f', 'on', 'TARGETING_MATCH', user1);

    expect(ex.size, 1);
  });

  test('records it again in the next UTC hour', () {
    final ex = make();

    ex.record('f', 'on', 'TARGETING_MATCH', user1);
    clock.advance(const Duration(minutes: 45));
    ex.record('f', 'on', 'TARGETING_MATCH', user1);

    expect(ex.size, 2);
  });

  test('records another variant, flag or user again', () {
    final ex = make();

    ex.record('f', 'on', 'TARGETING_MATCH', user1);
    ex.record('f', 'off', 'TARGETING_MATCH', user1);
    ex.record('g', 'on', 'TARGETING_MATCH', user1);
    ex.record('f', 'on', 'TARGETING_MATCH', {'key': 'user-2'});

    expect(ex.size, 4);
  });

  test('treats contexts without a targeting key as one user', () {
    final ex = make();

    ex.record('f', 'on', 'STATIC', {});
    ex.record('f', 'on', 'STATIC', {'plan': 'pro'});

    expect(ex.size, 1);
  });

  test('evicts the least recently seen key', () {
    final ex = make(maxKeys: 2);

    ex.record('a', 'on', 'STATIC', user1);
    ex.record('b', 'on', 'STATIC', user1);
    ex.record('a', 'on', 'STATIC', user1); // a is now the most recent
    ex.record('c', 'on', 'STATIC', user1); // evicts b
    ex.drain(10);
    ex.record('a', 'on', 'STATIC', user1);
    ex.record('b', 'on', 'STATIC', user1);

    expect(flags(ex.drain(10)), ['b']);
  });

  test('builds the exposure with an RFC 3339 UTC timestamp and no '
      'context', () {
    final ex = make();

    ex.record('f', 'on', 'TARGETING_MATCH', user1);

    expect(
      [for (final e in ex.drain(10)) e.toJson()],
      [
        {
          'flag_key': 'f',
          'variant': 'on',
          'reason': 'TARGETING_MATCH',
          'timestamp': '2026-11-10T09:15:02.000Z',
        },
      ],
    );
  });

  test('adds the context only with sendContext', () {
    final ex = make(sendContext: true);

    ex.record('f', 'on', 'TARGETING_MATCH', user1);

    expect(ex.drain(10).single.toJson()['context'], user1);
  });

  test('drains at most max items, oldest first', () {
    final ex = make();
    for (final f in ['a', 'b', 'c']) {
      ex.record(f, 'on', 'STATIC', user1);
    }

    expect(flags(ex.drain(2)), ['a', 'b']);
    expect(ex.size, 1);
    expect(flags(ex.drain(2)), ['c']);
  });

  test('puts items back in front of the buffer', () {
    final ex = make();
    for (final f in ['a', 'b', 'c']) {
      ex.record(f, 'on', 'STATIC', user1);
    }
    final batch = ex.drain(2);

    ex.restore(batch);

    expect(flags(ex.drain(10)), ['a', 'b', 'c']);
  });

  test('restore drops the newest items over the bound', () {
    final ex = make(maxBuffer: 2);
    ex.record('a', 'on', 'STATIC', user1);
    final batch = ex.drain(10);
    ex.record('b', 'on', 'STATIC', user1);
    ex.record('c', 'on', 'STATIC', user1);

    ex.restore(batch);

    expect(flags(ex.drain(10)), ['a', 'b']);
    expect(warnings, hasLength(1));
  });

  test('drops new items over the bound and warns once until the next '
      'drain', () {
    final ex = make(maxBuffer: 2);

    for (final f in ['a', 'b', 'c', 'd']) {
      ex.record(f, 'on', 'STATIC', user1);
    }

    expect(ex.size, 2);
    expect(warnings, [
      'megooflags: the exposure buffer is full (2); dropping new exposures',
    ]);
    expect(flags(ex.drain(10)), ['a', 'b']);

    for (final f in ['e', 'f', 'g']) {
      ex.record(f, 'on', 'STATIC', user1);
    }
    expect(warnings, hasLength(2));
  });

  test('records a dropped item again once there is room', () {
    final ex = make(maxBuffer: 1);

    ex.record('a', 'on', 'STATIC', user1);
    ex.record('b', 'on', 'STATIC', user1);
    ex.drain(10);
    ex.record('b', 'on', 'STATIC', user1);

    expect(flags(ex.drain(10)), ['b']);
  });

  test('calls onFull in a microtask once 1000 items are buffered', () {
    fakeAsync((async) {
      final ex = make();
      var calls = 0;
      ex.onFull = () => calls++;

      for (var i = 0; i < 999; i++) {
        ex.record('f$i', 'on', 'STATIC', user1);
      }
      async.flushMicrotasks();
      expect(calls, 0);
      ex.record('last', 'on', 'STATIC', user1);
      ex.record('more', 'on', 'STATIC', user1);
      expect(calls, 0);
      async.flushMicrotasks();

      expect(calls, 1);
    });
  });
}
