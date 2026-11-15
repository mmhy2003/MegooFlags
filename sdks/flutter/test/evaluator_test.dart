import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:megooflags/src/evaluator.dart';
import 'package:megooflags/src/store.dart';
import 'package:megooflags/src/transport.dart';
import 'package:megooflags/src/version.dart';

import 'helpers/fake_server.dart';

const key = 'mf_client_secret123';

const result = {
  'key': 'new-checkout',
  'value': true,
  'variant': 'on',
  'reason': 'TARGETING_MATCH',
  'error_code': null,
  'error_message': null,
};

void expectResult(EvaluateResult r) {
  expect(r.key, 'new-checkout');
  expect(r.value, true);
  expect(r.variant, 'on');
  expect(r.reason, 'TARGETING_MATCH');
  expect(r.errorCode, isNull);
  expect(r.errorMessage, isNull);
}

void main() {
  late FakeServer server;
  late http.Client client;
  late Evaluator evaluator;

  setUp(() async {
    server = await FakeServer.start();
    client = http.Client();
    evaluator = Evaluator(client, baseUrl: server.url, sdkKey: key);
  });

  tearDown(() async {
    client.close();
    await server.stop();
  });

  Future<Object> failure(Future<Object?> f) async {
    try {
      await f;
    } catch (e) {
      return e;
    }
    fail('no error');
  }

  test('returns the results and the ETag of a 200', () async {
    server.reply(evaluatePath, evaluated([result], etag: '"e1"'));

    final out = await evaluator.evaluate({'key': 'user-1'}, null);

    expect(out, isA<EvaluateOk>());
    final ok = out as EvaluateOk;
    expect(ok.etag, '"e1"');
    expect(ok.results, hasLength(1));
    expectResult(ok.results.single);
  });

  test('returns a null ETag when the response has none', () async {
    server.reply(evaluatePath, evaluated([result]));

    final out = await evaluator.evaluate({'key': 'user-1'}, null);

    expect((out as EvaluateOk).etag, isNull);
  });

  test('decodes the body as UTF-8 whatever the Content-Type', () async {
    server.reply(
      evaluatePath,
      Reply(
        200,
        headers: {'Content-Type': 'application/octet-stream'},
        body: jsonEncode({
          'results': [
            {...result, 'value': 'café 😀'},
          ],
        }),
      ),
    );

    final out = await evaluator.evaluate({}, null) as EvaluateOk;

    expect(out.results.single.value, 'café 😀');
  });

  test(
    'posts the context, without flags, with the key as a bearer token',
    () async {
      server.reply(evaluatePath, evaluated([]));

      await evaluator.evaluate({'key': 'user-1', 'plan': 'pro'}, null);

      final req = server.received(evaluatePath).single;
      expect(req.method, 'POST');
      expect(jsonDecode(req.body), {
        'context': {'key': 'user-1', 'plan': 'pro'},
      });
      expect(req.headers['authorization'], 'Bearer $key');
      expect(req.headers['content-type'], startsWith('application/json'));
      expect(req.headers['accept'], 'application/json');
      expect(
        req.headers['user-agent'],
        'megooflags-flutter/$megooFlagsVersion',
      );
      expect(req.headers.containsKey('if-none-match'), isFalse);
    },
  );

  test('sends If-None-Match when an ETag is given', () async {
    server.reply(evaluatePath, evaluated([]));

    await evaluator.evaluate({}, '"e1"');

    expect(
      server.received(evaluatePath).single.headers['if-none-match'],
      '"e1"',
    );
  });

  test('reports a 304 as not modified', () async {
    server.reply(evaluatePath, const Reply(304, headers: {'ETag': '"e1"'}));

    final out = await evaluator.evaluate({}, '"e1"');

    expect(out, isA<EvaluateNotModified>());
    expect((out as EvaluateNotModified).etag, '"e1"');
  });

  for (final status in [401, 403]) {
    test('throws FatalException on $status', () async {
      server.reply(evaluatePath, Reply(status));

      final err = await failure(evaluator.evaluate({}, null));

      expect(err, isA<FatalException>());
      expect((err as FatalException).status, status);
    });
  }

  test(
    'throws RetryAfterException with the Retry-After seconds on 429',
    () async {
      server.reply(
        evaluatePath,
        const Reply(429, headers: {'Retry-After': '7'}),
      );

      final err = await failure(evaluator.evaluate({}, null));

      expect(err, isA<RetryAfterException>());
      expect((err as RetryAfterException).wait, const Duration(seconds: 7));
    },
  );

  test('throws HttpStatusException on 503', () async {
    server.reply(evaluatePath, const Reply(503));

    final err = await failure(evaluator.evaluate({}, null));

    expect(err, isA<HttpStatusException>());
    expect((err as HttpStatusException).status, 503);
  });

  for (final (name, body) in [
    ('not JSON', '<html>'),
    ('results missing', '{}'),
    (
      'an invalid result',
      jsonEncode({
        'results': [
          {'key': 1},
        ],
      }),
    ),
  ]) {
    test('throws InvalidResponseException on a body with $name', () async {
      server.reply(evaluatePath, Reply(200, body: body));

      final err = await failure(evaluator.evaluate({}, null));

      expect(err, isA<InvalidResponseException>());
      final message = (err as InvalidResponseException).message;
      expect(message, startsWith('megooflags: invalid response: evaluate: '));
      expect(message, isNot(contains(key)));
    });
  }

  test('passes a network failure through', () async {
    await server.stop();

    final err = await failure(evaluator.evaluate({}, null));

    expect(err, isA<http.ClientException>());
  });
}
