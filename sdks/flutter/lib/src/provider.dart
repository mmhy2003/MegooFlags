// The OpenFeature provider: wires the configuration, store, cache, sync,
// streamer, exposures, batcher and background signal together, maps the sync
// events to provider events and serves flag reads from the store.
import 'dart:async';

import 'package:http/http.dart' as http;
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

import 'batcher.dart';
import 'cache.dart';
import 'config.dart';
import 'evaluator.dart';
import 'exposures.dart';
import 'lifecycle.dart';
import 'resolve.dart';
import 'store.dart';
import 'streamer.dart';
import 'sync.dart';
import 'transport.dart';

/// How long shutdown waits for the last exposures.
const _closeTimeout = Duration(seconds: 5);

const _metadata = ProviderMetadata(name: 'MegooFlags');

bool _isFatal(Object e) => e is FatalException || e is RevokedException;

/// _message describes [e] without the SDK key.
String _message(Object e) => switch (e) {
  SdkException(:final message) || SyncException(:final message) => message,
  _ => 'megooflags: ${e.runtimeType}',
};

/// MegooFlagsProvider evaluates flags remotely with a client key and serves
/// them synchronously from the last values for the current context.
class MegooFlagsProvider
    implements
        FeatureProvider,
        InitializableProvider,
        ContextReconciliationProvider,
        ShutdownProvider,
        ProviderEventSource {
  /// The constructor throws ArgumentError when [config] could not be built;
  /// MegooFlagsConfig checks the options itself.
  MegooFlagsProvider(MegooFlagsConfig config)
    : _client = config.httpClient ?? http.Client(),
      _ownsClient = config.httpClient == null,
      _exposures = config.offline
          ? null
          : Exposures(sendContext: config.sendContext, logger: config.logger) {
    final baseUrl = config.baseUrl;
    final online = !config.offline && baseUrl != null;
    final exposures = _exposures;
    _batcher = online && exposures != null
        ? Batcher(
            _client,
            baseUrl: baseUrl,
            sdkKey: config.sdkKey,
            exposures: exposures,
            flushInterval: config.flushInterval,
            logger: config.logger,
            onFatal: _onBatcherFatal,
          )
        : null;
    _sync = Sync(
      config: config,
      store: _store,
      cache: Cache(
        config.sdkKey,
        config.disableCache ? null : SharedPreferencesCacheStore(),
        config.logger,
      ),
      evaluator: online
          ? Evaluator(_client, baseUrl: baseUrl, sdkKey: config.sdkKey)
          : null,
      newStreamer: online ? (hooks) => Streamer(config, hooks) : null,
      events: SyncEvents(
        ready: () => _emit(ProviderEvent(type: ProviderEventType.ready)),
        stale: () => _emit(ProviderEvent(type: ProviderEventType.stale)),
        changed: (flags) => _emit(
          ProviderEvent(
            type: ProviderEventType.configurationChanged,
            flagsChanged: flags,
          ),
        ),
        fatal: _emitFatal,
        // Sync logs and retries; a failed context change is reported by
        // onContextChanged.
        error: (_) {},
      ),
      onFatalStop: _stopDelivery,
    );
    _background = BackgroundController(
      source: AppLifecycleSource(),
      flush: () => _batcher?.flush() ?? Future.value(),
      pause: _sync.pause,
      resume: _sync.resume,
    );
  }

  final http.Client _client;
  final bool _ownsClient;
  final Exposures? _exposures;
  final _store = Store();
  // Synchronous, as the SDK's own providers: it reads the status an event
  // sets right after initialize or onContextChanged fails.
  final _events = StreamController<ProviderEvent>.broadcast(sync: true);
  late final Batcher? _batcher;
  late final Sync _sync;
  late final BackgroundController _background;
  bool _fatal = false;
  Future<void>? _shutdown;

  @override
  ProviderMetadata get metadata => _metadata;

  /// events emits ready, stale, configurationChanged (with the changed
  /// flags), reconciling, contextChanged and errors; it closes on shutdown.
  @override
  Stream<ProviderEvent> get events => _events.stream;

  /// initialize serves bootstrap or cached values at once, or waits for the
  /// first evaluation up to initTimeout, then emits ready. A refused key
  /// emits an error with providerFatal and throws it; a timeout or another
  /// failure emits and throws a general error while retries go on, and
  /// ready follows when values arrive.
  @override
  Future<void> initialize(EvaluationContext context, {String? domain}) async {
    _batcher?.start();
    _background.start();
    try {
      await _sync.start(context);
    } catch (e) {
      throw _fail(e);
    }
    _emit(ProviderEvent(type: ProviderEventType.ready));
  }

  /// onContextChanged emits reconciling, fetches the values for
  /// [newContext], then emits contextChanged, or an error that it also
  /// throws (retries go on). A new targeting key serves defaults until its
  /// values arrive.
  @override
  Future<void> onContextChanged(
    EvaluationContext previousContext,
    EvaluationContext newContext,
  ) async {
    _emit(ProviderEvent(type: ProviderEventType.reconciling));
    try {
      await _sync.setContext(newContext);
    } catch (e) {
      throw _fail(e);
    }
    _emit(ProviderEvent(type: ProviderEventType.contextChanged));
  }

  /// shutdown stops the sync and the background signal, sends the last
  /// exposures within 5 seconds and closes [events]. Later calls return the
  /// first call's future.
  @override
  Future<void> shutdown() => _shutdown ??= _close();

  Future<void> _close() async {
    _background.stop();
    _sync.stop();
    await _batcher?.close(timeout: _closeTimeout);
    if (_ownsClient) _client.close();
    await _events.close();
  }

  @override
  ResolutionDetails<bool> resolveBooleanValue(
    String flagKey,
    bool defaultValue,
    EvaluationContext context,
  ) => _read(flagKey, FlagKind.boolean, defaultValue);

  @override
  ResolutionDetails<String> resolveStringValue(
    String flagKey,
    String defaultValue,
    EvaluationContext context,
  ) => _read(flagKey, FlagKind.string, defaultValue);

  @override
  ResolutionDetails<int> resolveIntegerValue(
    String flagKey,
    int defaultValue,
    EvaluationContext context,
  ) => _read(flagKey, FlagKind.integer, defaultValue);

  @override
  ResolutionDetails<double> resolveDoubleValue(
    String flagKey,
    double defaultValue,
    EvaluationContext context,
  ) => _read(flagKey, FlagKind.number, defaultValue);

  @override
  ResolutionDetails<Map<String, Object?>> resolveStructureValue(
    String flagKey,
    Map<String, Object?> defaultValue,
    EvaluationContext context,
  ) => _read(flagKey, FlagKind.object, defaultValue);

  /// _read resolves [flag] from the store and records an exposure for a
  /// served value: no error and a variant.
  ResolutionDetails<T> _read<T>(String flag, FlagKind kind, T defaultValue) {
    final d = resolve(_store.current, flag, kind, defaultValue);
    final variant = d.variant;
    if (_exposures != null &&
        _shutdown == null &&
        d.errorCode == null &&
        variant != null) {
      _exposures.record(flag, variant, d.reason ?? '', _sync.body);
    }
    return d;
  }

  /// _fail emits the error event for a failed start or context change and
  /// returns the exception to throw. A fatal error is emitted only once.
  OpenFeatureException _fail(Object e) {
    final fatal = _isFatal(e);
    final code = fatal ? ErrorCode.providerFatal : ErrorCode.general;
    if (fatal) {
      _emitFatal(e as SdkException);
    } else {
      _emit(
        ProviderEvent(
          type: ProviderEventType.error,
          message: _message(e),
          errorCode: code,
        ),
      );
    }
    return OpenFeatureException(_message(e), errorCode: code);
  }

  void _emit(ProviderEvent event) {
    if (!_events.isClosed) _events.add(event);
  }

  void _emitFatal(SdkException error) {
    if (_fatal) return;
    _fatal = true;
    _emit(
      ProviderEvent(
        type: ProviderEventType.error,
        message: error.message,
        errorCode: ErrorCode.providerFatal,
      ),
    );
  }

  /// _stopDelivery stops the exposures and the background signal for a
  /// refused key.
  void _stopDelivery() {
    _background.stop();
    _batcher?.stop();
  }

  /// The events endpoint refused the key: stop everything and report it.
  void _onBatcherFatal(FatalException error) {
    _sync.stop();
    _background.stop();
    _emitFatal(error);
  }
}
