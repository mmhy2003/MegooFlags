import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:megooflags/megooflags.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk_experimental.dart';
import 'package:shared_preferences_platform_interface/in_memory_shared_preferences_async.dart';
import 'package:shared_preferences_platform_interface/shared_preferences_async_platform_interface.dart';

import 'helpers/fake_server.dart';

const key = 'mf_client_secret123';
const eventsPath = '/sdk/v1/events';
final user1 = EvaluationContext(targetingKey: 'user-1');
final user2 = EvaluationContext(targetingKey: 'user-2');

Map<String, Object?> result({
  String key = 'f',
  Object? value = true,
  String? variant = 'on',
  String reason = 'TARGETING_MATCH',
}) => {
  'key': key,
  'value': value,
  'variant': variant,
  'reason': reason,
  'error_code': null,
  'error_message': null,
};

void silent(MegooFlagsLogLevel level, String message) {}

/// until waits for [done] to hold, checking every 10 ms for up to 5 s.
Future<void> until(bool Function() done) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!done()) {
    if (DateTime.now().isAfter(deadline)) fail('timed out waiting');
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
}

void main() {
  late FakeServer server;
  late OpenFeatureAPI api;
  late OpenFeatureClient client;
  final held = <Completer<void>>[];

  /// hold answers the next evaluate requests only when the returned
  /// completer completes, with [results].
  Completer<void> hold(List<Map<String, Object?>> results) {
    final release = Completer<void>();
    held.add(release);
    server.handle(evaluatePath, (req) async {
      await release.future;
      req.response.headers.contentType = ContentType.json;
      req.response.write(jsonEncode({'results': results}));
      await req.response.close();
    });
    return release;
  }

  MegooFlagsConfig config({
    String? bootstrap,
    bool disableStream = true,
    Duration initTimeout = const Duration(seconds: 5),
  }) => MegooFlagsConfig(
    sdkKey: key,
    baseUrl: server.url,
    bootstrap: bootstrap,
    disableStream: disableStream,
    initTimeout: initTimeout,
    flushInterval: const Duration(seconds: 60),
    logger: silent,
  );

  setUp(() async {
    SharedPreferencesAsyncPlatform.instance =
        InMemorySharedPreferencesAsync.empty();
    server = await FakeServer.start();
    server.handle(eventsPath, (req) async {
      req.response.statusCode = 202;
      await req.response.close();
    });
    api = createIsolatedOpenFeatureAPI();
    client = api.getClient();
  });

  tearDown(() async {
    for (final h in held) {
      if (!h.isCompleted) h.complete();
    }
    held.clear();
    await api.shutdown();
    await server.stop();
    SharedPreferencesAsyncPlatform.instance = null;
  });

  test('metadata names the provider', () {
    expect(MegooFlagsProvider(config()).metadata.name, 'MegooFlags');
  });

  test('serves the value the server evaluated', () async {
    server.reply(evaluatePath, evaluated([result()]));
    await api.setEvaluationContextAndWait(user1);
    await api.setProviderAndWait(MegooFlagsProvider(config()));

    final d = client.getBooleanDetails('f', false);
    expect(d.value, isTrue);
    expect(d.variant, 'on');
    expect(d.reason, 'TARGETING_MATCH');
    expect(client.providerStatus, ProviderStatus.ready);
    final sent = jsonDecode(server.received(evaluatePath).single.body);
    expect(sent, {
      'context': {'key': 'user-1'},
    });
    expect(client.getStringDetails('f', 'x').errorCode, ErrorCode.typeMismatch);
    expect(
      client.getIntegerDetails('missing', 3).errorCode,
      ErrorCode.flagNotFound,
    );
  });

  test('reads every type', () async {
    server.reply(
      evaluatePath,
      evaluated([
        result(key: 's', value: 'blue'),
        result(key: 'i', value: 7),
        result(key: 'd', value: 1.5),
        result(key: 'o', value: {'a': 1}),
      ]),
    );
    await api.setProviderAndWait(MegooFlagsProvider(config()));

    expect(client.getStringValue('s', ''), 'blue');
    expect(client.getIntegerValue('i', 0), 7);
    expect(client.getDoubleValue('d', 0), 1.5);
    expect(client.getStructureValue('o', const {}), {'a': 1});
  });

  test('bootstrap values are ready at once', () async {
    hold([result(value: false)]);
    await api.setProviderAndWait(
      MegooFlagsProvider(
        config(
          bootstrap: jsonEncode({
            'results': [result(reason: 'STATIC')],
          }),
        ),
      ),
    );

    expect(client.providerStatus, ProviderStatus.ready);
    final d = client.getBooleanDetails('f', false);
    expect(d.value, isTrue);
    expect(d.reason, 'STATIC');
  });

  test('a timeout fails with general, then ready follows', () async {
    final release = hold([result()]);
    final readies = <ProviderEventDetails>[];
    client.addHandler(ProviderEventType.ready, readies.add);

    await expectLater(
      api.setProviderAndWait(
        MegooFlagsProvider(
          config(initTimeout: const Duration(milliseconds: 100)),
        ),
      ),
      throwsA(
        isA<OpenFeatureException>().having(
          (e) => e.errorCode,
          'errorCode',
          ErrorCode.general,
        ),
      ),
    );
    expect(client.providerStatus, ProviderStatus.error);

    release.complete();
    await until(() => readies.isNotEmpty);
    expect(client.providerStatus, ProviderStatus.ready);
    expect(client.getBooleanValue('f', false), isTrue);
  });

  test('a stream change emits configurationChanged', () async {
    final stream = StreamController<String>();
    server.handle(streamPath, (req) async {
      // Unbuffered: dart:io otherwise holds the chunks until the close.
      req.response.bufferOutput = false;
      req.response.headers.contentType = ContentType('text', 'event-stream');
      await req.response.flush();
      await for (final chunk in stream.stream) {
        req.response.write(chunk);
        await req.response.flush();
      }
      await req.response.close();
    });
    server.reply(evaluatePath, evaluated([result()]));
    server.reply(evaluatePath, evaluated([result(value: false)]));
    final changes = <ProviderEventDetails>[];
    client.addHandler(ProviderEventType.configurationChanged, changes.add);

    await api.setProviderAndWait(
      MegooFlagsProvider(config(disableStream: false)),
    );
    await until(() => server.received(streamPath).isNotEmpty);
    stream.add('event: changed\nid: 1\ndata: {}\n\n');

    await until(() => changes.isNotEmpty);
    expect(changes.single.flagsChanged, ['f']);
    expect(client.getBooleanValue('f', true), isFalse);
    await stream.close();
  });

  test('a context change emits reconciling then contextChanged, with '
      'defaults while a new targeting key is pending', () async {
    server.reply(evaluatePath, evaluated([result()]));
    await api.setEvaluationContextAndWait(user1);
    await api.setProviderAndWait(MegooFlagsProvider(config()));
    final events = <ProviderEventType>[];
    for (final t in [
      ProviderEventType.reconciling,
      ProviderEventType.contextChanged,
    ]) {
      client.addHandler(t, (d) => events.add(d.type));
    }
    final release = hold([result(value: false, variant: 'off')]);

    final changing = api.setEvaluationContextAndWait(user2);
    await until(() => server.received(evaluatePath).length == 2);
    final pending = client.getBooleanDetails('f', true);
    expect(pending.value, isTrue);
    expect(pending.variant, isNull);
    expect(pending.reason, 'ERROR');

    release.complete();
    await changing;
    expect(events, [
      ProviderEventType.reconciling,
      ProviderEventType.contextChanged,
    ]);
    expect(client.getBooleanDetails('f', true).variant, 'off');
    expect(jsonDecode(server.received(evaluatePath).last.body), {
      'context': {'key': 'user-2'},
    });
  });

  test('a refused key emits error with providerFatal', () async {
    server.reply(evaluatePath, const Reply(401));
    final errors = <ProviderEventDetails>[];
    client.addHandler(ProviderEventType.error, errors.add);

    await expectLater(
      api.setProviderAndWait(MegooFlagsProvider(config())),
      throwsA(
        isA<OpenFeatureException>().having(
          (e) => e.errorCode,
          'errorCode',
          ErrorCode.providerFatal,
        ),
      ),
    );
    await until(() => errors.isNotEmpty);
    expect(errors.single.errorCode, ErrorCode.providerFatal);
    expect(errors.single.message, isNot(contains(key)));
    expect(client.providerStatus, ProviderStatus.fatal);
  });

  test('exposures are sent on shutdown', () async {
    server.reply(evaluatePath, evaluated([result()]));
    await api.setProviderAndWait(MegooFlagsProvider(config()));
    client.getBooleanValue('f', false);
    client.getBooleanValue('f', false);
    client.getBooleanValue('missing', false);

    await api.shutdown();

    final sent = server.received(eventsPath).single;
    expect(sent.headers['authorization'], 'Bearer $key');
    final exposures = (jsonDecode(sent.body) as Map)['exposures'] as List;
    expect(exposures, hasLength(1));
    expect(exposures.single, containsPair('flag_key', 'f'));
    expect(exposures.single, containsPair('variant', 'on'));
  });

  test('shutdown twice is safe', () async {
    server.reply(evaluatePath, evaluated([result()]));
    final provider = MegooFlagsProvider(config());
    final closed = Completer<void>();
    provider.events.listen(null, onDone: closed.complete);
    await api.setProviderAndWait(provider);

    await provider.shutdown();
    await provider.shutdown();
    await closed.future;
    expect(server.received(eventsPath), isEmpty);
  });
}
