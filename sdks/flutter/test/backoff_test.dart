import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/backoff.dart';

/// FixedRandom always draws [value] from nextDouble.
class FixedRandom implements Random {
  FixedRandom(this.value);

  final double value;

  @override
  double nextDouble() => value;

  @override
  bool nextBool() => throw UnimplementedError();

  @override
  int nextInt(int max) => throw UnimplementedError();
}

Duration s(num seconds) =>
    Duration(microseconds: (seconds * Duration.microsecondsPerSecond).round());

void main() {
  const minute = Duration(seconds: 60);

  test('first wait is base', () {
    expect(Backoff(s(1), minute, FixedRandom(0)).next(), s(1));
  });

  test('doubles from base up to max', () {
    final b = Backoff(s(3), minute, FixedRandom(1));
    final waits = [for (var i = 0; i < 7; i++) b.next()];
    expect(waits, [3, 6, 12, 24, 48, 60, 60].map(s).toList());
  });

  test('reset starts over', () {
    final b = Backoff(s(3), minute, FixedRandom(1));
    b.next();
    b.next();
    b.reset();
    expect(b.next(), s(3));
  });

  test('jitters from the second wait', () {
    final b = Backoff(s(3), minute, FixedRandom(0));
    expect(b.next(), s(3));
    expect(b.next(), s(4.8));
    expect(b.next(), s(9.6));
  });

  test('keeps jitter within [0.8, 1.0] of the doubled wait', () {
    final b = Backoff(s(1), minute);
    b.next();
    for (var n = 1; n < 6; n++) {
      final w = b.next();
      final full = s(min(60, pow(2, n)));
      expect(w, greaterThanOrEqualTo(full * 0.8));
      expect(w, lessThanOrEqualTo(full));
    }
  });

  test('first wait is never above max', () {
    expect(Backoff(s(90), minute).next(), minute);
  });

  test('never goes above max', () {
    final b = Backoff(s(3), minute);
    for (var i = 0; i < 200; i++) {
      final w = b.next();
      expect(w, greaterThan(Duration.zero));
      expect(w, lessThanOrEqualTo(minute));
    }
  });

  test('base can be replaced', () {
    final b = Backoff(s(3), minute, FixedRandom(1));
    b.base = s(1);
    expect(b.next(), s(1));
    expect(b.next(), s(2));
  });
}
