import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:megooflags/src/config.dart';
import 'package:megooflags/src/streamer.dart';
import 'package:megooflags/src/transport.dart';
import 'package:megooflags/src/version.dart';

import 'backoff_test.dart' show FixedRandom;
import 'helpers/fake_server.dart';

const key = 'mf_client_secret123';
const base = 'https://flags.example.com';

/// One stream the fake client opened; the test writes its body.
class FakeConn {
  FakeConn(this.request, this.client) {
    body = StreamController<List<int>>(onCancel: () => cancelled = true);
  }

  final http.BaseRequest request;
  final FakeClient client;
  late final StreamController<List<int>> body;
  bool cancelled = false;

  Map<String, String> get headers => request.headers;

  void write(String text) => body.add(utf8.encode(text));

  void end() => body.close();

  void fail(Object error) => body.addError(error);
}

/// FakeStreams answers stream requests in memory: 200 unless a status is
/// queued in [replies]; [gate], when set, holds the answer until completed;
/// [refuse] makes the next send throw.
class FakeStreams {
  final calls = <FakeConn>[];
  final clients = <FakeClient>[];
  final replies = <(int, Map<String, String>)>[];
  Completer<void>? gate;
  Object? refuse;

  FakeConn get last => calls.last;

  http.Client newClient() {
    final c = FakeClient(this);
    clients.add(c);
    return c;
  }

  Future<http.StreamedResponse> _send(
    http.BaseRequest request,
    FakeClient client,
  ) async {
    final g = gate;
    if (g != null) await g.future;
    final err = refuse;
    if (err != null) {
      refuse = null;
      throw err;
    }
    final (status, headers) = replies.isEmpty
        ? (200, const <String, String>{})
        : replies.removeAt(0);
    final conn = FakeConn(request, client);
    calls.add(conn);
    return http.StreamedResponse(
      conn.body.stream,
      status,
      headers: {for (final e in headers.entries) e.key.toLowerCase(): e.value},
      request: request,
    );
  }
}

class FakeClient extends http.BaseClient {
  FakeClient(this._streams);

  final FakeStreams _streams;
  bool closed = false;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      _streams._send(request, this);

  @override
  void close() => closed = true;
}

/// Hooks records what the streamer reports.
class Hooks {
  final log = <List<Object?>>[];
  void Function(String id)? onChanged;

  List<Object?> get names => [for (final e in log) e[0]];

  StreamerHooks get hooks => StreamerHooks(
    onConnected: () => log.add(['connected']),
    onChanged: (id) {
      log.add(['changed', id]);
      onChanged?.call(id);
    },
    onFatal: (e) => log.add(['fatal', e]),
    onFailure: (n, e) => log.add(['failure', n, e]),
  );
}

