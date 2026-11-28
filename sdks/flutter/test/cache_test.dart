import 'dart:convert';

import 'package:crypto/crypto.dart' as crypto;
import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/src/cache.dart';
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/store.dart';
import 'package:shared_preferences_platform_interface/in_memory_shared_preferences_async.dart';
import 'package:shared_preferences_platform_interface/shared_preferences_async_platform_interface.dart';

const key = 'mf_client_secret123';
final savedAt = DateTime.utc(2026, 11, 14, 12);

const results = [
  EvaluateResult(
    key: 'banner',
    value: 'blue',
    variant: 'blue',
    reason: 'TARGETING_MATCH',
  ),
  EvaluateResult(
    key: 'gone',
    value: false,
    variant: null,
    reason: 'ERROR',
    errorCode: 'FLAG_NOT_FOUND',
    errorMessage: 'no such flag',
  ),
];

StoredResults stored({String? etag = '"e1"', String hash = 'h1'}) =>
    StoredResults.fromList(
      results,
      etag: etag,
      source: Source.network,
      contextHash: hash,
      targetingKey: 'user-1',
    );

/// MemoryStore is a CacheStore in a map; [fail] makes every call throw.
class MemoryStore implements CacheStore {
  final values = <String, String>{};
  bool fail = false;

  void _check() {
    if (fail) throw StateError('storage broken');
  }

  @override
  Future<String?> get(String k) async {
    _check();
    return values[k];
  }

  @override
  Future<void> set(String k, String v) async {
    _check();
    values[k] = v;
  }

  @override
  Future<void> remove(String k) async {
    _check();
    values.remove(k);
  }
}

void expectResults(StoredResults? s) {
  expect(s, isNotNull);
  expect(s!.results.keys, ['banner', 'gone']);
  final gone = s.results['gone']!;
  expect(gone.value, false);
  expect(gone.variant, isNull);
  expect(gone.reason, 'ERROR');
  expect(gone.errorCode, 'FLAG_NOT_FOUND');
  expect(gone.errorMessage, 'no such flag');
  final banner = s.results['banner']!;
  expect(banner.value, 'blue');
  expect(banner.variant, 'blue');
  expect(banner.errorCode, isNull);
  expect(banner.errorMessage, isNull);
}

