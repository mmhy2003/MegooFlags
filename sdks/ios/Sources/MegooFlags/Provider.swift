// The OpenFeature provider: wires the configuration, store, cache, sync,
// streamer, exposures, batcher and background signal together, maps the
// sync events to provider events and serves flag reads from the store.
import Combine
import Foundation
import OpenFeature

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

private struct MegooFlagsMetadata: ProviderMetadata {
    let name: String? = "MegooFlags"
}

/// Completes a lifecycle Future from a Task; the SDK resolves it once.
private struct Completion: @unchecked Sendable {
    let promise: (Result<Void, Never>) -> Void

    func callAsFunction() {
        promise(.success(()))
    }
}

/// Lets the parts built before the provider reach it once it exists.
private final class ProviderLink: @unchecked Sendable {
    weak var provider: MegooFlagsProvider?
}

/// isFatal tells a refused or revoked key from other failures.
private func isFatal(_ error: any Error) -> Bool {
    switch error as? SDKError {
    case .fatal?, .revoked?: return true
    default: return false
    }
}

/// errorDetails describes error for an error event.
private func errorDetails(_ error: any Error) -> ProviderEventDetails {
    ProviderEventDetails(message: failureReason(error), errorCode: isFatal(error) ? .providerFatal : .general)
}

/// MegooFlagsProvider evaluates flags remotely with a client key and serves
/// them synchronously from the last values for the current context.
public final class MegooFlagsProvider: FeatureProvider, @unchecked Sendable {
    /// How long shutdown waits for the last exposures, in seconds.
    static let closeTimeout: TimeInterval = 5

    public let hooks: [any Hook] = []
    public let metadata: any ProviderMetadata = MegooFlagsMetadata()

    private let tracker = ProviderStatusTracker()
    private let store = Store()
    private let sync: Sync
    private let exposures: Exposures?
    private let batcher: Batcher?
    private let background: Background?
    private let lock = NSLock()
    /// The request context of the current evaluation context.
    private var body: [String: JSONValue] = [:]
    private var shutDown = false

    /// The initialiser throws MegooFlagsConfigError on an invalid
    /// configuration, a server key included.
    public convenience init(config: MegooFlagsConfig) throws {
        try self.init(config: config, transport: nil)
    }

    /// transport nil uses URLSession with config.sessionConfiguration;
    /// cacheDirectory nil is Application Support/megooflags; backgroundNames
    /// nil follows no notifications.
    init(
        config: MegooFlagsConfig, transport: (any HTTPTransport)?, cacheDirectory: URL? = nil,
        notificationCenter: NotificationCenter = .default,
        backgroundNames: (hidden: Notification.Name, visible: Notification.Name)? = Background.platformNames
    ) throws {
        let resolved = try ResolvedConfig(config)
        let transport = transport ?? URLSessionTransport(configuration: resolved.sessionConfiguration)
        let (baseURL, sdkKey, logger) = (resolved.baseURL, resolved.sdkKey, resolved.logger)
        let link = ProviderLink()
        let tracker = self.tracker
        sync = Sync(
            config: resolved, store: store, cache: Cache(sdkKey: sdkKey, directory: cacheDirectory, logger: logger),
            transport: transport,
            makeStreamer: { hooks in
                // Sync makes a streamer only online, where baseURL is required.
                Streamer(baseURL: baseURL!, sdkKey: sdkKey, transport: transport, logger: logger, hooks: hooks)
            },
            onEvent: { event in
                switch event {
                case .ready: tracker.send(.ready(nil))
                case .stale: tracker.send(.stale(nil))
                case .changed(let flags): tracker.send(.configurationChanged(ProviderEventDetails(flagsChanged: flags)))
                case .fatal(let error): tracker.send(.error(errorDetails(error)))
                // The sync logs and retries; a failed context change is
                // reported by onContextSet.
                case .error: break
                }
            },
            onFatalStop: { link.provider?.stopDelivery() })
        let exposures = resolved.offline ? nil : Exposures(sendContext: resolved.sendContext, logger: logger)
        self.exposures = exposures
        batcher = exposures.flatMap { exposures in
            baseURL.map { url in
                Batcher(
                    baseURL: url, sdkKey: sdkKey, transport: transport, exposures: exposures,
                    flushInterval: resolved.flushInterval, logger: logger,
                    onFatal: { error in
                        guard let provider = link.provider else { return }
                        Task { await provider.sync.stop() }
                        provider.stopDelivery()
                        tracker.send(.error(errorDetails(error)))
                    })
            }
        }
        background = backgroundNames.map { names in
            Background(
                center: notificationCenter, hidden: names.hidden, visible: names.visible,
                flush: { await link.provider?.batcher?.flush() }, pause: { await link.provider?.sync.pause() },
                resume: { await link.provider?.sync.resume() })
        }
        link.provider = self
    }

