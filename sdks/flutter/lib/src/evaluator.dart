// Remote evaluation: posts the request context to /sdk/v1/evaluate and
// returns the evaluated results, or EvaluateNotModified when the ETag still
// matches.
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'store.dart';
import 'transport.dart';

/// What an evaluate call returned.
sealed class EvaluateOutcome {
  const EvaluateOutcome();
}

/// A 200: the results and the response's ETag.
final class EvaluateOk extends EvaluateOutcome {
  const EvaluateOk(this.results, this.etag);

  final List<EvaluateResult> results;
  final String? etag;
}

/// A 304: the stored results are still current.
final class EvaluateNotModified extends EvaluateOutcome {
  const EvaluateNotModified(this.etag);

  final String? etag;
}

/// Evaluator sends evaluate requests through [client], which it never
/// closes.
class Evaluator {
  Evaluator(this._client, {required String baseUrl, required String sdkKey})
    : _url = Uri.parse('$baseUrl/sdk/v1/evaluate'),
      _sdkKey = sdkKey;

  final http.Client _client;
  final Uri _url;
  final String _sdkKey;

  /// evaluate sends [body] as the context, with If-None-Match when [etag] is
  /// set. It throws the statusError of an unexpected status,
  /// InvalidResponseException for an unreadable 200 body, and the client's
  /// exception on a network failure.
  Future<EvaluateOutcome> evaluate(
    Map<String, Object?> body,
    String? etag,
  ) async {
    final res = await _client.post(
      _url,
      headers: buildHeaders(
        sdkKey: _sdkKey,
        accept: 'application/json',
        etag: etag,
        json: true,
      ),
      body: jsonEncode({'context': body}),
    );
    // The http package lowercases response header names.
    String? header(String name) => res.headers[name.toLowerCase()];
    final err = statusError(res.statusCode, header);
    if (err != null) throw err;
    if (res.statusCode == 304) {
      return EvaluateNotModified(header('ETag'));
    }
    try {
      // The server sends UTF-8; res.body would read the body by its
      // Content-Type charset.
      final text = utf8.decode(res.bodyBytes, allowMalformed: true);
      return EvaluateOk(parseResults(text, 'evaluate'), header('ETag'));
    } on FormatException catch (e) {
      throw InvalidResponseException(e.message);
    }
  }
}
