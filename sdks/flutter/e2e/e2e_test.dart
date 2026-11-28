// Runs MegooFlagsProvider through OpenFeatureAPI against a running MegooFlags
// stack: evaluation, toggles over the stream, a context change, exposures
// counted by the server and the cache serving values with the API
// unreachable. Set MEGOOFLAGS_E2E_URL (for example http://localhost:8080)
// and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) and run
// flutter test e2e.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:megooflags/megooflags.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';
import 'package:shared_preferences_platform_interface/in_memory_shared_preferences_async.dart';
import 'package:shared_preferences_platform_interface/shared_preferences_async_platform_interface.dart';

final _base = (Platform.environment['MEGOOFLAGS_E2E_URL'] ?? '').replaceFirst(
  RegExp(r'/+$'),
  '',
);
final _token = Platform.environment['MEGOOFLAGS_API_TOKEN'] ?? '';

/// Keeps every log line as "level message", for example
/// "debug megooflags: stream connected".
class CapturingLogger {
  final lines = <String>[];

  void call(MegooFlagsLogLevel level, String message) =>
      lines.add('${level.name} $message');

  int count(String text) => lines.where((l) => l.contains(text)).length;
}

/// api calls the management API with the API token and returns the status
/// and the decoded JSON object of the body.
Future<(int, Map<String, Object?>)> api(
  String method,
  String path, [
  Map<String, Object?>? body,
]) async {
  final req = http.Request(method, Uri.parse('$_base$path'))
    ..headers['Authorization'] = 'Bearer $_token'
    ..headers['Accept'] = 'application/json';
  if (body != null) {
    req.headers['Content-Type'] = 'application/json';
    req.body = jsonEncode(body);
  }
  final res = await http.Response.fromStream(
    await req.send().timeout(const Duration(seconds: 10)),
  );
  final json = res.body.trim().isEmpty
      ? <String, Object?>{}
      : jsonDecode(res.body) as Map<String, Object?>;
  return (res.statusCode, json);
}

/// mustApi calls api and fails unless the status is [want].
Future<Map<String, Object?>> mustApi(
  int want,
  String method,
  String path, [
  Map<String, Object?>? body,
]) async {
  final (status, json) = await api(method, path, body);
  if (status != want) {
    fail('$method $path: status $status, want $want: $json');
  }
  return json;
}

