// The SSE client for GET /sdk/v1/stream: follows `changed` events and
// reconnects with backoff until it is paused, stopped or the key is refused.
import 'dart:async';
import 'dart:math';

import 'package:http/http.dart' as http;

import 'backoff.dart';
import 'config.dart';
import 'sse.dart';
import 'transport.dart';

/// The longest silence before the connection counts as dead.
const _deadTimeout = Duration(seconds: 45);

/// A connection that lasted this long resets the backoff.
const _stableAfter = Duration(seconds: 60);

/// The stream sent nothing, not even a comment, for 45 s.
final class DeadConnectionException implements Exception {
  const DeadConnectionException();

  String get message =>
      'megooflags: stream sent nothing for ${_deadTimeout.inSeconds}s '
      '(dead connection)';

  @override
  String toString() => message;
}

/// The server closed the stream.
final class StreamEndedException implements Exception {
  const StreamEndedException();

  String get message => 'megooflags: stream ended';

  @override
  String toString() => message;
}

/// What the streamer reports. The hooks may call pause and stop.
class StreamerHooks {
  const StreamerHooks({
    required this.onConnected,
    required this.onChanged,
    required this.onFatal,
    required this.onFailure,
  });

  /// Runs when the server answers 200.
  final void Function() onConnected;

  /// Receives the id of each `changed` event.
  final void Function(String id) onChanged;

  /// Runs once when the key is refused (FatalException or RevokedException);
  /// the streamer is done.
  final void Function(SdkException error) onFatal;

  /// Receives the failures since the last 200 (2 means: start polling) and
  /// the last error.
  final void Function(int consecutive, Object error) onFailure;
}

enum _State { idle, running, paused, done }

/// Streamer opens each connection with a client from [newClient] (default:
/// the configured httpClient, else a new http.Client). http.Client has no
/// per-request abort, so pause and stop cancel the body subscription and
/// close the connection's client, unless it is the configured httpClient,
/// which the streamer never closes. [random] jitters the backoff; [now] is
/// the clock of the 60 s backoff reset.
class Streamer {
  Streamer(
    MegooFlagsConfig config,
    this._hooks, {
    http.Client Function()? newClient,
    Random? random,
    DateTime Function()? now,
  }) : _url = Uri.parse('${config.baseUrl!}/sdk/v1/stream'),
       _sdkKey = config.sdkKey,
       _log = config.logger,
       _shared = config.httpClient,
       _newClient = newClient ?? (() => config.httpClient ?? http.Client()),
       _backoff = Backoff(
         const Duration(seconds: 3),
         const Duration(seconds: 60),
         random,
       ),
       _now = now ?? DateTime.now;

  final Uri _url;
  final String _sdkKey;
  final MegooFlagsLogger _log;
  final http.Client? _shared;
  final http.Client Function() _newClient;
  final StreamerHooks _hooks;
  final Backoff _backoff;
  final DateTime Function() _now;

  _State _state = _State.idle;
  _Session? _session;
  int _failures = 0;

  /// The id of the last `changed` event, sent as Last-Event-ID.
  String? get lastEventId => _lastEventId;
  String? _lastEventId;

  /// start opens the stream; only the first call does anything.
  void start() {
    if (_state == _State.idle) _run();
  }

  /// pause closes the connection and stops reconnecting until resume.
  void pause() {
    if (_state != _State.running) return;
    _state = _State.paused;
    _cancel();
  }

  /// resume reconnects at once after pause, with Last-Event-ID.
  void resume() {
    if (_state == _State.paused) _run();
  }

  /// stop closes the connection for good.
  void stop() {
    _state = _State.done;
    _cancel();
  }

  void _run() {
    _state = _State.running;
    final s = _Session();
    _session = s;
    unawaited(_loop(s));
  }

  void _cancel() {
    _session?.cancel();
    _session = null;
  }

  Future<void> _loop(_Session s) async {
    while (true) {
      final (connectedAt, err) = await _connect(s);
      if (s.cancelled) return;
      if (err is FatalException || err is RevokedException) {
        _state = _State.done;
        _cancel();
        _call(() => _hooks.onFatal(err as SdkException));
        return;
      }
      if (connectedAt != null &&
          _now().difference(connectedAt) >= _stableAfter) {
        _backoff.reset();
      }
      _failures++;
      var wait = _backoff.next();
      if (err is RetryAfterException && err.wait > wait) {
        wait = err.wait;
      }
      _log(
        MegooFlagsLogLevel.debug,
        'megooflags: stream failed: ${_reason(err)}',
      );
      final failures = _failures;
      _call(() => _hooks.onFailure(failures, err));
      if (s.cancelled || !await s.sleep(wait)) return;
    }
  }

