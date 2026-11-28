// A text/event-stream parser over a byte stream (the stream response body).
import Foundation

/// One dispatched server-sent event. name is "message" by default.
struct SSEEvent: Equatable, Sendable {
    var name: String
    var data: String
    var id: String
}

/// SSEParser parses the chunks fed to it. Lines are split on the LF byte
/// before decoding, so a character split across chunks decodes whole, and a
/// line of any length is accepted; a trailing CR is removed. An error thrown
/// by onEvent is passed through feed and stops reading. finish discards an
/// unterminated last line or event.
final class SSEParser {
    /// Runs for every line, comments included (the dead timer).
    private let onLine: () -> Void
    /// Receives a valid retry field, in milliseconds.
    private let onRetry: (Int) -> Void
    /// Receives each event.
    private let onEvent: (SSEEvent) throws -> Void

    private var pending = Data()
    private var name = ""
    private var id = ""
    private var data: [String] = []
    private var seen = false

    init(onLine: @escaping () -> Void, onRetry: @escaping (Int) -> Void, onEvent: @escaping (SSEEvent) throws -> Void) {
        self.onLine = onLine
        self.onRetry = onRetry
        self.onEvent = onEvent
    }

    func feed(_ chunk: Data) throws {
        var start = chunk.startIndex
        while let end = chunk[start...].firstIndex(of: 0x0a) {
            var line = chunk[start..<end]
            if !pending.isEmpty {
                pending.append(line)
                line = pending
                pending = Data()
            }
            if line.last == 0x0d {
                line = line.dropLast()
            }
            start = end + 1
            try self.line(String(decoding: line, as: UTF8.self))
        }
        pending.append(chunk[start...])
    }

    /// finish ends the stream: an unterminated line or event is dropped.
    func finish() {
        pending = Data()
        reset()
    }

    private func line(_ line: String) throws {
        onLine()
        if line.isEmpty {
            try dispatch()
            return
        }
        if line.hasPrefix(":") {
            return
        }
        var field = Substring(line)
        var value = Substring("")
        if let colon = line.firstIndex(of: ":") {
            field = line[..<colon]
            value = line[line.index(after: colon)...]
            if value.hasPrefix(" ") {
                value = value.dropFirst()
            }
        }
        switch field {
        case "event":
            name = String(value)
        case "data":
            data.append(String(value))
            seen = true
        case "id":
            if !value.contains("\u{0}") {
                id = String(value)
            }
        case "retry":
            if !value.isEmpty, value.allSatisfy({ $0.isASCII && $0.isNumber }), let ms = Int(value) {
                onRetry(ms)
            }
        default:
            break
        }
    }

    private func dispatch() throws {
        let event =
            seen ? SSEEvent(name: name.isEmpty ? "message" : name, data: data.joined(separator: "\n"), id: id) : nil
        reset()
        if let event {
            try onEvent(event)
        }
    }

    private func reset() {
        name = ""
        id = ""
        data = []
        seen = false
    }
}