/// until polls [check] every [interval] until it holds or [timeout] passes.
Future<void> until(
  String what,
  Duration timeout,
  FutureOr<bool> Function() check, {
  Duration interval = const Duration(milliseconds: 50),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (!await check()) {
    if (DateTime.now().isAfter(deadline)) {
      fail('$what: not within ${timeout.inMilliseconds} ms');
    }
    await Future<void>.delayed(interval);
  }
}

/// closedPort returns a local port nothing listens on: the one the system
/// chose for a socket that is now closed.
Future<int> closedPort() async {
  final socket = await ServerSocket.bind('127.0.0.1', 0);
  final port = socket.port;
  await socket.close();
  return port;
}

String _randomHex(int n) {
  final r = Random.secure();
  return List.generate(n, (_) => r.nextInt(16).toRadixString(16)).join();
}

void main() {
  final skip = _base.isEmpty || _token.isEmpty
      ? 'set MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN'
      : null;
  final openFeature = OpenFeatureAPI.instance;
  String? project;

  setUpAll(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    // The test binding answers every HTTP request with 400; this test needs
    // the real network.
    HttpOverrides.global = null;
    // One in-memory store for both providers, so check 5 reads what the
    // first provider cached.
    SharedPreferencesAsyncPlatform.instance =
        InMemorySharedPreferencesAsync.empty();
  });

  tearDownAll(() async {
    await openFeature.shutdown();
    final p = project;
    if (p != null) await api('DELETE', '/api/projects/$p');
    SharedPreferencesAsyncPlatform.instance = null;
  });

  /// setUpProject creates project sdkflutter-<8 hex> with environment e2e,
  /// the boolean flag e2e-flag turned on (false for vip-user, true for
  /// everyone else) and a client key; it returns the flag's environment path
  /// and the key.
  Future<(String, String)> setUpProject() async {
    final key = 'sdkflutter-${_randomHex(8)}';
    final flagPath = '/api/projects/$key/flags/e2e-flag/environments/e2e';
    await mustApi(201, 'POST', '/api/projects', {
      'key': key,
      'name': key,
      'environments': [
        {'key': 'e2e', 'name': 'E2E'},
      ],
    });
    project = key;
    await mustApi(201, 'POST', '/api/projects/$key/flags', {
      'key': 'e2e-flag',
      'name': 'E2E',
      'type': 'boolean',
    });
    await mustApi(200, 'POST', '$flagPath/toggle', {'enabled': true});
    final config = await mustApi(200, 'GET', flagPath);
    await mustApi(200, 'PUT', flagPath, {
      'version': config['version'],
      'enabled': true,
      'targets': [
        {
          'variation': 'false',
          'values': ['vip-user'],
        },
      ],
      'rules': <Object?>[],
      'fallthrough': {'variation': 'true'},
      'prerequisites': <Object?>[],
    });
    final created = await mustApi(
      201,
      'POST',
      '/api/projects/$key/environments/e2e/sdk-keys',
      {'name': 'flutter-e2e', 'kind': 'client'},
    );
    final sdkKey = created['key'];
    if (sdkKey is! String || !sdkKey.startsWith('mf_client_')) {
      fail('POST sdk-keys returned no client key');
    }
    return (flagPath, sdkKey);
  }

  test(
    'evaluates, follows toggles and the context, sends exposures and serves '
    'the cache',
    () async {
      final (flagPath, sdkKey) = await setUpProject();
      final logger = CapturingLogger();
      final provider = MegooFlagsProvider(
        MegooFlagsConfig(
          sdkKey: sdkKey,
          baseUrl: _base,
          flushInterval: const Duration(seconds: 10),
          logger: logger.call,
        ),
      );
      var changes = 0;
      openFeature.addHandler(
        ProviderEventType.configurationChanged,
        (_) => changes++,
      );
      await openFeature.setEvaluationContextAndWait(
        EvaluationContext(targetingKey: 'user-1'),
      );
      await openFeature.setProviderAndWait(provider);
      final client = openFeature.getClient();

      // Check 1: the server evaluates the flag for the context.
      expect(client.getBooleanValue('e2e-flag', false), isTrue);

      // Check 2: toggles arrive over the stream within 5 s.
      await until(
        'the stream connected',
        const Duration(seconds: 5),
        () => logger.count('megooflags: stream connected') > 0,
      );
      await mustApi(200, 'POST', '$flagPath/toggle', {'enabled': false});
      await until(
        'e2e-flag false after the toggle',
        const Duration(seconds: 5),
        () => !client.getBooleanValue('e2e-flag', true),
      );
      await until(
        'a configuration-changed event',
        const Duration(seconds: 5),
        () => changes > 0,
      );
      await mustApi(200, 'POST', '$flagPath/toggle', {'enabled': true});
      await until(
        'e2e-flag true after the second toggle',
        const Duration(seconds: 5),
        () => client.getBooleanValue('e2e-flag', false),
      );

      // Check 3: a new targeting key is evaluated by the server.
      await openFeature.setEvaluationContextAndWait(
        EvaluationContext(targetingKey: 'vip-user'),
      );
      final vip = client.getBooleanDetails('e2e-flag', true);
      expect(vip.value, isFalse);
      expect(vip.reason, 'TARGETING_MATCH');

      // Check 4: exposures are sent at shutdown and counted.
      for (var i = 0; i < 5; i++) {
        client.getBooleanValue('e2e-flag', true);
      }
      await provider.shutdown();
      expect(
        logger.count('megooflags: sent '),
        greaterThan(0),
        reason: logger.lines.join('\n'),
      );
      await until(
        'evaluations total >= 1',
        const Duration(seconds: 10),
        () async {
          final counts = await mustApi(
            200,
            'GET',
            '$flagPath/evaluations?range=24h',
          );
          return ((counts['total'] as num?) ?? 0) >= 1;
        },
        interval: const Duration(milliseconds: 500),
      );

      // Check 5: with the API unreachable, a new provider serves the values
      // the first one left in the same store.
      final offline = MegooFlagsProvider(
        MegooFlagsConfig(
          sdkKey: sdkKey,
          baseUrl: 'http://127.0.0.1:${await closedPort()}',
          initTimeout: const Duration(seconds: 1),
          logger: CapturingLogger().call,
        ),
      );
      await openFeature.setProviderAndWait(offline);
      final cached = openFeature.getClient().getBooleanDetails(
        'e2e-flag',
        true,
      );
      expect(cached.value, isFalse);
      expect(cached.reason, 'CACHED');
    },
    skip: skip,
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
