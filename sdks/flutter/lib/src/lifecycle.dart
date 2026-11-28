// The app's background signal (spec/client/README.md, background signal):
// AppLifecycleListener tells visible from hidden, and the controller flushes,
// pauses and resumes the provider's delivery.
import 'dart:async';

import 'package:flutter/widgets.dart';

/// Whether the app is shown.
enum BackgroundState { visible, hidden }

/// Tells the provider when the app goes to the background and comes back.
abstract interface class LifecycleSource {
  /// subscribe calls [l] on every change and returns the function that
  /// unsubscribes it.
  void Function() subscribe(void Function(BackgroundState) l);
}

/// AppLifecycleSource follows the app through an AppLifecycleListener: hide
/// is hidden, show is visible. Without an initialised WidgetsBinding (a pure
/// Dart program, or before runApp) there is no signal.
class AppLifecycleSource implements LifecycleSource {
  @override
  void Function() subscribe(void Function(BackgroundState) l) {
    final WidgetsBinding binding;
    try {
      binding = WidgetsBinding.instance;
    } catch (_) {
      return () {};
    }
    final listener = AppLifecycleListener(
      binding: binding,
      onHide: () => l(BackgroundState.hidden),
      onShow: () => l(BackgroundState.visible),
    );
    return listener.dispose;
  }
}

/// How long the app stays hidden before the stream and the poller pause.
const _hiddenGrace = Duration(seconds: 30);

/// BackgroundController follows [source]: hidden flushes the exposures at
/// once and pauses delivery after 30 s hidden; visible cancels that pause,
/// or resumes what was paused. A failing flush is ignored.
class BackgroundController {
  BackgroundController({
    required LifecycleSource? source,
    required Future<void> Function() flush,
    required void Function() pause,
    required void Function() resume,
    Duration grace = _hiddenGrace,
  }) : _source = source,
       _flush = flush,
       _pause = pause,
       _resume = resume,
       _grace = grace;

  final LifecycleSource? _source;
  final Future<void> Function() _flush;
  final void Function() _pause;
  final void Function() _resume;
  final Duration _grace;
  void Function()? _unsubscribe;
  Timer? _timer;
  bool _paused = false;

  /// start follows the source; it is idempotent.
  void start() {
    final source = _source;
    if (source == null || _unsubscribe != null) return;
    _unsubscribe = source.subscribe(_on);
  }

  /// stop unsubscribes and cancels a pending pause.
  void stop() {
    _timer?.cancel();
    _timer = null;
    final unsubscribe = _unsubscribe;
    _unsubscribe = null;
    unsubscribe?.call();
  }

  void _on(BackgroundState state) {
    if (_unsubscribe == null) return;
    switch (state) {
      case BackgroundState.hidden:
        unawaited(_flush().then((_) {}, onError: (Object _) {}));
        if (_paused || _timer != null) return;
        _timer = Timer(_grace, () {
          _timer = null;
          _paused = true;
          _pause();
        });
      case BackgroundState.visible:
        _timer?.cancel();
        _timer = null;
        if (!_paused) return;
        _paused = false;
        _resume();
    }
  }
}
