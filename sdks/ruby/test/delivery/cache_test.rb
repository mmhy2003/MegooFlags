# frozen_string_literal: true

require_relative "../test_helper"
require "json"
require "tmpdir"
require "megooflags/internal/delivery/cache"

class CacheTest < Minitest::Test
  Delivery = MegooFlags::Internal::Delivery

  KEY = "srv-secret-key"
  RULESET = '{"version":1,"flags":{"a":{"on":true}},"segments":{}}'

  def setup
    @dir = Dir.mktmpdir("megooflags-cache-test")
  end

  def teardown
    FileUtils.rm_rf(@dir)
  end

  def cache(dir = @dir) = Delivery::Cache.new(dir, KEY)

  def test_round_trip
    cache.save(RULESET, '"etag-1"')
    got = cache.load

    assert_equal JSON.parse(RULESET), got.ruleset
    assert_equal '"etag-1"', got.etag
  end

  def test_missing_file_is_nil
    assert_nil cache.load
  end

  def test_corrupt_file_raises
    cache.save(RULESET, "e")
    File.write(cache.path, "{not json")

    err = assert_raises(Delivery::CacheError) { cache.load }
    assert_includes err.message, "corrupt cache file"
  end

  def test_wrong_shapes_are_corrupt
    ['["x"]', '{"etag":1,"ruleset":{}}', '{"etag":"e"}'].each do |content|
      FileUtils.mkdir_p(@dir)
      File.write(cache.path, content)

      assert_raises(Delivery::CacheError, content) { cache.load }
    end
  end

  def test_unreadable_file_raises_a_system_call_error
    Dir.mkdir(cache.path)

    assert_raises(SystemCallError) { cache.load }
  end

  def test_file_name_is_16_hex_of_the_key_hash_and_never_the_key
    name = File.basename(cache.path)

    assert_equal "megooflags-#{Digest::SHA256.hexdigest(KEY)[0, 16]}.json", name
    assert_match(/\Amegooflags-[0-9a-f]{16}\.json\z/, name)
    refute_includes name, KEY
    refute_equal name, File.basename(Delivery::Cache.new(@dir, "other").path)
  end

  def test_file_mode_is_owner_read_write_only
    cache.save(RULESET, "e")

    assert_equal 0o600, File.stat(cache.path).mode & 0o777
  end

  def test_atomic_replace_leaves_no_temp_file
    cache.save(RULESET, "e1")
    cache.save('{"version":1,"flags":{},"segments":{}}', "e2")

    assert_equal [File.basename(cache.path)], Dir.children(@dir)
    assert_equal "e2", cache.load.etag
  end

  def test_creates_the_directory_for_the_owner_only
    dir = File.join(@dir, "nested", "cache")
    cache(dir).save(RULESET, "e")

    assert_equal 0o700, File.stat(dir).mode & 0o777
    assert_equal "e", cache(dir).load.etag
  end

  def test_no_cache_directory_loads_nothing_and_saves_nothing
    [nil, ""].each do |dir|
      c = cache(dir)
      c.save(RULESET, "e")

      assert_nil c.load
      assert_nil c.path
    end
    assert_empty Dir.children(@dir)
  end

  def test_file_is_the_go_sdk_format
    cache.save(RULESET, '"etag-1"')
    file = JSON.parse(File.read(cache.path))

    assert_equal %w[etag ruleset], file.keys
    assert_equal '"etag-1"', file["etag"]
    assert_equal JSON.parse(RULESET), file["ruleset"]
  end

  def test_reads_a_file_written_by_the_go_sdk
    FileUtils.mkdir_p(@dir)
    File.write(cache.path, %({"etag":"g","ruleset":#{RULESET}}\n))

    assert_equal "g", cache.load.etag
  end

  def test_saving_an_invalid_ruleset_raises_and_writes_nothing
    assert_raises(Delivery::CacheError) { cache.save("{oops", "e") }
    assert_empty Dir.children(@dir)
  end
end
