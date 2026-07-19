import Foundation
import UserNotifications
import XCTest

@testable import OpenChronicleApp

final class CapturePauseStateTests: XCTestCase {
  private var root: URL!
  private var store: CapturePauseStateStore!

  override func setUpWithError() throws {
    root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    store = CapturePauseStateStore(fileURL: root.appendingPathComponent(".paused"))
  }

  override func tearDownWithError() throws {
    try? FileManager.default.removeItem(at: root)
  }

  func testTimedPausePreservesAFullWarningMinute() {
    let deadline = Date(timeIntervalSince1970: 2_000_000_000)
    var state = CapturePauseState.timed(duration: 30 * 60, now: deadline.addingTimeInterval(-1800))
    state.resumeArmedAt = deadline.addingTimeInterval(10)

    XCTAssertEqual(
      state.effectiveResumeAt,
      deadline.addingTimeInterval(70)
    )
  }

  func testStaleHeartbeatRequiresWarningToBeRearmedAfterWake() {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    var state = CapturePauseState.timed(duration: 30 * 60, now: now.addingTimeInterval(-1900))
    state.resumeArmedAt = now.addingTimeInterval(-120)
    state.appHeartbeatAt = now.addingTimeInterval(-120)

    XCTAssertTrue(state.needsWakeRearm(at: now))

    state.appHeartbeatAt = now
    XCTAssertFalse(state.needsWakeRearm(at: now))
  }

  func testLegacyTimestampLoadsAsIndefinitePause() throws {
    let startedAt = "2026-07-19T12:30:00Z"
    try Data(startedAt.utf8).write(to: store.fileURL)

    let state = try XCTUnwrap(store.load())

    XCTAssertEqual(state.mode, .indefinite)
    XCTAssertNil(state.resumeAt)
    XCTAssertTrue(state.id.hasPrefix("legacy-"))
  }

  func testStructuredPauseRoundTripsAndClears() throws {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    var expected = CapturePauseState.timed(duration: 60 * 60, now: now)
    expected.appHeartbeatAt = now.addingTimeInterval(30)
    try store.save(expected)

    XCTAssertEqual(store.load(), expected)
    try store.clear()
    XCTAssertNil(store.load())
  }

  func testIndefiniteReminderUsesOneHourThenTwoHourIntervals() {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    var state = CapturePauseState.indefinite(now: now)

    XCTAssertEqual(state.nextIndefiniteReminderAt, now.addingTimeInterval(60 * 60))

    state.lastReminderAt = now.addingTimeInterval(60 * 60)
    XCTAssertEqual(state.nextIndefiniteReminderAt, now.addingTimeInterval(3 * 60 * 60))
  }

  @MainActor
  func testPreparingForSleepDisarmsTimedResume() throws {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    var state = CapturePauseState.timed(duration: 30 * 60, now: now)
    state.resumeArmedAt = now.addingTimeInterval(29 * 60)
    state.appHeartbeatAt = now.addingTimeInterval(29 * 60)
    try store.save(state)
    let controller = makeController()

    controller.prepareForSleep()

    let persisted = try XCTUnwrap(store.load())
    XCTAssertNil(persisted.resumeArmedAt)
    XCTAssertNil(persisted.appHeartbeatAt)
  }

  @MainActor
  func testStaleNotificationActionCannotChangeNewerPause() throws {
    let state = CapturePauseState.indefinite(
      now: Date(timeIntervalSince1970: 2_000_000_000)
    )
    try store.save(state)
    let controller = makeController()

    XCTAssertFalse(
      controller.handleNotificationAction(
        CapturePauseController.resumeActionIdentifier,
        pauseID: "older-pause"
      )
    )
    XCTAssertNotNil(store.load())

    XCTAssertTrue(
      controller.handleNotificationAction(
        CapturePauseController.resumeActionIdentifier,
        pauseID: state.id
      )
    )
    XCTAssertNil(store.load())
  }

  @MainActor
  private func makeController() -> CapturePauseController {
    let paths = RuntimePaths(root: root)
    let locator = BackendLocator(
      environment: [:],
      homeDirectory: root,
      bundleResources: nil
    )
    let backend = BackendController(paths: paths, locator: locator)
    return CapturePauseController(
      paths: paths,
      backend: backend,
      notificationCenter: CapturePauseNotificationCenterMock()
    )
  }
}

private final class CapturePauseNotificationCenterMock: CapturePauseNotificationCenter {
  func notificationSettings() async -> UNNotificationSettings {
    fatalError("notification settings are not used by these state tests")
  }

  func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool {
    true
  }

  func add(_ request: UNNotificationRequest) async throws {}

  func setNotificationCategories(_ categories: Set<UNNotificationCategory>) {}

  func removeDeliveredNotifications(withIdentifiers identifiers: [String]) {}
}
