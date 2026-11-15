# frozen_string_literal: true

require "digest"
require "fileutils"
require "json"
require "securerandom"

module MegooFlags
  module Internal
    module Delivery
      # CacheError is a cache file that is corrupt, or a ruleset that cannot be cached.
      class CacheError < StandardError; end

      # Cached is a cached ruleset: the decoded ruleset object and its ETag.
      Cached = Data.define(:ruleset, :etag)

      # Cache is the last-known ruleset on disk, in the Go SDK's file format so a cache directory
      # can be shared between SDKs: <dir>/megooflags-<first 16 hex of sha256(key)>.json holding
      # {"etag", "ruleset"}. The key itself is never written to disk. Without a directory, load
      # finds nothing and save does nothing.
      class Cache
        attr_reader :path

        def initialize(cache_dir, sdk_key)
          @dir = cache_dir.nil? || cache_dir.to_s.empty? ? nil : cache_dir.to_s
          @path = @dir && File.join(@dir, "megooflags-#{Digest::SHA256.hexdigest(sdk_key.b)[0, 16]}.json")
        end

        # load returns the Cached ruleset, or nil when there is no file. Raises SystemCallError
        # for a file it cannot read and CacheError for corrupt content.
        def load
          return nil unless @path

          begin
            data = File.binread(@path)
          rescue Errno::ENOENT
            return nil
          end
          parse(data)
        end

        # save writes the ruleset JSON text, embedded as received, and its ETag to a temp file in
        # the cache directory and renames it over the cache file, so a reader never sees a
        # partial write. Raises CacheError when raw is not JSON and SystemCallError when the
        # write fails; leaves no temp file behind.
        def save(raw, etag)
          return unless @path

          text = raw.dup.force_encoding(Encoding::UTF_8).scrub
          begin
            JSON.parse(text)
          rescue JSON::ParserError => e
            raise CacheError, "megooflags: cannot cache an invalid ruleset: #{e.message}"
          end
          write(%({"etag":#{JSON.generate(etag.to_s)},"ruleset":#{text}}\n))
        end

        private

        def parse(data)
          file = JSON.parse(data.force_encoding(Encoding::UTF_8))
          raise corrupt("not a JSON object") unless file.is_a?(Hash)

          etag = file.fetch("etag", "")
          raise corrupt("etag is not a string") unless etag.is_a?(String)
          raise corrupt("no ruleset") unless file.key?("ruleset")

          Cached.new(ruleset: file["ruleset"], etag: etag)
        rescue JSON::ParserError, EncodingError => e
          raise corrupt(e.message)
        end

        def write(content)
          FileUtils.mkdir_p(@dir, mode: 0o700)
          tmp = File.join(@dir, ".megooflags-#{SecureRandom.hex(8)}.tmp")
          begin
            File.open(tmp, File::WRONLY | File::CREAT | File::EXCL | File::BINARY, 0o600) do |f|
              f.write(content)
              f.flush
              f.fsync
            end
            File.chmod(0o600, tmp)
            File.rename(tmp, @path)
          rescue StandardError
            FileUtils.rm_f(tmp)
            raise
          end
        end

        def corrupt(why) = CacheError.new("megooflags: corrupt cache file #{@path}: #{why}")
      end
    end
  end
end
