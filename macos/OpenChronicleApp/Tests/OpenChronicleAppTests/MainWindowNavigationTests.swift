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