void main() {
  late MemoryStore store;
  late List<String> lines;
  late Cache cache;

  void logger(MegooFlagsLogLevel level, String message) =>
      lines.add('${level.name} $message');

  Cache make([CacheStore? s, String sdkKey = key]) =>
      Cache(sdkKey, s, logger, now: () => savedAt);

  setUp(() {
    store = MemoryStore();
    lines = [];
    cache = make(store);
  });

  test(
    'round-trips results, ETag and context hash with source cache',
    () async {
      await cache.save(stored());
      final loaded = await cache.load('h1');
      expectResults(loaded);
      expect(loaded!.etag, '"e1"');
      expect(loaded.source, Source.cache);
      expect(loaded.contextHash, 'h1');
      expect(loaded.targetingKey, isNull);
      expect(lines, isEmpty);
    },
  );

  test('keeps a null ETag', () async {
    await cache.save(stored(etag: null));
    final loaded = await cache.load('h1');
    expect(loaded, isNotNull);
    expect(loaded!.etag, isNull);
  });

  test('writes exactly the entry fields', () async {
    await cache.save(stored(etag: null));
    final entry = jsonDecode(store.values[cache.key]!) as Map<String, Object?>;
    expect(entry.keys.toSet(), {
      'v',
      'context_hash',
      'etag',
      'results',
      'saved_at',
    });
    expect(entry['v'], 1);
    expect(entry['context_hash'], 'h1');
    expect(entry['etag'], isNull);
    expect(entry['saved_at'], '2026-11-14T12:00:00.000Z');
    final items = entry['results'] as List<Object?>;
    expect(items, hasLength(2));
    expect((items.first as Map).keys.toSet(), {
      'key',
      'value',
      'variant',
      'reason',
      'error_code',
      'error_message',
    });
  });

  test('returns null for another context hash', () async {
    await cache.save(stored());
    expect(await cache.load('h2'), isNull);
  });

  test('returns null when nothing is stored', () async {
    expect(await cache.load('h1'), isNull);
    expect(lines, isEmpty);
  });

  test('returns null for a foreign version', () async {
    store.values[cache.key] =
        '{"v":2,"context_hash":"h1","etag":null,"results":[],"saved_at":"x"}';
    expect(await cache.load('h1'), isNull);
  });

  test('returns null for a corrupt entry', () async {
    for (final text in [
      '{not json',
      '[]',
      '{"v":1,"context_hash":"h1","etag":3,"results":[]}',
      '{"v":1,"context_hash":"h1","results":[]}',
      '{"v":1,"context_hash":"h1","etag":null}',
      '{"v":1,"context_hash":"h1","etag":null,"results":[{"key":1}]}',
    ]) {
      store.values[cache.key] = text;
      expect(await cache.load('h1'), isNull, reason: text);
    }
    expect(lines, hasLength(6));
    for (final line in lines) {
      expect(line, startsWith('debug megooflags: cache entry ignored: '));
    }
  });

  test('a failing read returns null and logs at debug', () async {
    store.fail = true;
    expect(await cache.load('h1'), isNull);
    expect(lines, ['debug megooflags: cache read failed: StateError']);
  });

  test('a failing write logs a warning and does not throw', () async {
    store.fail = true;
    await cache.save(stored());
    expect(lines, ['warn megooflags: cache write failed: StateError']);
    expect(lines.single, isNot(contains('secret123')));
  });

  test('a failing remove logs a warning and does not throw', () async {
    store.fail = true;
    await cache.remove();
    expect(lines, ['warn megooflags: cache remove failed: StateError']);
  });

  test('remove deletes the entry', () async {
    await cache.save(stored());
    await cache.remove();
    expect(await cache.load('h1'), isNull);
    expect(store.values, isEmpty);
    await cache.remove();
    expect(lines, isEmpty);
  });

  test('without a store nothing is read or written', () async {
    final off = make();
    await off.save(stored());
    expect(await off.load('h1'), isNull);
    await off.remove();
    expect(lines, isEmpty);
  });

  test('the key is megooflags- plus 16 hex and has no SDK key', () {
    expect(cache.key, matches(RegExp(r'^megooflags-[0-9a-f]{16}$')));
    expect(cache.key, isNot(contains('secret123')));
    final digest = crypto.sha256.convert(utf8.encode(key)).toString();
    expect(cache.key, 'megooflags-${digest.substring(0, 16)}');
  });

  test('the key differs per SDK key', () {
    expect(make(store, 'mf_client_other').key, isNot(cache.key));
  });

  group('SharedPreferencesCacheStore', () {
    late InMemorySharedPreferencesAsync prefs;

    setUp(() {
      prefs = InMemorySharedPreferencesAsync.empty();
      SharedPreferencesAsyncPlatform.instance = prefs;
    });

    tearDown(() {
      SharedPreferencesAsyncPlatform.instance = null;
    });

    test('gets, sets and removes strings', () async {
      final s = SharedPreferencesCacheStore();
      expect(await s.get('k'), isNull);
      await s.set('k', 'v');
      expect(await s.get('k'), 'v');
      await s.remove('k');
      expect(await s.get('k'), isNull);
    });

    test('backs a cache across instances', () async {
      await make(SharedPreferencesCacheStore()).save(stored());
      expectResults(await make(SharedPreferencesCacheStore()).load('h1'));
    });

    test('without a platform store the cache logs and returns null', () async {
      SharedPreferencesAsyncPlatform.instance = null;
      final c = make(SharedPreferencesCacheStore());
      expect(await c.load('h1'), isNull);
      await c.save(stored());
      expect(lines, [
        'debug megooflags: cache read failed: StateError',
        'warn megooflags: cache write failed: StateError',
      ]);
    });
  });
}
