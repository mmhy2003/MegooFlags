// HTTP helpers shared by the evaluate, stream and events requests: request
// headers, status mapping and Retry-After parsing. The SDK key travels in
// the Authorization header only, and no message ever contains it.
import 'package:flutter/foundation.dart' show kIsWeb;

import 'version.dart';

/// The ways an SDK request fails.
sealed class SdkException implements Exception {
  const SdkException();

  /// What went wrong, without the SDK key.
  String get message;

  @override
  String toString() => message;
}

/// The server refused the SDK key (401 or 403); retrying cannot succeed.
final class FatalException extends SdkException {
  const FatalException(this.status);

  final int? status;

  @override
  String get message => status == null
      ? 'megooflags: SDK key refused'
      : 'megooflags: SDK key refused ($status)';
}

/// The stream's revoked event: the key was revoked.
final class RevokedException extends SdkException {
  const RevokedException();

  @override
  String get message => 'megooflags: SDK key refused (revoked)';
}

/// A 429; [wait] is how long the server asked the SDK to wait.
final class RetryAfterException extends SdkException {
  const RetryAfterException(this.wait);

  final Duration wait;

  @override
  String get message =>
      'megooflags: rate limited, retry after ${wait.inSeconds}s';
}

/// Any other unexpected status.
final class HttpStatusException extends SdkException {
  const HttpStatusException(this.status);

  final int status;

  @override
  String get message => 'megooflags: HTTP $status';
}

/// A response the SDK could not read.
final class InvalidResponseException extends SdkException {
  const InvalidResponseException(this.why);

  final String why;

  @override
  String get message => 'megooflags: invalid response: $why';
}

/// buildHeaders returns the headers of an SDK request: the key as a bearer
/// token, Accept, Content-Type when [json] (a JSON body), If-None-Match and
/// Last-Event-ID when given, and the SDK's User-Agent unless [web] (browsers
/// forbid setting it).
Map<String, String> buildHeaders({
  required String sdkKey,
  required String accept,
  String? etag,
  String? lastEventId,
  bool json = false,
  bool web = kIsWeb,
}) => {
  'Authorization': 'Bearer $sdkKey',
  'Accept': accept,
  if (json) 'Content-Type': 'application/json',
  'If-None-Match': ?etag,
  'Last-Event-ID': ?lastEventId,
  if (!web) 'User-Agent': 'megooflags-flutter/$megooFlagsVersion',
};

/// statusError maps a response status to an error: null for 2xx and 304.
/// [header] reads a response header; [now] is the current time (default:
/// now), for an HTTP-date Retry-After.
SdkException? statusError(
  int status,
  String? Function(String name) header, {
  DateTime? now,
}) {
  if ((status >= 200 && status < 300) || status == 304) {
    return null;
  }
  if (status == 401 || status == 403) {
    return FatalException(status);
  }
  if (status == 429) {
    return RetryAfterException(
      parseRetryAfter(header('Retry-After'), now ?? DateTime.now()),
    );
  }
  return HttpStatusException(status);
}

/// The wait when Retry-After is missing or invalid.
const _defaultRetryAfter = Duration(seconds: 60);

final _deltaSeconds = RegExp(r'^[+-]?[0-9]+$');

/// parseRetryAfter reads a Retry-After value as delta-seconds or an
/// HTTP-date and returns whole seconds (a partial second rounds up). A
/// negative number or a date in the past is 0; a missing or invalid value
/// is 60 s.
Duration parseRetryAfter(String? value, DateTime now) {
  final v = value?.trim() ?? '';
  if (_deltaSeconds.hasMatch(v)) {
    final seconds = int.tryParse(v);
    if (seconds == null) return _defaultRetryAfter;
    return Duration(seconds: seconds < 0 ? 0 : seconds);
  }
  final at = _parseHttpDate(v);
  if (at == null) return _defaultRetryAfter;
  final wait = at.difference(now).inMilliseconds;
  return Duration(seconds: wait <= 0 ? 0 : (wait + 999) ~/ 1000);
}

const _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

// The three HTTP-date forms of RFC 9110 §5.6.7. dart:io's HttpDate is not
// available on the web.
final _imfFixdate = RegExp(
  r'^[A-Z][a-z]{2}, ([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{4}) '
  r'([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$',
);
final _rfc850 = RegExp(
  r'^[A-Z][a-z]+, ([0-9]{2})-([A-Z][a-z]{2})-([0-9]{2}) '
  r'([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$',
);
final _asctime = RegExp(
  r'^[A-Z][a-z]{2} ([A-Z][a-z]{2}) ([ 0-9][0-9]) '
  r'([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})$',
);

DateTime? _parseHttpDate(String v) {
  var m = _imfFixdate.firstMatch(v);
  if (m != null) {
    return _utc(m[3]!, m[2]!, m[1]!, m[4]!, m[5]!, m[6]!);
  }
  m = _rfc850.firstMatch(v);
  if (m != null) {
    // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx.
    final yy = int.parse(m[3]!);
    final year = yy >= 69 ? 1900 + yy : 2000 + yy;
    return _utc('$year', m[2]!, m[1]!, m[4]!, m[5]!, m[6]!);
  }
  m = _asctime.firstMatch(v);
  if (m != null) {
    return _utc(m[6]!, m[1]!, m[2]!, m[3]!, m[4]!, m[5]!);
  }
  return null;
}

DateTime? _utc(
  String year,
  String month,
  String day,
  String hour,
  String minute,
  String second,
) {
  final mon = _months.indexOf(month) + 1;
  final [y, d, h, mi, s] = [
    year,
    day,
    hour,
    minute,
    second,
  ].map((x) => int.parse(x.trim())).toList();
  if (mon == 0 || h > 23 || mi > 59 || s > 59) {
    return null;
  }
  final t = DateTime.utc(y, mon, d, h, mi, s);
  // A day the month does not have rolls over; refuse it.
  if (t.month != mon || t.day != d) {
    return null;
  }
  return t;
}
