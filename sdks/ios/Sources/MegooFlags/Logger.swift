import os

/// The level of a log line.
public enum MegooFlagsLogLevel: Sendable {
    case debug, info, warn, error
}

/// Receives the SDK's log lines. Messages start with "megooflags: " and never
/// contain the SDK key.
public protocol MegooFlagsLogger: Sendable {
    func log(level: MegooFlagsLogLevel, message: String)
}

/// The default logger: unified logging under the SDK's subsystem.
public struct OSLogLogger: MegooFlagsLogger {
    static let subsystem = "io.github.mmhy2003.megooflags"

    private let logger = Logger(subsystem: OSLogLogger.subsystem, category: "MegooFlags")

    public init() {}

    public func log(level: MegooFlagsLogLevel, message: String) {
        switch level {
        case .debug: logger.debug("\(message, privacy: .public)")
        case .info: logger.info("\(message, privacy: .public)")
        case .warn: logger.warning("\(message, privacy: .public)")
        case .error: logger.error("\(message, privacy: .public)")
        }
    }
}
