# frozen_string_literal: true

module MegooFlags
  module Internal
    # The default logger. logger is a bundled gem from Ruby 4.0 and cannot be required under
    # Bundler unless the application declares it, so the SDK falls back to Stderr instead of
    # adding a dependency.
    module Log
      # Stderr writes warnings and errors with Kernel#warn and drops debug and info, like a
      # Logger at level WARN.
      class Stderr
        def debug(_message = nil, &) = nil

        def info(_message = nil, &) = nil

        def warn(message = nil, &) = write("WARN", message, &)

        def error(message = nil, &) = write("ERROR", message, &)

        private

        def write(level, message)
          message = yield if message.nil? && block_given?
          Kernel.warn("megooflags #{level}: #{message}")
          nil
        end
      end

      module_function

      # default_logger is Logger.new($stderr, level: Logger::WARN), or a Stderr when the logger
      # gem cannot be loaded.
      def default_logger
        require "logger"
        ::Logger.new($stderr, level: ::Logger::WARN)
      rescue LoadError
        Stderr.new
      end
    end
  end
end
