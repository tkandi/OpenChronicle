import XCTest

@testable import OpenChronicleApp

final class PrivacyReasonOptionsTests: XCTestCase {
  func testReasonOptionsAndDefaultsAreStable() {
    XCTAssertEqual(PrivacyReasonDisplayOption.allCases.map(\.rawValue), [
      "overlay", "diagnostics", "hybrid",
    ])
    XCTAssertEqual(PrivacyReasonDisplayOption.defaultValue, .hybrid)
    XCTAssertEqual(PrivacyReasonDetailOption.defaultValue, .exact)
    XCTAssertEqual(PrivacyReasonTriggerOption.defaultValue, .hover)
  }
}
