# frozen_string_literal: true

require "uri"
require_relative "internal/log"

module MegooFlags
  # Config is a Provider's options with the defaults filled in and the intervals clamped.
  # Durations are seconds. Zero, negative and NaN durations mean the default, the poll interval
  # is at least 15 s, the flush interval 10 to 60 s, and the base URL loses its trailing
  # slashes. inspect leaves the key out.
  Config = Data.define(:sdk_key, :base_url, :bootstrap_file, :cache_dir, :offline, :init_timeout,
                       :disable_stream, :poll_interval, :flush_interval, :send_context, :logger) do
    def initialize(sdk_key:, base_url: nil, bootstrap_file: nil, cache_dir: nil, offline: false, # rubocop:disable Metrics/ParameterLists
                   init_timeout: Config::DEFAULT_INIT_TIMEOUT, disable_stream: false,
                   poll_interval: Config::DEFAULT_POLL_INTERVAL, flush_interval: Config::DEFAULT_FLUSH_INTERVAL,
                   send_context: false, logger: nil)
      flush = Config.seconds(flush_interval, Config::DEFAULT_FLUSH_INTERVAL)
      super(
        sdk_key: sdk_key,
        base_url: base_url.to_s.sub(%r{/+\z}, "").freeze,
        bootstrap_file: Config.path(bootstrap_file),
        cache_dir: Config.path(cache_dir),
        offline: offline ? true : false,
        init_timeout: Config.seconds(init_timeout, Config::DEFAULT_INIT_TIMEOUT),
        disable_stream: disable_stream ? true : false,
        poll_interval: [Config.seconds(poll_interval, Config::DEFAULT_POLL_INTERVAL), Config::MIN_POLL_INTERVAL].max,
        flush_interval: flush.clamp(Config::MIN_FLUSH_INTERVAL, Config::MAX_FLUSH_INTERVAL),
        send_context: send_context ? true : false,
        logger: logger || Internal::Log.default_logger
      )
    end

    # error is why a provider cannot run with this config, or nil. It never includes the key.
    def error
      key = sdk_key.is_a?(String) ? sdk_key : ""
      if key.start_with?("mf_client_")
        return "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)"
      end
      return "megooflags: invalid SDK key; use a server key (mf_server_…)" unless key.start_with?("mf_server_")
      return offline_error if offline
      return "megooflags: base_url must be an http or https URL" unless http_url?

      nil
    end

    def inspect
      fields = to_h.except(:sdk_key, :logger).map { |k, v| "#{k}=#{v.inspect}" }
      "#<data MegooFlags::Config sdk_key=[hidden] #{fields.join(", ")}>"
    end
    alias_method :to_s, :inspect

    private

    def offline_error
      return nil if bootstrap_file || cache_dir

      "megooflags: offline needs a bootstrap_file or a cache_dir"
    end

    def http_url?
      uri = URI.parse(base_url)
      %w[http https].include?(uri.scheme) && !uri.host.to_s.empty?
    rescue URI::InvalidURIError
      false
    end
  end

  # Defaults and limits of Config, in seconds.
  class Config
    DEFAULT_INIT_TIMEOUT = 5.0
    DEFAULT_POLL_INTERVAL = 30.0
    MIN_POLL_INTERVAL = 15.0
    DEFAULT_FLUSH_INTERVAL = 30.0
    MIN_FLUSH_INTERVAL = 10.0
    MAX_FLUSH_INTERVAL = 60.0

    # seconds is value as a Float when it is a positive finite number, else default.
    def self.seconds(value, default)
      return default unless value.is_a?(Numeric)

      s = value.to_f
      s.finite? && s.positive? ? s : default
    end

    # path is value as a String, or nil when it is nil or empty.
    def self.path(value)
      return nil if value.nil?

      s = value.to_s
      s.empty? ? nil : s.dup.freeze
    end
  end
end
