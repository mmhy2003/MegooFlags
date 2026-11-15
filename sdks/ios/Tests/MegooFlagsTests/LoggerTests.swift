import Foundation
import XCTest

@testable import MegooFlags

private final class CapturingLogger: MegooFlagsLogger, @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []

    func log(level: MegooFlagsLogLevel, message: String) {
        lock.lock()
        defer { lock.unlock() }
        lines.append("\(level) \(message)")
    }

    var captured: [String] {
        lock.lock()
        defer { lock.unlock() }
        return lines
    }
}

final class LoggerTests: XCTestCase {
    func testTheDefaultLoggerUsesTheSDKSubsystem() {
        XCTAssertEqual(OSLogLogger.subsystem, "io.github.mmhy2003.megooflags")
        let logger: any MegooFlagsLogger = OSLogLogger()
        for level in [MegooFlagsLogLevel.debug, .info, .warn, .error] {
            logger.log(level: level, message: "megooflags: logger test")
        }
    }

    func testACustomLoggerReceivesLevelAndMessage() {
        let logger = CapturingLogger()
        let any: any MegooFlagsLogger = logger
        any.log(level: .debug, message: "megooflags: stream connected")
        any.log(level: .warn, message: "megooflags: stream failed: x")
        XCTAssertEqual(logger.captured, ["debug megooflags: stream connected", "warn megooflags: stream failed: x"])
    }
}
