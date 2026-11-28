// The polling fallback: refreshes the values on an interval while the stream
// is down, or always with disableStream.
import 'dart:async';

/// The shortest poll interval.
const _minInterval = Duration(seconds: 15);

class Poller {
  /// [tick] runs every [interval] (at least 15 s); a failure it returns is
  /// ignored.
  Poller(Duration interval, this._tick)
    : _interval = interval < _minInterval ? _minInterval : interval;

  final Duration _interval;
  final Future<void> Function() _tick;
  Timer? _timer;

  bool get running => _timer != null;

  /// start schedules the first tick one interval from now; it is idempotent.
  void start() {
    _timer ??= Timer.periodic(
      _interval,
      (_) => unawaited(_tick().then((_) {}, onError: (_) {})),
    );
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }
}