    public var status: ProviderStatus { tracker.status }

    public func observe() -> AnyPublisher<ProviderEvent, Never> {
        tracker.observe()
    }

    /// initialize serves bootstrap or cached values at once, or waits for
    /// the first evaluation up to initTimeout. It sends ready, or an error
    /// with PROVIDER_FATAL for a refused key and GENERAL otherwise (ready
    /// follows when values arrive), before the Future completes.
    public func initialize(initialContext: (any EvaluationContext)?) -> Future<Void, Never> {
        let body = toRequestContext(initialContext)
        setBody(body)
        return Future { promise in
            let done = Completion(promise: promise)
            Task {
                if let batcher = self.batcher {
                    await batcher.start()
                }
                self.background?.start()
                do {
                    try await self.sync.start(body: body)
                    self.tracker.send(.ready(nil))
                } catch {
                    self.tracker.send(.error(errorDetails(error)))
                }
                done()
            }
        }
    }

    /// onContextSet sends reconciling, fetches the values for newContext,
    /// then sends contextChanged, or an error when the fetch failed (it is
    /// retried), before the Future completes.
    public func onContextSet(
        oldContext: (any EvaluationContext)?, newContext: any EvaluationContext
    ) -> Future<Void, Never> {
        let body = toRequestContext(newContext)
        setBody(body)
        return Future { promise in
            let done = Completion(promise: promise)
            self.tracker.send(.reconciling(nil))
            Task {
                do {
                    try await self.sync.setContext(body: body)
                    self.tracker.send(.contextChanged(nil))
                } catch {
                    self.tracker.send(.error(errorDetails(error)))
                }
                done()
            }
        }
    }

    /// shutdown stops the sync and the background signal and sends the last
    /// exposures within 5 seconds. A second call returns at once.
    public func shutdown() async {
        let first: Bool = {
            lock.lock()
            defer { lock.unlock() }
            defer { shutDown = true }
            return !shutDown
        }()
        guard first else { return }
        background?.stop()
        await sync.stop()
        await batcher?.close(timeout: Self.closeTimeout)
    }

    public func getBooleanEvaluation(key: String, defaultValue: Bool, context: (any EvaluationContext)?) throws
        -> ProviderEvaluation<Bool>
    {
        read(key, as: .boolean, default: defaultValue)
    }

    public func getStringEvaluation(key: String, defaultValue: String, context: (any EvaluationContext)?) throws
        -> ProviderEvaluation<String>
    {
        read(key, as: .string, default: defaultValue)
    }

    public func getIntegerEvaluation(key: String, defaultValue: Int64, context: (any EvaluationContext)?) throws
        -> ProviderEvaluation<Int64>
    {
        read(key, as: .integer, default: defaultValue)
    }

    public func getDoubleEvaluation(key: String, defaultValue: Double, context: (any EvaluationContext)?) throws
        -> ProviderEvaluation<Double>
    {
        read(key, as: .double, default: defaultValue)
    }

    public func getObjectEvaluation(key: String, defaultValue: Value, context: (any EvaluationContext)?) throws
        -> ProviderEvaluation<Value>
    {
        read(key, as: .object, default: defaultValue)
    }

    /// read resolves flag from the store and records an exposure for a
    /// served value.
    private func read<T>(_ flag: String, as kind: FlagKind, default defaultValue: T) -> ProviderEvaluation<T> {
        let evaluation = resolve(store.get(), flag: flag, as: kind, default: defaultValue)
        if let exposures, evaluation.errorCode == nil, let variant = evaluation.variant {
            exposures.record(flag: flag, variant: variant, reason: evaluation.reason ?? "", body: currentBody)
        }
        return evaluation
    }

    private var currentBody: [String: JSONValue] {
        lock.lock()
        defer { lock.unlock() }
        return body
    }

    private func setBody(_ b: [String: JSONValue]) {
        lock.lock()
        defer { lock.unlock() }
        body = b
    }

    /// stopDelivery stops the exposures and the background signal for a
    /// refused key.
    private func stopDelivery() {
        background?.stop()
        if let batcher {
            Task { await batcher.stop() }
        }
    }
}
