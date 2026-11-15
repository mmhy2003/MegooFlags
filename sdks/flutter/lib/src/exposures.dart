// Deduplicated exposures waiting to be sent to POST /sdk/v1/events. Recording
// runs on the read path, so it never does I/O.
import 'dart:async';
import 'dart:collection';

import 'config.dart';
import 'context.dart';

/// Exposure records that a context saw a variant.
class Exposure {
  const Exposure({
    required this.flagKey,
    required this.variant,
    required this.reason,
    required this.timestamp,
    this.context,
  });

  final String flagKey;
  final String variant;
  final String reason;

  /// RFC 3339 UTC with milliseconds.
  final String timestamp;

  /// The request context, only with sendContext.
  final Map<String, Object?>? context;

  Map<String, Object?> toJson() => {
    'flag_key': flagKey,
    'variant': variant,
    'reason': reason,
    'timestamp': timestamp,
    'context': ?context,
  };
}

/// The buffered count that asks for an immediate flush.
const batchSize = 1000;

class Exposures {
  /// [now] is the clock of the timestamps and the dedup hour; [maxKeys]
  /// bounds the dedup keys and [maxBuffer] the waiting exposures.
  Exposures({
    required bool sendContext,
    required MegooFlagsLogger logger,
    DateTime Function()? now,
    int maxKeys = 10000,
    int maxBuffer = 10000,
  }) : _sendContext = sendContext,
       _log = logger,
       _now = now ?? DateTime.now,
       _maxKeys = maxKeys < 1 ? 1 : maxKeys,
       _maxBuffer = maxBuffer;

  /// Called in a microtask when the buffer reaches [batchSize] items; the
  /// batcher sets it to flush.
  void Function()? onFull;

  final bool _sendContext;
  final MegooFlagsLogger _log;
  final DateTime Function() _now;
  final int _maxKeys;
  final int _maxBuffer;

  /// The dedup keys, least recently seen first (a map literal is a
  /// LinkedHashMap: insertion order).
  final _seen = <String, bool>{};
  final _buffer = ListQueue<Exposure>();
  bool _warned = false;
  bool _fullScheduled = false;

  int get size => _buffer.length;

  /// record buffers an exposure unless the same targeting key saw this flag
  /// and variant in the current UTC hour. A full buffer drops it, warning
  /// once until the next drain.
  void record(
    String flag,
    String variant,
    String reason,
    Map<String, Object?> body,
  ) {
    final timestamp = formatUtcMillis(_now());
    final key =
        '${targetingKeyOf(body) ?? ''}|$flag|$variant|'
        '${timestamp.substring(0, 13)}';
    if (_seen.remove(key) != null) {
      _seen[key] = true;
      return;
    }
    if (_buffer.length >= _maxBuffer) {
      _warnFull();
      return;
    }
    _seen[key] = true;
    if (_seen.length > _maxKeys) _seen.remove(_seen.keys.first);
    _buffer.add(
      Exposure(
        flagKey: flag,
        variant: variant,
        reason: reason,
        timestamp: timestamp,
        context: _sendContext ? body : null,
      ),
    );
    if (_buffer.length >= batchSize && !_fullScheduled) {
      _fullScheduled = true;
      scheduleMicrotask(() {
        _fullScheduled = false;
        onFull?.call();
      });
    }
  }

  /// drain removes and returns up to [max] of the oldest exposures.
  List<Exposure> drain(int max) {
    _warned = false;
    return [
      for (var i = 0; i < max && _buffer.isNotEmpty; i++) _buffer.removeFirst(),
    ];
  }

  /// restore puts [items] that could not be sent back in front of the
  /// buffer; the newest items over the bound are dropped.
  void restore(List<Exposure> items) {
    items.reversed.forEach(_buffer.addFirst);
    if (_buffer.length > _maxBuffer) {
      while (_buffer.length > _maxBuffer) {
        _buffer.removeLast();
      }
      _warnFull();
    }
  }

  void _warnFull() {
    if (_warned) return;
    _warned = true;
    _log(
      MegooFlagsLogLevel.warn,
      'megooflags: the exposure buffer is full ($_maxBuffer); dropping new '
      'exposures',
    );
  }
}
