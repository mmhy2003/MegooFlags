# frozen_string_literal: true

require "json"
require "tmpdir"
require_relative "test_helper"
require "megooflags/internal/engine/bucketing"
require "megooflags/internal/engine/parser"
require "megooflags/internal/engine/evaluator"

# Runs the language-neutral suite in spec/conformance against the engine: one
# test per <file stem>::<name> and per bucketing::<input>.
class ConformanceTest < Minitest::Test
  Engine = MegooFlags::Internal::Engine
  CONFORMANCE = File.expand_path("../../../spec/conformance", __dir__)
  CASE_FILES = Dir[File.join(CONFORMANCE, "cases", "*.json")].freeze # Dir[] sorts
  SCHEMA_VERSION = 1

  # load_suite reads a suite file, refusing any schema_version but the Integer 1.
  def self.load_suite(path)
    data = JSON.parse(File.read(path, encoding: "UTF-8"))
    version = data["schema_version"]
    raise ArgumentError, "#{File.basename(path)}: unknown schema_version #{version.inspect}" \
      unless version.is_a?(Integer) && version == SCHEMA_VERSION

    data
  end

  # same_json? is JSON equality: 1 == 1.0, but true != 1, "1" != 1 and {} != [].
  def self.same_json?(left, right)
    case left
    when true, false then left.equal?(right)
    when Integer, Float then [Integer, Float].include?(right.class) && left == right
    when String then right.is_a?(String) && left == right
    when nil then right.nil?
    when Array
      right.is_a?(Array) && left.size == right.size && left.zip(right).all? { |a, b| same_json?(a, b) }
    when Hash
      right.is_a?(Hash) && left.keys.sort == right.keys.sort && left.all? { |k, v| same_json?(v, right[k]) }
    else false
    end
  end

  def self.parsed
    @parsed ||= {}
  end

  # ruleset_for parses a file's ruleset once; a parse error fails each test of the file.
  def self.ruleset_for(path, suite)
    parsed[path] ||= Engine::Parser.parse(suite["ruleset"])
  end

  def self.names
    @names ||= []
  end

  def self.define_case(id, &)
    names << id
    define_method("test_#{id}", &)
  end

  CASE_FILES.each do |path|
    suite = load_suite(path)
    stem = File.basename(path, ".json")
    suite["tests"].each do |kase|
      define_case("#{stem}::#{kase["name"]}") { check_case(self.class.ruleset_for(path, suite), kase) }
    end
  end

  BUCKETING = File.join(CONFORMANCE, "bucketing.json")
  VECTORS = File.exist?(BUCKETING) ? load_suite(BUCKETING)["vectors"] : []
  VECTORS.each do |vector|
    define_case("bucketing::#{vector["input"]}") do
      flag, text = vector["input"].split(".", 2)
      assert_equal vector["bucket"], Engine::Bucketing.bucket(flag, text)
    end
  end

  def check_case(ruleset, kase)
    result = Engine::Evaluator.evaluate(ruleset, kase["flag"], kase["context"], kase["default"], kase["expected_type"])
    expected = kase["expected"]
    assert self.class.same_json?(result.value, expected["value"]),
           "value #{result.value.inspect} != #{expected["value"].inspect}"
    assert_same_or_nil expected["variant"], result.variant, "variant"
    assert_equal expected["reason"], result.reason
    assert_same_or_nil expected["error_code"], result.error_code, "error_code"
    metadata = result.metadata
    expected["metadata"].each do |key, value|
      assert metadata.key?(key), "metadata #{key} missing from #{metadata.inspect}"
      assert self.class.same_json?(metadata[key], value),
             "metadata #{key}: #{metadata[key].inspect} != #{value.inspect}"
    end
  end

  def assert_same_or_nil(expected, actual, what)
    expected.nil? ? assert_nil(actual, what) : assert_equal(expected, actual, what)
  end

  def test_has_case_files_and_vectors
    refute_empty CASE_FILES
    assert_operator VECTORS.size, :>=, 20
  end

  def test_case_ids_are_unique
    assert_equal self.class.names.uniq.size, self.class.names.size
  end

  def test_other_schema_versions_are_refused
    Dir.mktmpdir do |dir|
      path = File.join(dir, "cases.json")
      ["2", "1.0", '"1"', "true", "null"].each do |version|
        File.write(path, %({"schema_version": #{version}, "tests": []}))
        error = assert_raises(ArgumentError) { self.class.load_suite(path) }
        assert_match(/unknown schema_version/, error.message)
      end
    end
  end

  def test_same_json
    [[1, 1.0, true], [true, 1, false], ["1", 1, false], [nil, nil, true], [nil, false, false],
     [[1, { "a" => true }], [1.0, { "a" => true }], true], [[1], [1, 2], false],
     [{ "a" => 1 }, { "a" => 1, "b" => 2 }, false], [{ "a" => 0 }, { "a" => false }, false],
     [{}, [], false]].each do |left, right, equal|
      assert_equal equal, self.class.same_json?(left, right), [left, right].inspect
    end
  end
end
