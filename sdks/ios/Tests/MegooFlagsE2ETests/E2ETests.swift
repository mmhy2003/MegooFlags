import Foundation
import XCTest

final class E2ETests: XCTestCase {
    func testSkipsWithoutEnvironment() throws {
        try XCTSkipIf(ProcessInfo.processInfo.environment["MEGOOFLAGS_E2E_URL"] == nil)
    }
}
