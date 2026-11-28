// The last-known results on the device, one entry per SDK key. Every call
// is awaited and guarded, so a broken storage never fails an evaluation:
// reads return null and write failures are logged at warn.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'config.dart';
import 'context.dart';
import 'sha256.dart';
import 'store.dart';

const _version = 1;

/// The string storage the cache writes its entry to.
abstract interface class CacheStore {
  Future<String?> get(String k);
  Future<void> set(String k, String v);
  Future<void> remove(String k);
}

/// SharedPreferencesCacheStore keeps the entry in `shared_preferences`. The
/// SharedPreferencesAsync instance is created on the first call, so a
/// missing platform store fails that call (logged), not the construction.
class SharedPreferencesCacheStore implements CacheStore {
  SharedPreferencesAsync? _prefs;

  SharedPreferencesAsync get _p => _prefs ??= SharedPreferencesAsync();

  @override
  Future<String?> get(String k) => _p.getString(k);

  @override
  Future<void> set(String k, String v) => _p.setString(k, v);

  @override
  Future<void> remove(String k) => _p.remove(k);
}

/// Cache keeps the entry of one SDK key in [store]; a null store means no
/// cache (no reads, no writes). [now] is the time written as saved_at.
class Cache {
  Cache(
    String sdkKey,
    CacheStore? store,
    MegooFlagsLogger log, {
    DateTime Function()? now,
  }) : key = 'megooflags-${sha256Hex(sdkKey).substring(0, 16)}',
       _store = store,
       _log = log,
       _now = now ?? DateTime.now;

  /// The storage key: it never contains the SDK key itself.
  final String key;
  final CacheStore? _store;
  final MegooFlagsLogger _log;
  final DateTime Function() _now;

  /// load returns the entry for [contextHash] with source cache, or null when
  /// it is missing, unreadable, of another version or another context. The
  /// entry does not record the targeting key: the caller knows it from the
  /// context whose hash matched.
  Future<StoredResults?> load(String contextHash) async {
    final store = _store;
    if (store == null) return null;
    final String? raw;
    try {
      raw = await store.get(key);
    } catch (e) {
      _log(
        MegooFlagsLogLevel.debug,
        'megooflags: cache read failed: ${e.runtimeType}',
      );
      return null;
    }
    if (raw == null) return null;
    try {
      return _entry(raw, contextHash);
    } on FormatException catch (e) {
      _log(
        MegooFlagsLogLevel.debug,
        'megooflags: cache entry ignored: ${e.message}',
      );
      return null;
    }
  }

  StoredResults _entry(String raw, String contextHash) {
    Never fail(String why) => throw FormatException(why);
    final Object? entry;
    try {
      entry = jsonDecode(raw);
    } on FormatException {
      fail('not valid JSON');
    }
    if (entry is! Map<String, Object?>) fail('not an object');
    if (entry['v'] != _version) fail('version ${entry['v']}');
    if (entry['context_hash'] != contextHash) fail('another context');
    if (!entry.containsKey('etag')) fail('etag: missing');
    final etag = entry['etag'];
    if (etag != null && etag is! String) fail('etag: not a string or null');
    return StoredResults.fromList(
      parseResults(raw, 'invalid entry'),
      etag: etag as String?,
      source: Source.cache,
      contextHash: contextHash,
      targetingKey: null,
    );
  }

  /// save writes [s] as the entry; a failure is logged at warn.
  Future<void> save(StoredResults s) async {
    final store = _store;
    if (store == null) return;
    try {
      final entry = {
        'v': _version,
        'context_hash': s.contextHash,
        'etag': s.etag,
        'results': [
          for (final r in s.results.values)
            {
              'key': r.key,
              'value': r.value,
              'variant': r.variant,
              'reason': r.reason,
              'error_code': r.errorCode,
              'error_message': r.errorMessage,
            },
        ],
        'saved_at': formatUtcMillis(_now()),
      };
      await store.set(key, jsonEncode(entry));
    } catch (e) {
      _log(
        MegooFlagsLogLevel.warn,
        'megooflags: cache write failed: ${e.runtimeType}',
      );
    }
  }

  /// remove deletes the entry; a failure is logged at warn.
  Future<void> remove() async {
    final store = _store;
    if (store == null) return;
    try {
      await store.remove(key);
    } catch (e) {
      _log(
        MegooFlagsLogLevel.warn,
        'megooflags: cache remove failed: ${e.runtimeType}',
      );
    }
  }
}
