import XCTest

@testable import OpenChronicleApp

final class MainWindowNavigationTests: XCTestCase {
  func testSidebarGroupsCoverEverySectionExactlyOnce() {
    let combined =
      MainWindowSection.controlSections
      + MainWindowSection.configurationSections

    XCTAssertEqual(combined.count, MainWindowSection.allCases.count)
    XCTAssertEqual(Set(combined), Set(MainWindowSection.allCases))
    XCTAssertTrue(MainWindowSection.models.isConfiguration)
    XCTAssertFalse(MainWindowSection.overview.isConfiguration)
  }

  func testProtectionDiagnosticsIsAStableControlSection() {
    let occurrences = (
      MainWindowSection.controlSections
        + MainWindowSection.configurationSections
    ).filter { $0 == .protectionDiagnostics }

    XCTAssertEqual(occurrences.count, 1)
    XCTAssertTrue(MainWindowSection.controlSections.contains(.protectionDiagnostics))
    XCTAssertFalse(MainWindowSection.configurationSections.contains(.protectionDiagnostics))
    XCTAssertFalse(MainWindowSection.protectionDiagnostics.isConfiguration)
    XCTAssertEqual(MainWindowSection.protectionDiagnostics.sidebarTitle, "Diagnostics")
    XCTAssertEqual(MainWindowSection.protectionDiagnostics.title, "Protection Diagnostics")
    XCTAssertEqual(
      MainWindowSection.protectionDiagnostics.subtitle,
      "Per-display reasons and privacy guard state"
    )
    XCTAssertEqual(MainWindowSection.protectionDiagnostics.systemImage, "checkmark.shield")
  }

  func testSidebarTitlesAreUnique() {
    let sidebarTitles = MainWindowSection.allCases.map(\.sidebarTitle)

    XCTAssertEqual(Set(sidebarTitles).count, MainWindowSection.allCases.count)
  }

  @MainActor
  func testNavigatorPreservesTheLastPageUnlessExplicitlyChanged() {
    let navigator = MainWindowNavigator(selection: .overview)

    XCTAssertEqual(navigator.selectedSection, .overview)
    navigator.select(.models)
    XCTAssertEqual(navigator.selectedSection, .models)
    navigator.select(nil)
    XCTAssertEqual(navigator.selectedSection, .models)
    navigator.selection = nil
    XCTAssertEqual(navigator.selectedSection, .overview)
  }
}