  /// _connect opens one stream and reads it until it ends. It returns when
  /// the server answered 200 (null if it did not) and why the stream ended;
  /// it never throws.
  Future<(DateTime?, Object)> _connect(_Session s) async {
    final client = _newClient();
    final owned = !identical(client, _shared);
    if (owned) s.client = client;
    DateTime? connectedAt;
    Timer? dead;
    StreamSubscription<List<int>>? sub;
    try {
      final req = http.Request('GET', _url)
        ..headers.addAll(
          buildHeaders(
            sdkKey: _sdkKey,
            accept: 'text/event-stream',
            lastEventId: _lastEventId,
          ),
        );
      final res = await client.send(req);
      final bad = s.cancelled
          ? const _Cancelled()
          : statusError(
                  res.statusCode,
                  (name) => res.headers[name.toLowerCase()],
                  now: _now(),
                ) ??
                (res.statusCode == 200
                    ? null
                    : HttpStatusException(res.statusCode));
      if (bad != null) {
        _ignore(res.stream.listen(null).cancel());
        return (null, bad);
      }
      connectedAt = _now();
      _failures = 0;
      _log(MegooFlagsLogLevel.debug, 'megooflags: stream connected');
      _call(_hooks.onConnected);
      final ended = Completer<Object>();
      void end(Object why) {
        if (!ended.isCompleted) ended.complete(why);
      }

      void arm() {
        dead?.cancel();
        dead = Timer(_deadTimeout, () => end(const DeadConnectionException()));
      }

      s.end = end;
      if (s.cancelled) end(const _Cancelled());
      final parser = SseParser(
        onLine: arm,
        onRetry: (ms) => _backoff.base = Duration(milliseconds: ms),
        onEvent: (event) => _onEvent(s, event),
      );
      arm();
      sub = res.stream.listen(
        (chunk) {
          if (ended.isCompleted) return;
          try {
            parser.feed(chunk);
          } catch (e) {
            end(e);
          }
        },
        onError: end,
        onDone: () {
          parser.finish();
          end(const StreamEndedException());
        },
        cancelOnError: true,
      );
      return (connectedAt, await ended.future);
    } catch (e) {
      return (connectedAt, e);
    } finally {
      dead?.cancel();
      s.end = null;
      s.client = null;
      if (sub != null) _ignore(sub.cancel());
      if (owned) client.close();
    }
  }

  void _onEvent(_Session s, SseEvent event) {
    if (s.cancelled) return;
    switch (event.name) {
      case 'changed':
        if (event.id.isNotEmpty) _lastEventId = event.id;
        _call(() => _hooks.onChanged(event.id));
      case 'revoked':
        throw const RevokedException();
    }
  }

  /// _call runs a hook; an error it throws is logged, never propagated.
  void _call(void Function() hook) {
    try {
      hook();
    } catch (e) {
      _log(
        MegooFlagsLogLevel.error,
        'megooflags: stream callback failed: ${e.runtimeType}',
      );
    }
  }
}

/// One run of the loop, from start or resume to pause or stop.
class _Session {
  bool cancelled = false;

  /// The current connection's client when the streamer created it.
  http.Client? client;

  /// Ends the current read.
  void Function(Object why)? end;

  Timer? _timer;
  Completer<bool>? _wake;

  /// sleep waits [d]; false when the session was cancelled meanwhile.
  Future<bool> sleep(Duration d) {
    final wake = Completer<bool>();
    _wake = wake;
    _timer = Timer(d, () => wake.complete(true));
    return wake.future;
  }

  void cancel() {
    cancelled = true;
    client?.close();
    client = null;
    end?.call(const _Cancelled());
    _timer?.cancel();
    final wake = _wake;
    if (wake != null && !wake.isCompleted) wake.complete(false);
  }
}

/// The read ended because the session was cancelled.
final class _Cancelled implements Exception {
  const _Cancelled();
}

/// _reason describes a failure for the debug log: an SDK or stream error's
/// message without the prefix, else the error's type (a message could hold
/// a host name).
String _reason(Object e) {
  final message = switch (e) {
    SdkException(:final message) => message,
    DeadConnectionException(:final message) => message,
    StreamEndedException(:final message) => message,
    _ => null,
  };
  return message?.replaceFirst('megooflags: ', '') ?? '${e.runtimeType}';
}

void _ignore(Future<void> f) => unawaited(f.then((_) {}, onError: (_) {}));
