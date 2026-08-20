import XCTest

@testable import OpenChronicleApp

final class PrivacyIndicatorStyleTests: XCTestCase {
  func testAllConfigValuesAndDefaultAreStable() {
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.allCases.map(\.rawValue),
      ["off", "border", "shield", "pill", "quiet-shield", "banner"]
    )
    XCTAssertEqual(PrivacyIndicatorStyleOption.defaultStyle, .pill)
    XCTAssertEqual(PrivacyIndicatorStyleOption.pill.title, "B2 · 已保护")
  }
}
