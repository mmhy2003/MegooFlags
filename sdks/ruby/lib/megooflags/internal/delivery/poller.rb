# frozen_string_literal: true

require_relative "../transport/transport"

module MegooFlags
  module Internal
    module Delivery
      # InvalidRulesetError: a poll downloaded a ruleset that does not parse.
      class InvalidRulesetError < StandardError
        def initialize(message = "megooflags: invalid ruleset") = super
      end

      # PollTimeoutError: a poll took longer than its timeout to connect or to read.
      class PollTimeoutError < StandardError; end

      # Poller makes one download of GET /sdk/v1/ruleset. The poll loop and its interval belong
      # to Sync. etag returns the ETag sent as If-None-Match when not empty; on_ruleset receives
      # a 200 body with its ETag header and returns false for an invalid ruleset. timeout (10 s)
      # is the open and read timeout.
      class Poller
        def initialize(url:, sdk_key:, etag: -> { "" }, on_ruleset: ->(_raw, _etag) { true }, timeout: 10.0)
          @url = url
          @sdk_key = sdk_key
          @etag = etag
          @on_ruleset = on_ruleset
          @timeout = timeout
        end

        # poll downloads the ruleset once. A 304 keeps what we have; a 200 goes to on_ruleset.
        # Raises the status_error error for other statuses, PollTimeoutError after the timeout,
        # InvalidRulesetError, Transport::CancelledError when cancel is set, and the network
        # error (SystemCallError, IOError, ...) otherwise.
        def poll(cancel)
          headers = Transport.headers(@sdk_key)
          etag = @etag.call.to_s
          headers["If-None-Match"] = etag unless etag.empty?
          raw, got = download(cancel, headers)
          return if raw.nil?
          raise Transport::CancelledError if cancel.cancelled?
          raise InvalidRulesetError unless @on_ruleset.call(raw, got)

          nil
        end

        private

        def download(cancel, headers)
          Transport.get(@url, headers: headers, timeout: @timeout, cancel: cancel) do |resp|
            status = resp.code.to_i
            next nil if status == 304
            raise Transport.status_error(status, resp, Time.now) || Transport::HttpError.new(status) if status != 200

            [(resp.body || "").b, resp["ETag"].to_s]
          end
        rescue Net::OpenTimeout, Net::ReadTimeout
          raise PollTimeoutError, "megooflags: ruleset poll timed out after #{@timeout}s"
        end
      end
    end
  end
end
