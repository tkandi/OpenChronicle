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

  func testPreviewDescriptorsMatchRuntimeCompositionAndPlacement() {
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.off.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(composition: .none, placement: .none, text: nil)
    )
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.border.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(
        composition: .borderAndBadge,
        placement: .lowerTrailing,
        text: "已保护"
      )
    )
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.shield.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(
        composition: .solidShield,
        placement: .lowerTrailing,
        text: nil
      )
    )
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.pill.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(
        composition: .pill,
        placement: .lowerTrailing,
        text: "已保护"
      )
    )
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.quietShield.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(
        composition: .quietShield,
        placement: .lowerTrailing,
        text: nil
      )
    )
    XCTAssertEqual(
      PrivacyIndicatorStyleOption.banner.previewDescriptor,
      PrivacyIndicatorPreviewDescriptor(composition: .banner, placement: .top, text: "已保护")
    )
  }
}
