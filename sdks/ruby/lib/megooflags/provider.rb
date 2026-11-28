# frozen_string_literal: true

require "open_feature/sdk"
require "time"
require_relative "config"
require_relative "internal/delivery/cache"
require_relative "internal/delivery/store"
require_relative "internal/delivery/sync"
require_relative "internal/engine/evaluator"
require_relative "internal/events/batcher"
require_relative "internal/fork_hook"
require_relative "internal/transport/transport"

module MegooFlags
  # Internal holds the SDK's private parts.
  module Internal
    class << self
      # sync_tuning replaces the Sync timings of the providers created afterwards; only tests set
      # it.
      attr_accessor :sync_tuning
    end
  end

  # Provider is the MegooFlags OpenFeature provider. It evaluates flags against the ruleset of
  # the environment of a server SDK key, with no network call per evaluation.
  #
  # Construction never touches the network: it validates the options (an invalid config is
  # logged, and every evaluation then returns the default with PROVIDER_FATAL) and loads the
  # bootstrap file, then, when that gave no ruleset, the cache. init starts the background
  # threads; a forked child restarts them (see Internal::ForkHook). Durations are seconds.
  class Provider # rubocop:disable Metrics/ClassLength
    include OpenFeature::SDK::Provider::EventEmitter

    SDK = OpenFeature::SDK
    ErrorCode = OpenFeature::SDK::Provider::ErrorCode
    Event = OpenFeature::SDK::ProviderEvent
    Delivery = Internal::Delivery
    # Bounds the final events flush, in seconds.
    SHUTDOWN_TIMEOUT = 5.0
    # The OpenFeature result type of each fetch method, as the engine names it.
    EXPECTED_TYPES = { boolean: "boolean", string: "string", number: "number", integer: "number",
                       float: "number", object: "json" }.freeze

    # config is the Config, with the defaults filled in.
    attr_reader :config, :metadata

    def initialize(**)
      @config = Config.new(**)
      @logger = @config.logger
      @config_error = @config.error
      @metadata = SDK::Provider::ProviderMetadata.new(name: "MegooFlags").freeze

      new_locks
      @started = false
      @shut_down = false
      @fatal = nil # the refusing error, once the key was refused
      @fatal_reported = false # PROVIDER_ERROR with PROVIDER_FATAL was emitted
      @waiting = false # init waits for the first ruleset
      @init_timed_out = false # init failed with the timeout error
      @stale = false # STALE was emitted and not yet followed by READY

      @store = Delivery::Store.new
      @cache = Delivery::Cache.new(@config.cache_dir, @config.sdk_key.to_s)
      @sync = nil
      @batcher = nil
      if @config_error
        @logger.error("megooflags: invalid config: #{@config_error}")
        return
      end
      # Offline uses Sync only to apply the bootstrap file and the cache; it is never started.
      @sync = new_sync
      @batcher = new_batcher
      load_ruleset
    end

    # The OpenFeature SDK leaves READY and ERROR to init (see init).
    def emits_lifecycle_events? = true

    # init starts the event sender and, unless offline, the ruleset sync. With a ruleset from the
    # bootstrap file or the cache, it emits PROVIDER_READY and returns at once; the network
    # refreshes the ruleset in the background. Otherwise it waits up to init_timeout for the
    # first ruleset; on timeout it keeps retrying and emits PROVIDER_READY when a ruleset
    # arrives. A failure emits PROVIDER_ERROR (PROVIDER_FATAL for an invalid config or a refused
    # key) and raises an OpenFeature::SDK::EvaluationError with that code.
    def init(_evaluation_context = nil)
      start_once
      wait_for_ruleset
      emit_event(Event::PROVIDER_READY, message: "megooflags: ruleset loaded")
      nil
    rescue SDK::EvaluationError => e
      init_failed(e)
      raise
    end

    # shutdown stops the sync and sends the buffered events within 5 s. Idempotent. The last
    # ruleset keeps being served.
    def shutdown
      @lock.synchronize do
        return if @shut_down

        @shut_down = true
        @cond.broadcast
      end
      Internal::ForkHook.unregister(self)
      @sync&.stop
      return if @batcher.nil? || @batcher.shutdown(SHUTDOWN_TIMEOUT) || @fatal

      @logger.warn("megooflags: could not send all events before shutdown")
    end

    # emit_event emits nothing once the provider is shut down.
    def emit_event(event_type, details = {})
      return if @shut_down

      super
    end

    def fetch_boolean_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:boolean, flag_key, default_value, evaluation_context)
    end

    def fetch_string_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:string, flag_key, default_value, evaluation_context)
    end

    # fetch_number_value returns the number as it is in the ruleset: an Integer or a Float.
    def fetch_number_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:number, flag_key, default_value, evaluation_context)
    end

    # fetch_integer_value refuses a number that is not integral with TYPE_MISMATCH.
    def fetch_integer_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:integer, flag_key, default_value, evaluation_context)
    end

    def fetch_float_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:float, flag_key, default_value, evaluation_context)
    end

    # fetch_object_value serves only a Hash or an Array; any other value is TYPE_MISMATCH.
    def fetch_object_value(flag_key:, default_value:, evaluation_context: nil)
      resolve(:object, flag_key, default_value, evaluation_context)
    end

    private

    def new_locks
      @lock = Mutex.new
      @cond = ConditionVariable.new
    end

    def new_sync
      Delivery::Sync.new(
        base_url: @config.base_url, sdk_key: @config.sdk_key, store: @store, cache: @cache, logger: @logger,
        disable_stream: @config.disable_stream, poll_interval: @config.poll_interval,
        on_change: method(:on_change), on_status: method(:on_status),
        tuning: Internal.sync_tuning || Delivery::Tuning.new
      )
    end

    def new_batcher
      return nil if @config.offline

      Internal::Events::Batcher.new(
        url: Internal::Transport.sdk_url(@config.base_url, "events"), sdk_key: @config.sdk_key, logger: @logger,
        flush_interval: @config.flush_interval, send_context: @config.send_context,
        on_fatal: ->(err) { on_fatal(err, from_sync: false) }
      )
    end

    # load_ruleset applies the bootstrap file, then, when there is still no ruleset, the cache.
    def load_ruleset
      if @config.bootstrap_file
        begin
          raw = File.binread(@config.bootstrap_file)
          @sync.apply(raw, "", "bootstrap") # apply logs an invalid ruleset
        rescue SystemCallError, IOError => e
          @logger.warn("megooflags: could not read the bootstrap file: #{e.message}")
        end
      end
      return unless @store.load.nil?

      begin
        cached = @cache.load
      rescue Delivery::CacheError, SystemCallError => e
        @logger.warn("megooflags: could not read the ruleset cache: #{e.message}")
        return
      end
      @sync.apply(cached.ruleset, cached.etag, "cache") if cached
    end

    def start_once
      raise init_error(@config_error, ErrorCode::PROVIDER_FATAL) if @config_error

      start = @lock.synchronize do
        raise init_error("megooflags: the provider was shut down") if @shut_down

        first = !@started
        @started = true
        first
      end
      start_workers if start
    end

    def start_workers
      @batcher&.start
      return if @config.offline

      @sync.start
      Internal::ForkHook.register(self)
    end

    # wait_for_ruleset returns once there is a ruleset, or raises the init error.
    def wait_for_ruleset
      return unless @store.load.nil?

      if @config.offline
        raise init_error("megooflags: offline, and neither the bootstrap file nor the cache gave a valid ruleset")
      end

      timeout = @config.init_timeout
      @lock.synchronize do
        @waiting = true
        deadline = monotonic + timeout
        while @store.load.nil? && @fatal.nil? && !@shut_down && (left = deadline - monotonic).positive?
          @cond.wait(@lock, left)
        end
        @waiting = false
        return unless @store.load.nil?
        raise init_error(@fatal.message, ErrorCode::PROVIDER_FATAL) if @fatal
        raise init_error("megooflags: the provider was shut down") if @shut_down

        @init_timed_out = true
      end
      raise init_error("megooflags: no ruleset within #{format("%g", timeout)}s")
    end

    def init_error(message, code = ErrorCode::GENERAL) = SDK::EvaluationError.new(message, error_code: code)

    # init_failed emits PROVIDER_ERROR for a failed init; a refused key is reported once only.
    def init_failed(err)
      if err.error_code == ErrorCode::PROVIDER_FATAL
        report = @lock.synchronize { !@fatal_reported && (@fatal_reported = true) }
        return unless report
      end
      emit_event(Event::PROVIDER_ERROR, error_code: err.error_code, message: err.message)
    end

    def monotonic = Process.clock_gettime(Process::CLOCK_MONOTONIC)

    # restart_after_fork runs in a forked child (see Internal::ForkHook): new locks, a store with
    # the parent's ruleset, and a new sync and event sender, whose buffers start empty. The
    # parent's threads do not exist here, so the old objects are dropped without being stopped
    # (stopping would close connections the parent still uses).
    def restart_after_fork
      new_locks
      return if @shut_down || @fatal

      current = @store.load
      @store = Delivery::Store.new
      @store.swap(current) if current
      @stale = false
      @waiting = false
      @sync = new_sync
      @batcher = new_batcher
      start_workers
    end

    # Sync and events callbacks

    # on_change: the first ruleset wakes a waiting init (and makes the provider ready when init
    # had timed out); a later one is a configuration change.
    def on_change(old, new)
      if old.nil?
        timed_out = @lock.synchronize do
          @cond.broadcast
          @init_timed_out
        end
        emit_event(Event::PROVIDER_READY, message: "megooflags: ruleset loaded") if timed_out
        return
      end
      emit_event(Event::PROVIDER_CONFIGURATION_CHANGED,
                 message: "megooflags: ruleset updated", flags_changed: flag_changes(old.ruleset, new.ruleset))
    end

    # on_status: stale while disconnected after being ready, and ready again on reconnect.
    def on_status(status, err)
      case status
      when :stale
        emit = @lock.synchronize do
          emit = !@stale && !@store.load.nil? && @fatal.nil?
          @stale ||= emit
          emit
        end
        message = "megooflags: disconnected; serving the last ruleset"
        message += ": #{err.message}" if err
        emit_event(Event::PROVIDER_STALE, message: message) if emit
      when :ready
        emit = @lock.synchronize { @stale.tap { @stale = false } }
        emit_event(Event::PROVIDER_READY, message: "megooflags: reconnected") if emit
      else
        on_fatal(err, from_sync: true)
      end
    end

    # on_fatal handles a refused key once: it stops sending events and the sync, and emits
    # PROVIDER_ERROR with PROVIDER_FATAL, unless init is waiting and reports it itself. The
    # last ruleset keeps being served. Sync stops itself on a fatal status.
    def on_fatal(err, from_sync:)
      fatal = err || Internal::Transport::FatalError.new(reason: "refused")
      report = @lock.synchronize do
        return unless @fatal.nil?

        @fatal = fatal
        @cond.broadcast
        !@waiting && !@fatal_reported && (@fatal_reported = true)
      end
      @logger.error("megooflags: the server refused the SDK key; serving the last ruleset: #{fatal.message}")
      @batcher&.stop
      @sync&.stop unless from_sync
      Internal::ForkHook.unregister(self)
      emit_event(Event::PROVIDER_ERROR, error_code: ErrorCode::PROVIDER_FATAL, message: fatal.message) if report
    end

    # Evaluation

    def resolve(type, flag_key, default, evaluation_context)
      snapshot = @store.load
      return not_ready(default) if snapshot.nil?

      context = engine_context(evaluation_context)
      result = Internal::Engine::Evaluator.evaluate(snapshot.ruleset, flag_key, context, default, EXPECTED_TYPES[type])
      record(flag_key, result, context)
      return details(result, default) unless served?(result)

      typed(type, snapshot.ruleset, flag_key, result, default)
    end

    # typed is the details of a served result for the fetch method of type.
    def typed(type, ruleset, flag_key, result, default)
      case type
      when :integer
        variation = ruleset.flags[flag_key].variations[result.variant]
        return mismatch(default, "flag '#{flag_key}' is not an integer") unless variation.is_int

        details(result, variation.int_value)
      when :float then details(result, result.value.to_f)
      when :object
        unless result.value.is_a?(Hash) || result.value.is_a?(Array)
          return mismatch(default, "flag '#{flag_key}' is not an object or an array")
        end

        details(result, result.value)
      else details(result, result.value)
      end
    end

    def served?(result) = result.error_code.nil? && !result.variant.nil?

    # details maps an engine result to OpenFeature; PREREQUISITE_FAILED passes through.
    def details(result, value)
      SDK::Provider::ResolutionDetails.new(
        value: value, reason: result.reason, variant: result.variant, error_code: result.error_code,
        error_message: result.error_message, flag_metadata: result.metadata
      )
    end

    def mismatch(default, message)
      SDK::Provider::ResolutionDetails.new(value: default, reason: SDK::Provider::Reason::ERROR,
                                           error_code: ErrorCode::TYPE_MISMATCH, error_message: message)
    end

    # not_ready is the details while there is no ruleset.
    def not_ready(default)
      code, message = if @config_error
                        [ErrorCode::PROVIDER_FATAL, @config_error]
                      elsif @fatal
                        [ErrorCode::PROVIDER_FATAL, "megooflags: the SDK key was refused"]
                      else
                        [ErrorCode::PROVIDER_NOT_READY, "megooflags: no ruleset is loaded yet"]
                      end
      SDK::Provider::ResolutionDetails.new(value: default, reason: SDK::Provider::Reason::ERROR,
                                           error_code: code, error_message: message)
    end

    # record queues the evaluation for the event sender without blocking.
    def record(flag_key, result, context)
      return if @batcher.nil?

      key = context["key"]
      @batcher.record(Internal::Events::Evaluation.new(
                        flag_key: flag_key, variant: result.variant || "", served: !result.variant.nil?,
                        reason: result.reason.to_s, context_key: key.is_a?(String) ? key : "",
                        at: Process.clock_gettime(Process::CLOCK_REALTIME, :millisecond),
                        context: @config.send_context ? json_safe(context) : nil
                      ))
    end

    # engine_context is the engine's context: the fields with string keys at every level, and key
    # set to the targeting key when there is one. A Time stays a Time and matches no operator.
    def engine_context(evaluation_context)
      return {} if evaluation_context.nil?

      context = {}
      evaluation_context.fields.each do |k, v|
        context[k.to_s] = plain(v) unless k == SDK::EvaluationContext::TARGETING_KEY
      end
      key = evaluation_context.targeting_key
      context["key"] = key unless key.nil?
      context
    end

    def plain(value)
      case value
      when Hash then value.to_h { |k, v| [k.to_s, plain(v)] }
      when Array then value.map { |v| plain(v) }
      else value
      end
    end

    # json_safe is a copy of the context that JSON.generate turns into the expected text: times
    # and dates become ISO 8601 text.
    def json_safe(value)
      case value
      when Hash then value.transform_values { |v| json_safe(v) }
      when Array then value.map { |v| json_safe(v) }
      when Time then value.iso8601
      else value.respond_to?(:iso8601) ? value.iso8601 : value
      end
    end

    # flag_changes is the sorted keys of the flags added, removed or with a different version.
    def flag_changes(old, new)
      keys = new.flags.filter_map { |k, f| k if !old.flags.key?(k) || old.flags[k].version != f.version }
      keys.concat(old.flags.keys.reject { |k| new.flags.key?(k) })
      keys.sort
    end
  end
end