void main() {
  late FakeStreams streams;
  late Hooks hooks;
  late List<String> lines;
  late Streamer streamer;

  void logger(MegooFlagsLogLevel level, String message) =>
      lines.add('${level.name} $message');

  MegooFlagsConfig config({String baseUrl = base, http.Client? httpClient}) =>
      MegooFlagsConfig(
        sdkKey: key,
        baseUrl: baseUrl,
        logger: logger,
        httpClient: httpClient,
      );

  Streamer make(FakeAsync async, {double random = 1}) => streamer = Streamer(
    config(),
    hooks.hooks,
    newClient: streams.newClient,
    random: FixedRandom(random),
    now: () => DateTime.utc(2026).add(async.elapsed),
  );

  setUp(() {
    streams = FakeStreams();
    hooks = Hooks();
    lines = [];
  });

  /// run runs [body] in fake time and stops the streamer afterwards.
  void run(void Function(FakeAsync async) body) => fakeAsync((async) {
    make(async);
    body(async);
    streamer.stop();
    async.flushMicrotasks();
  });

  group('in fake time', () {
    test('opens the stream with the SDK headers and reports it', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();

        expect(streams.calls, hasLength(1));
        final req = streams.last.request;
        expect(req.method, 'GET');
        expect(req.url.toString(), '$base/sdk/v1/stream');
        expect(streams.last.headers, {
          'Authorization': 'Bearer $key',
          'Accept': 'text/event-stream',
          'User-Agent': 'megooflags-flutter/$megooFlagsVersion',
        });
        expect(hooks.log, [
          ['connected'],
        ]);
        expect(lines, contains('debug megooflags: stream connected'));
      });
    });

    test('a second start opens nothing', () {
      run((async) {
        streamer.start();
        streamer.start();
        async.flushMicrotasks();
        expect(streams.calls, hasLength(1));
      });
    });

    test('calls onChanged with the id and resumes from it on reconnect', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: changed\nid: "e2"\ndata: {"etag":"\\"e2');
        streams.last.write('\\""}\n\n');
        async.flushMicrotasks();

        expect(hooks.log, contains(equals(['changed', '"e2"'])));
        expect(streamer.lastEventId, '"e2"');

        streams.last.end();
        async.elapse(const Duration(seconds: 3));
        expect(streams.calls, hasLength(2));
        expect(streams.last.headers['Last-Event-ID'], '"e2"');
      });
    });

    test('ignores unknown events and comments', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: other\ndata: x\n\n: ping\n\ndata: y\n\n');
        async.flushMicrotasks();

        expect(hooks.names, ['connected']);
        expect(streamer.lastEventId, isNull);
        expect(streams.calls, hasLength(1));
      });
    });

    test('retry: sets the next wait', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('retry: 5000\n\n');
        streams.last.end();
        async.elapse(const Duration(milliseconds: 4999));
        expect(streams.calls, hasLength(1));
        async.elapse(const Duration(milliseconds: 1));
        expect(streams.calls, hasLength(2));
      });
    });

    test('retry: never waits less than 1 s', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('retry: 0\n\n');
        streams.last.end();
        async.elapse(const Duration(milliseconds: 999));
        expect(streams.calls, hasLength(1));
        async.elapse(const Duration(milliseconds: 1));
        expect(streams.calls, hasLength(2));
      });
    });

    test('reconnects after 45 s without a line', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        final first = streams.last;
        async.elapse(const Duration(milliseconds: 44999));
        expect(first.cancelled, isFalse);
        async.elapse(const Duration(milliseconds: 1));
        expect(first.cancelled, isTrue);
        expect(first.client.closed, isTrue);
        expect(hooks.log[1].sublist(0, 2), ['failure', 1]);
        expect(hooks.log[1][2], isA<DeadConnectionException>());
        expect(
          lines,
          contains(
            'debug megooflags: stream failed: stream sent nothing for 45s '
            '(dead connection)',
          ),
        );
        async.elapse(const Duration(seconds: 3));
        expect(streams.calls, hasLength(2));
      });
    });

    test('a line every 15 s keeps the connection open', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        for (var i = 0; i < 6; i++) {
          async.elapse(const Duration(seconds: 15));
          streams.last.write(': ping\n');
        }
        async.elapse(const Duration(seconds: 44));
        expect(streams.calls, hasLength(1));
        expect(hooks.names, ['connected']);
      });
    });

    test('backs off 3 s, 6 s, 12 s … up to 60 s', () {
      run((async) {
        streams.replies.addAll(List.filled(8, (500, const {})));
        streamer.start();
        async.flushMicrotasks();
        expect(streams.calls, hasLength(1));
        for (final wait in [3, 6, 12, 24, 48, 60, 60]) {
          final before = streams.calls.length;
          async.elapse(Duration(milliseconds: wait * 1000 - 1));
          expect(streams.calls, hasLength(before), reason: '$wait s');
          async.elapse(const Duration(milliseconds: 1));
          expect(streams.calls, hasLength(before + 1), reason: '$wait s');
        }
      });
    });

    test('jitters later waits between 80 % and 100 %', () {
      fakeAsync((async) {
        make(async, random: 0);
        streams.replies.addAll([(500, const {}), (500, const {})]);
        streamer.start();
        async.elapse(const Duration(seconds: 3));
        expect(streams.calls, hasLength(2));
        async.elapse(const Duration(milliseconds: 4799));
        expect(streams.calls, hasLength(2));
        async.elapse(const Duration(milliseconds: 1));
        expect(streams.calls, hasLength(3));
        streamer.stop();
      });
    });

    test('resets the backoff after a connection that lasted 60 s', () {
      run((async) {
        streams.replies.addAll([(500, const {}), (500, const {})]);
        streamer.start();
        async.elapse(const Duration(seconds: 3 + 6));
        expect(streams.calls, hasLength(3));
        for (var i = 0; i < 4; i++) {
          async.elapse(const Duration(seconds: 15));
          streams.last.write(': ping\n');
        }
        streams.last.end();
        async.elapse(const Duration(seconds: 3));
        expect(streams.calls, hasLength(4));
      });
    });

    test('keeps the backoff after a short connection', () {
      run((async) {
        streams.replies.addAll([(500, const {}), (500, const {})]);
        streamer.start();
        async.elapse(const Duration(seconds: 3 + 6));
        streams.last.end();
        async.elapse(const Duration(milliseconds: 11999));
        expect(streams.calls, hasLength(3));
        async.elapse(const Duration(milliseconds: 1));
        expect(streams.calls, hasLength(4));
      });
    });

    test('counts consecutive failures; the count resets on connect', () {
      run((async) {
        streams.replies.addAll([(500, const {}), (503, const {})]);
        streamer.start();
        async.elapse(const Duration(seconds: 3 + 6));
        streams.last.end();
        async.flushMicrotasks();

        final failures = hooks.log.where((e) => e[0] == 'failure').toList();
        expect(hooks.names, ['failure', 'failure', 'connected', 'failure']);
        expect([for (final f in failures) f[1]], [1, 2, 1]);
        expect((failures[0][2]! as HttpStatusException).status, 500);
        expect((failures[1][2]! as HttpStatusException).status, 503);
        expect(failures[2][2], isA<StreamEndedException>());
        expect(lines, contains('debug megooflags: stream failed: HTTP 500'));
        expect(
          lines,
          contains('debug megooflags: stream failed: stream ended'),
        );
      });
    });

    test('a read error is a failure', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.fail(const SocketException('reset'));
        async.flushMicrotasks();
        expect(hooks.log[1].sublist(0, 2), ['failure', 1]);
        expect(hooks.log[1][2], isA<SocketException>());
        expect(
          lines,
          contains('debug megooflags: stream failed: SocketException'),
        );
      });
    });

    test('a send that gets no response headers in 45 s is a failure', () {
      run((async) {
        streams.gate = Completer<void>();
        streamer.start();
        async.elapse(const Duration(milliseconds: 44999));
        expect(hooks.log, isEmpty);
        async.elapse(const Duration(milliseconds: 1));

        expect(hooks.log[0].sublist(0, 2), ['failure', 1]);
        expect(hooks.log[0][2], isA<TimeoutException>());
        expect(streams.clients.single.closed, isTrue);
        streams.gate = null;
        async.elapse(const Duration(seconds: 3));
        expect(hooks.names, ['failure', 'connected']);
      });
    });

    test('a send that throws is a failure', () {
      run((async) {
        streams.refuse = http.ClientException('offline');
        streamer.start();
        async.flushMicrotasks();
        expect(hooks.log[0].sublist(0, 2), ['failure', 1]);
        expect(hooks.log[0][2], isA<http.ClientException>());
        expect(streams.clients.single.closed, isTrue);
        async.elapse(const Duration(seconds: 3));
        expect(hooks.names, ['failure', 'connected']);
      });
    });

    for (final status in [401, 403]) {
      test('$status calls onFatal once and never reconnects', () {
        run((async) {
          streams.replies.add((status, const {}));
          streamer.start();
          async.elapse(const Duration(minutes: 10));
          expect(hooks.log, hasLength(1));
          expect(hooks.log[0][0], 'fatal');
          expect((hooks.log[0][1]! as FatalException).status, status);
          expect(streams.calls, hasLength(1));
          expect(streams.last.cancelled, isTrue);
          expect(streams.last.client.closed, isTrue);
        });
      });
    }

    test('revoked calls onFatal once and never reconnects', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: revoked\ndata: {}\n\n');
        async.elapse(const Duration(minutes: 10));
        expect(hooks.names, ['connected', 'fatal']);
        expect(hooks.log[1][1], isA<RevokedException>());
        expect(streams.calls, hasLength(1));
        expect(streams.last.cancelled, isTrue);
      });
    });

    test('429 waits Retry-After seconds', () {
      run((async) {
        streams.replies.add((429, const {'Retry-After': '20'}));
        streamer.start();
        async.flushMicrotasks();
        expect(hooks.log[0][2], isA<RetryAfterException>());
        async.elapse(const Duration(milliseconds: 19999));
        expect(streams.calls, hasLength(1));
        async.elapse(const Duration(milliseconds: 1));
        expect(streams.calls, hasLength(2));
      });
    });

    test('pause closes the connection and does not reconnect', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streamer.pause();
        async.flushMicrotasks();
        expect(streams.last.cancelled, isTrue);
        expect(streams.last.client.closed, isTrue);
        async.elapse(const Duration(minutes: 10));
        expect(streams.calls, hasLength(1));
        expect(hooks.names, ['connected']);
      });
    });

    test('pause during a backoff wait cancels the reconnect', () {
      run((async) {
        streams.replies.add((500, const {}));
        streamer.start();
        async.flushMicrotasks();
        streamer.pause();
        async.elapse(const Duration(minutes: 10));
        expect(streams.calls, hasLength(1));
      });
    });

    test('resume opens at once with Last-Event-ID', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: changed\nid: e7\ndata: {}\n\n');
        async.flushMicrotasks();
        streamer.pause();
        async.elapse(const Duration(seconds: 60));
        streamer.resume();
        async.flushMicrotasks();
        expect(streams.calls, hasLength(2));
        expect(streams.last.headers['Last-Event-ID'], 'e7');
        expect(hooks.names, ['connected', 'changed', 'connected']);
      });
    });

    test('resume without pause opens nothing', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streamer.resume();
        async.flushMicrotasks();
        expect(streams.calls, hasLength(1));
      });
    });

    test('stop is final', () {
      run((async) {
        streamer.start();
        async.flushMicrotasks();
        streamer.stop();
        streamer.resume();
        streamer.start();
        async.elapse(const Duration(minutes: 10));
        expect(streams.calls, hasLength(1));
        expect(streams.last.cancelled, isTrue);
        expect(hooks.names, ['connected']);
      });
    });

    test('stop before the answer runs no hook and closes the stream', () {
      run((async) {
        streams.gate = Completer();
        streamer.start();
        async.flushMicrotasks();
        streamer.stop();
        expect(streams.clients.single.closed, isTrue);
        streams.gate!.complete();
        async.elapse(const Duration(minutes: 10));
        expect(hooks.log, isEmpty);
        expect(streams.calls, hasLength(1));
        expect(streams.last.cancelled, isTrue);
      });
    });

    test('a hook that throws is logged and the stream goes on', () {
      run((async) {
        hooks.onChanged = (_) => throw StateError('boom');
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: changed\nid: e1\ndata: {}\n\n');
        async.flushMicrotasks();
        expect(
          lines,
          contains('error megooflags: stream callback failed: StateError'),
        );
        expect(streams.calls, hasLength(1));
        expect(streams.last.cancelled, isFalse);
      });
    });

    test('a hook may pause the streamer', () {
      run((async) {
        hooks.onChanged = (_) => streamer.pause();
        streamer.start();
        async.flushMicrotasks();
        streams.last.write('event: changed\nid: e1\ndata: {}\n\n');
        async.elapse(const Duration(minutes: 10));
        expect(streams.calls, hasLength(1));
        expect(streams.last.cancelled, isTrue);
        expect(hooks.names, ['connected', 'changed']);
      });
    });

    test('never closes the configured httpClient', () {
      fakeAsync((async) {
        final shared = FakeClient(streams);
        streamer = Streamer(
          config(httpClient: shared),
          hooks.hooks,
          random: FixedRandom(1),
        );
        streamer.start();
        async.flushMicrotasks();
        expect(streams.last.client, same(shared));
        streamer.pause();
        async.flushMicrotasks();
        expect(streams.last.cancelled, isTrue);
        expect(shared.closed, isFalse);
        streamer.stop();
      });
    });
  });

  group('against an HTTP server', () {
    late FakeServer server;
    late List<HttpRequest> requests;
    late List<Completer<void>> gone;

    /// The server keeps each stream open, sends retry: 20 so reconnects are
    /// quick, and completes gone[i] when stream i's client disconnects. The
    /// body is read until the connection closes, so the test writes it on
    /// the detached socket and sees the close.
    setUp(() async {
      server = await FakeServer.start();
      requests = [];
      gone = [];
      server.handle(streamPath, (req) async {
        requests.add(req);
        final closed = Completer<void>();
        gone.add(closed);
        req.response
          ..persistentConnection = false
          ..headers.chunkedTransferEncoding = false
          ..headers.contentType = ContentType('text', 'event-stream');
        final socket = await req.response.detachSocket();
        void done() {
          if (!closed.isCompleted) closed.complete();
        }

        socket.listen(null, onDone: done, onError: (Object _) => done());
        socket.write('retry: 20\n\n');
      });
    });

    tearDown(() async {
      streamer.stop();
      await server.stop();
    });

    Future<void> until(bool Function() cond) async {
      final deadline = DateTime.now().add(const Duration(seconds: 5));
      while (!cond()) {
        if (DateTime.now().isAfter(deadline)) fail('timed out: ${hooks.log}');
        await Future<void>.delayed(const Duration(milliseconds: 5));
      }
    }

    Streamer real({http.Client? httpClient}) => streamer = Streamer(
      config(baseUrl: server.url, httpClient: httpClient),
      hooks.hooks,
    );

    test('reads chunked events and sends the SDK headers', () async {
      server.handle(streamPath, (req) async {
        requests.add(req);
        req.response.bufferOutput = false;
        req.response.write('event: chan');
        await req.response.flush();
        await Future<void>.delayed(const Duration(milliseconds: 20));
        req.response.write('ged\nid: e1\ndata: {}\n');
        await req.response.flush();
        await Future<void>.delayed(const Duration(milliseconds: 20));
        req.response.write('\n');
        await req.response.flush();
      });
      real().start();
      await until(() => hooks.names.contains('changed'));

      expect(hooks.log, [
        ['connected'],
        ['changed', 'e1'],
      ]);
      final h = requests.single.headers;
      expect(h.value('authorization'), 'Bearer $key');
      expect(h.value('accept'), 'text/event-stream');
      expect(h.value('user-agent'), 'megooflags-flutter/$megooFlagsVersion');
      expect(h.value('last-event-id'), isNull);
    });

    test('reconnects with Last-Event-ID after the server ends', () async {
      var n = 0;
      server.handle(streamPath, (req) async {
        requests.add(req);
        if (n++ == 0) {
          req.response.write(
            'retry: 20\n\nevent: changed\nid: e9\ndata: {}\n\n',
          );
          await req.response.close();
        } else {
          // Sends the status and headers and keeps the stream open.
          req.response.write(': open\n');
          await req.response.flush();
        }
      });
      real().start();
      await until(() => hooks.names.length == 4);

      expect(requests[1].headers.value('last-event-id'), 'e9');
      expect(hooks.names, ['connected', 'changed', 'failure', 'connected']);
    });

    test('pause closes the connection; resume reconnects', () async {
      real().start();
      await until(() => hooks.names.contains('connected'));
      streamer.pause();
      await gone[0].future.timeout(const Duration(seconds: 5));
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(requests, hasLength(1));

      streamer.resume();
      await until(() => hooks.names.length == 2);
      expect(hooks.names, ['connected', 'connected']);
    });

    test('pause leaves the configured httpClient usable', () async {
      final shared = http.Client();
      addTearDown(shared.close);
      server.reply(evaluatePath, evaluated([]));
      real(httpClient: shared).start();
      await until(() => hooks.names.contains('connected'));
      streamer.pause();
      await gone[0].future.timeout(const Duration(seconds: 5));

      final res = await shared.post(Uri.parse('${server.url}$evaluatePath'));
      expect(res.statusCode, 200);
    });

    test('401 is fatal and opens nothing more', () async {
      server.reply(streamPath, const Reply(401));
      real().start();
      await until(() => hooks.names.contains('fatal'));
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(server.received(streamPath), hasLength(1));
      expect(hooks.names, ['fatal']);
    });
  });
}
