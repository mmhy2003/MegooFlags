// Jittered exponential backoff.
import 'dart:math';

/// Backoff computes waits. The first wait is min(base, max); the n-th is
/// min(max, base * 2^n) scaled by a factor in [0.8, 1.0]. base can be
/// replaced, for the stream's retry field.
class Backoff {
  Backoff(this.base, this.max, [Random? random]) : _random = random ?? Random();

  Duration base;
  final Duration max;
  final Random _random;
  int _n = 0;

  /// next returns the next wait and advances the attempt counter.
  Duration next() {
    if (_n == 0) {
      _n++;
      return base < max ? base : max;
    }
    final full = min(
      max.inMicroseconds.toDouble(),
      // pow(2, n) with an int base would overflow the VM's 64-bit int.
      base.inMicroseconds * pow(2.0, _n),
    );
    if (_n < 64) {
      _n++;
    }
    return Duration(
      microseconds: (full * (0.8 + 0.2 * _random.nextDouble())).round(),
    );
  }

  /// reset makes the next wait base again.
  void reset() {
    _n = 0;
  }
}
