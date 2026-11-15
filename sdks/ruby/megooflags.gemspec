# frozen_string_literal: true

require_relative "lib/megooflags/version"

Gem::Specification.new do |spec|
  spec.name = "megooflags"
  spec.version = MegooFlags::VERSION
  spec.authors = ["MegooFlags contributors"]
  spec.summary = "MegooFlags server SDK: an OpenFeature provider that evaluates flags in-process"
  spec.description = "Downloads the MegooFlags ruleset for an environment, follows changes over SSE " \
                     "with a polling fallback and evaluates flags locally, as an OpenFeature provider."
  spec.homepage = "https://github.com/mmhy2003/MegooFlags"
  spec.license = "Apache-2.0"
  spec.required_ruby_version = ">= 3.4"

  spec.metadata = {
    "homepage_uri" => spec.homepage,
    "source_code_uri" => "https://github.com/mmhy2003/MegooFlags/tree/main/sdks/ruby",
    "rubygems_mfa_required" => "true"
  }

  spec.files = Dir["lib/**/*.rb", "LICENSE", "README.md"]
  spec.require_paths = ["lib"]

  spec.add_dependency "openfeature-sdk", "~> 0.8"
end
