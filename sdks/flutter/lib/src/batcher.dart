// Sends the buffered exposures to POST /sdk/v1/events in the background: on
// an interval, at 1000 buffered items, on request (the app was hidden) and on
// close. No counts are ever sent: the server counts remote evaluations.
import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;

import 'backoff.dart';
import 'config.dart';
import 'exposures.dart';
import 'transport.dart';

/// The longest one request may take before it counts as failed and is
/// retried.
const _requestTimeout = Duration(seconds: 10);

/// Batcher sends through [_client], which it never closes. http.Client has
/// no per-request abort, so after stop a request still in flight is left to
/// finish and its answer is ignored.
class Batcher {
  /// [onFatal] runs once when the server refuses the key; the batcher has
  /// stopped. [random] jitters the retry backoff.
  Batcher(
    this._client, {
    required String baseUrl,
    required String sdkKey,
    required Exposures exposures,
    required Duration flushInterval,
    required MegooFlagsLogger logger,
    required void Function(FatalException error) onFatal,
    Random? random,
  }) : _url = Uri.parse('$baseUrl/sdk/v1/events'),
       _sdkKey = sdkKey,
       _exposures = exposures,
       _flushInterval = flushInterval,
       _log = logger,
       _onFatal = onFatal,
       _backoff = Backoff(
         const Duration(seconds: 1),
         const Duration(seconds: 60),
         random,
       ) {
    exposures.onFull = () => unawaited(flush());
  }

  final http.Client _client;
  final Uri _url;
  final String _sdkKey;
  final Exposures _exposures;
  final Duration _flushInterval;
  final MegooFlagsLogger _log;
  final void Function(FatalException error) _onFatal;
  final Backoff _backoff;
  Timer? _interval;

  /// While active, a flush sends nothing: a retry wait is running.
  Timer? _retry;
  Future<void>? _flushing;
  Future<void>? _closing;
  bool _stopped = false;

  /// start runs the flush timer; it is idempotent.
  void start() {
    if (_interval != null || _stopped || _closing != null) return;
    _interval = Timer.periodic(_flushInterval, (_) => unawaited(flush()));
  }

  /// flush sends the buffered exposures in batches of at most 1000, joining
  /// the flush in flight if there is one. It sends nothing while a retry
  /// wait runs and stops at the first batch that must be retried. It never
  /// fails.
  Future<void> flush() {
    // close sends the last batches itself.
    final closing = _closing;
    if (closing != null) return closing;
    return _flushing ??= _run(last: false).whenComplete(() => _flushing = null);
  }

  /// close stops the timers and flushes once, ignoring a retry wait, then
  /// stops for good; it completes within [timeout] even when a request never
  /// answers. It is idempotent.
  Future<void> close({Duration timeout = const Duration(seconds: 5)}) =>
      _closing ??= _close(timeout);

  Future<void> _close(Duration timeout) async {
    _clearTimers();
    final deadline = Completer<void>();
    final timer = Timer(timeout, deadline.complete);
    Future<void> last() async {
      await _flushing;
      await _run(last: true);
    }

    await Future.any([last(), deadline.future]);
    timer.cancel();
    stop();
  }

  /// stop ends the timers and sends nothing more.
  void stop() {
    _stopped = true;
    _clearTimers();
  }

  Future<void> _run({required bool last}) async {
    if (!last && (_retry?.isActive ?? false)) return;
    while (!_stopped && _exposures.size > 0) {
      final unsent = await _send(_exposures.drain(batchSize));
      if (unsent != null) {
        if (!_stopped) _exposures.restore(unsent);
        return;
      }
    }
  }

  /// _send posts [batch], splitting it on 413, and returns what must be
  /// retried, after scheduling the retry.
  Future<List<Exposure>?> _send(List<Exposure> batch) async {
    if (_stopped) return null;
    final body = jsonEncode({
      'exposures': [for (final e in batch) e.toJson()],
    });
    Object? err;
    try {
      final res = await _client
          .post(
            _url,
            headers: buildHeaders(
              sdkKey: _sdkKey,
              accept: 'application/json',
              json: true,
            ),
            body: body,
          )
          .timeout(_requestTimeout);
      // The http package lowercases response header names.
      err = statusError(
        res.statusCode,
        (name) => res.headers[name.toLowerCase()],
      );
    } catch (e) {
      err = e;
    }
    if (_stopped) return null;

    if (err == null) {
      _backoff.reset();
      _log(
        MegooFlagsLogLevel.debug,
        'megooflags: sent ${batch.length} exposures',
      );
      return null;
    }
    if (err is FatalException) {
      stop();
      _onFatal(err);
      return null;
    }
    if (err is HttpStatusException && err.status == 413) return _split(batch);
    if (err is HttpStatusException && err.status < 500) {
      _log(
        MegooFlagsLogLevel.warn,
        'megooflags: the server refused ${batch.length} exposures '
        '(HTTP ${err.status}); dropping them',
      );
      return null;
    }
    final wait = err is RetryAfterException ? err.wait : _backoff.next();
    _log(
      MegooFlagsLogLevel.debug,
      'megooflags: sending exposures failed (${_reason(err)}); will retry',
    );
    _retry?.cancel();
    _retry = Timer(wait, () => unawaited(flush()));
    return batch;
  }

  /// _split sends [batch] as two halves; a single item cannot be split and
  /// is dropped. When the first half must be retried, the second is kept
  /// behind it.
  Future<List<Exposure>?> _split(List<Exposure> batch) async {
    if (batch.length <= 1) {
      _log(
        MegooFlagsLogLevel.warn,
        'megooflags: the server refused an exposure as too large (HTTP 413); '
        'dropping it',
      );
      return null;
    }
    final half = batch.length ~/ 2;
    final second = batch.sublist(half);
    final unsent = await _send(batch.sublist(0, half));
    if (unsent != null) return [...unsent, ...second];
    return _send(second);
  }

  void _clearTimers() {
    _interval?.cancel();
    _retry?.cancel();
    _interval = null;
    _retry = null;
  }
}

/// _reason describes a failure for the debug log: an SDK error's message
/// without the prefix, else the error's type (a message could hold a host
/// name).
String _reason(Object e) => e is SdkException
    ? e.message.replaceFirst('megooflags: ', '')
    : '${e.runtimeType}';
