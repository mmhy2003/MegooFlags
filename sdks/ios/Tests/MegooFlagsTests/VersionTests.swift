import Foundation
import XCTest

@testable import MegooFlags

final class VersionTests: XCTestCase {
    func testVersion() throws {
        XCTAssertEqual(megooFlagsVersion, "0.1.0")

        // The same pattern scripts/check_sdk_version.py uses (SWIFT_RE).
        let file = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../../Sources/MegooFlags/Version.swift")
            .standardizedFileURL
        let text = try String(contentsOf: file, encoding: .utf8)
        let pattern = try NSRegularExpression(pattern: #"\bpublic\s+let\s+megooFlagsVersion\s*=\s*"([^"]*)""#)
        let match = try XCTUnwrap(pattern.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)))
        let range = try XCTUnwrap(Range(match.range(at: 1), in: text))
        XCTAssertEqual(String(text[range]), megooFlagsVersion)
    }
}
