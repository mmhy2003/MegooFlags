# frozen_string_literal: true

module MegooFlags
  module Internal
    module Delivery
      # A text/event-stream parser over a byte stream (the HTTP response).
      module Sse
        # Event is one dispatched server-sent event. name is "message" by default.
        Event = Data.define(:name, :data, :id)

        DIGITS = /\A[0-9]+\z/
        LF = "\n".b.freeze
        CHUNK = 64 * 1024

        # Reader keeps the parse state across chunks.
        class Reader
          def initialize(on_line:, on_retry:, on_event:)
            @on_line = on_line
            @on_retry = on_retry
            @on_event = on_event
            @buffer = +"".b
            @scanned = 0
            reset
          end

          # feed parses every complete line of chunk and keeps the rest for the next chunk.
          def feed(chunk)
            @buffer << chunk.b
            start = 0
            while (lf = @buffer.index(LF, [start, @scanned].max))
              line(@buffer.byteslice(start, lf - start))
              start = lf + 1
            end
            @buffer = @buffer.byteslice(start..) unless start.zero?
            @scanned = @buffer.bytesize
          end

          private

          def reset
            @name = ""
            @id = ""
            @data = []
          end

          def line(raw)
            raw = raw.byteslice(0, raw.bytesize - 1) if raw.end_with?("\r")
            text = raw.force_encoding(Encoding::UTF_8).scrub
            @on_line.call
            return dispatch if text.empty?
            return if text.start_with?(":")

            field, colon, value = text.partition(":")
            value = value.delete_prefix(" ") unless colon.empty?
            apply_field(field, value)
          end

          def apply_field(name, value)
            case name
            when "event" then @name = value
            when "data" then @data << value
            when "id" then @id = value unless value.include?("\0")
            when "retry" then @on_retry.call(value.to_i) if DIGITS.match?(value)
            end
          end

          def dispatch
            if @data.empty?
              reset
              return
            end
            event = Event.new(name: @name.empty? ? "message" : @name, data: @data.join("\n"), id: @id)
            reset
            @on_event.call(event)
          end
        end

        module_function

        # read parses events until source ends. source is an IO (read with readpartial) or
        # anything whose each yields byte chunks, such as an Enumerator over
        # Net::HTTPResponse#read_body. on_line runs for every line, comments included; on_retry
        # receives a valid retry field in milliseconds; on_event receives each Event.
        #
        # Lines are split on the LF byte before decoding UTF-8 (invalid bytes become U+FFFD), so
        # a character split across chunks decodes whole, and a line of any length is accepted.
        # An unterminated last line or event is discarded. A read error, or an exception raised
        # by a callback, propagates unchanged.
        def read(source, on_line:, on_retry:, on_event:)
          reader = Reader.new(on_line: on_line, on_retry: on_retry, on_event: on_event)
          if source.respond_to?(:readpartial)
            read_io(source, reader)
          else
            source.each { |chunk| reader.feed(chunk) }
          end
          nil
        end

        def read_io(io, reader)
          loop { reader.feed(io.readpartial(CHUNK)) }
        rescue EOFError
          nil
        end
        private_class_method :read_io
      end
    end
  end
end
