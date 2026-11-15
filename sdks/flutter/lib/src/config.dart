// The provider's configuration, its validation and defaults, and the logger.
// Messages never contain the SDK key.
import 'dart:developer' as developer;

import 'package:http/http.dart' as http;

import 'store.dart';

const _minPollInterval = Duration(seconds: 15);
const _minFlushInterval = Duration(seconds: 10);
const _maxFlushInterval = Duration(seconds: 60);

/// How a MegooFlagsProvider connects and behaves. The constructor checks the
/// options and throws ArgumentError when one is invalid.
class MegooFlagsConfig {
  /// [sdkKey] is a client SDK key (mf_client_…). [baseUrl] is the MegooFlags
  /// server, an http or https URL, required unless [offline]. [bootstrap] is
  /// the body of an evaluate response (`{"results": [...]}`) as JSON text,
  /// served until there are newer values. [offline] means no network and no
  /// events, and needs bootstrap or a cache entry. [disableStream] polls
  /// instead of following the stream. [pollInterval] is raised to 15 seconds
  /// when smaller; [flushInterval] (how often exposures are sent) is clamped
  /// to 10…60 seconds. [sendContext] sends the context with exposures.
  /// [httpClient] is used for every request.
  MegooFlagsConfig({
    required String sdkKey,
    String? baseUrl,
    String? bootstrap,
    this.disableCache = false,
    this.offline = false,
    this.initTimeout = const Duration(seconds: 5),
    this.disableStream = false,
    Duration pollInterval = const Duration(seconds: 30),
    Duration flushInterval = const Duration(seconds: 30),
    this.sendContext = false,
    MegooFlagsLogger? logger,
    this.httpClient,
  }) : sdkKey = _checkSdkKey(sdkKey),
       baseUrl = _checkBaseUrl(baseUrl, offline),
       bootstrap = bootstrap == null ? null : _parseBootstrap(bootstrap),
       pollInterval = pollInterval < _minPollInterval
           ? _minPollInterval
           : pollInterval,
       flushInterval = flushInterval < _minFlushInterval
           ? _minFlushInterval
           : (flushInterval > _maxFlushInterval
                 ? _maxFlushInterval
                 : flushInterval),
       logger = logger ?? defaultLogger {
    if (initTimeout <= Duration.zero) {
      throw ArgumentError('megooflags: initTimeout must be greater than 0');
    }
  }

  final String sdkKey;

  /// Without trailing slashes; null when offline without a base URL.
  final String? baseUrl;
  final List<EvaluateResult>? bootstrap;
  final bool disableCache;
  final bool offline;
  final Duration initTimeout;
  final bool disableStream;
  final Duration pollInterval;
  final Duration flushInterval;
  final bool sendContext;
  final MegooFlagsLogger logger;

  /// null: the provider uses its own client.
  final http.Client? httpClient;
}

/// _checkSdkKey requires a client key; the message never contains the key.
String _checkSdkKey(String key) {
  if (key.startsWith('mf_server_')) {
    throw ArgumentError(
      'megooflags: a server key must never ship in an app; use a client key '
      '(mf_client_…)',
    );
  }
  if (!key.startsWith('mf_client_')) {
    throw ArgumentError(
      'megooflags: sdkKey must be a client key (mf_client_…)',
    );
  }
  return key;
}

/// _checkBaseUrl requires an http or https URL with a host unless offline,
/// and removes trailing slashes.
String? _checkBaseUrl(String? url, bool offline) {
  if (url == null) {
    if (!offline) {
      throw ArgumentError('megooflags: baseUrl is required unless offline');
    }
    return null;
  }
  final uri = Uri.tryParse(url);
  final scheme = uri?.scheme.toLowerCase();
  if ((scheme != 'http' && scheme != 'https') || uri!.host.isEmpty) {
    throw ArgumentError('megooflags: baseUrl must be an http or https URL');
  }
  return url.replaceFirst(RegExp(r'/+$'), '');
}

List<EvaluateResult> _parseBootstrap(String json) {
  try {
    return parseResults(json, 'megooflags: invalid bootstrap');
  } on FormatException catch (e) {
    throw ArgumentError(e.message);
  }
}

/// The severity of a log line.
enum MegooFlagsLogLevel { debug, info, warn, error }

/// A function the SDK logs through. Messages start with "megooflags: " and
/// never contain the SDK key.
typedef MegooFlagsLogger =
    void Function(MegooFlagsLogLevel level, String message);

/// The part of `dart:developer`'s `log` the default logger writes through.
typedef DeveloperLog = void Function(String message, {String name, int level});

/// developerLogger writes warnings (level 900) and errors (level 1000) to
/// [sink] under the name "megooflags"; debug and info are silent.
MegooFlagsLogger developerLogger(DeveloperLog sink) =>
    (level, message) => switch (level) {
      MegooFlagsLogLevel.debug || MegooFlagsLogLevel.info => null,
      MegooFlagsLogLevel.warn => sink(message, name: 'megooflags', level: 900),
      MegooFlagsLogLevel.error => sink(
        message,
        name: 'megooflags',
        level: 1000,
      ),
    };

/// defaultLogger writes warnings and errors with `dart:developer` `log`.
void defaultLogger(MegooFlagsLogLevel level, String message) =>
    _developerLogger(level, message);

final MegooFlagsLogger _developerLogger = developerLogger(developer.log);
