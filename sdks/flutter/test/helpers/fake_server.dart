// A dart:io HTTP server that records requests and answers them with queued
// replies or a handler per path.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

const evaluatePath = '/sdk/v1/evaluate';
const streamPath = '/sdk/v1/stream';

/// One received request; header names are lowercase.
class Received {
  Received(this.method, this.path, this.headers, this.body);

  final String method;
  final String path;
  final Map<String, String> headers;
  final String body;
}

/// A fixed answer: status, headers and a body.
class Reply {
  const Reply(this.status, {this.headers = const {}, this.body = ''});

  final int status;
  final Map<String, String> headers;
  final String body;
}

/// evaluated is a 200 evaluate answer with [results] and an optional ETag.
Reply evaluated(List<Map<String, Object?>> results, {String? etag}) => Reply(
  200,
  headers: {'Content-Type': 'application/json', 'ETag': ?etag},
  body: jsonEncode({'results': results}),
);

typedef Handler = Future<void> Function(HttpRequest request);

class FakeServer {
  FakeServer._(this._server) {
    _server.listen(_serve);
  }

  static Future<FakeServer> start() async =>
      FakeServer._(await HttpServer.bind(InternetAddress.loopbackIPv4, 0));

  final HttpServer _server;
  final _received = <Received>[];
  final _replies = <String, List<Reply>>{};
  final _handlers = <String, Handler>{};

  String get url => 'http://127.0.0.1:${_server.port}';

  /// received lists the requests to [path], oldest first.
  List<Received> received(String path) =>
      _received.where((r) => r.path == path).toList();

  /// reply queues [r] as the next answer to [path].
  void reply(String path, Reply r) => (_replies[path] ??= []).add(r);

  /// handle answers [path] with [h] when no reply is queued.
  void handle(String path, Handler h) => _handlers[path] = h;

  Future<void> stop() => _server.close(force: true);

  Future<void> _serve(HttpRequest req) async {
    final body = await utf8.decodeStream(req);
    final headers = <String, String>{};
    req.headers.forEach((name, values) => headers[name] = values.join(', '));
    _received.add(Received(req.method, req.uri.path, headers, body));
    final queued = _replies[req.uri.path];
    if (queued != null && queued.isNotEmpty) {
      final r = queued.removeAt(0);
      req.response.statusCode = r.status;
      r.headers.forEach(req.response.headers.set);
      req.response.add(utf8.encode(r.body));
      await req.response.close();
      return;
    }
    final h = _handlers[req.uri.path];
    if (h != null) {
      await h(req);
      return;
    }
    req.response.statusCode = 404;
    await req.response.close();
  }
}
