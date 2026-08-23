import XCTest

@testable import OpenChronicleApp

final class PrivacyIndicatorPlacementTests: XCTestCase {
  func testValuesTitlesAndDefault() {
    XCTAssertEqual(
      PrivacyIndicatorPlacementOption.allCases.map(\.rawValue),
      ["bottom-left-flush", "bottom-left-inset", "bottom-right-work-area"]
    )
    XCTAssertEqual(PrivacyIndicatorPlacementOption.defaultValue, .bottomLeftFlush)
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomLeftFlush.title, "左下角贴边")
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomLeftInset.title, "左下角留白")
    XCTAssertEqual(PrivacyIndicatorPlacementOption.bottomRightWorkArea.title, "右下角避开 Dock")
  }

  func testStyleAvailability() {
    XCTAssertFalse(PrivacyIndicatorPlacementOption.isEnabled(for: .off))
    XCTAssertFalse(PrivacyIndicatorPlacementOption.isEnabled(for: .banner))
    XCTAssertTrue(PrivacyIndicatorPlacementOption.isEnabled(for: .pill))
    XCTAssertTrue(PrivacyIndicatorPlacementOption.isEnabled(for: .border))
  }
}
