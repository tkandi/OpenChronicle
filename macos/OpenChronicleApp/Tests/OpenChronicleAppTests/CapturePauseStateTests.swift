import Darwin
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

  func testPauseStoreUsesOwnerOnlySiblingLockAndAtomicUpdate() throws {
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    let original = CapturePauseState.timed(duration: 30 * 60, now: now)
    try store.save(original)

    let updated = try store.update(now: now) { current in
      var current = try XCTUnwrap(current)
      current.resumeAt = now.addingTimeInterval(60 * 60)
      return current
    }

    XCTAssertEqual(store.lockFileURL.path, store.fileURL.path + ".lock")
    XCTAssertEqual(store.load(), updated)
    let attributes = try FileManager.default.attributesOfItem(
      atPath: store.lockFileURL.path
    )
    let permissions = try XCTUnwrap(
      attributes[FileAttributeKey.posixPermissions] as? NSNumber
    )
    XCTAssertEqual(permissions.intValue & 0o777, 0o600)
  }

  func testPauseStoreSaveWaitsForExistingProcessLock() throws {
    let lockURL = URL(fileURLWithPath: store.fileURL.path + ".lock")
    let descriptor = Darwin.open(
      lockURL.path,
      O_RDWR | O_CREAT,
      S_IRUSR | S_IWUSR
    )
    XCTAssertGreaterThanOrEqual(descriptor, 0)
    guard descriptor >= 0 else { return }
    XCTAssertEqual(capturePauseFlock(descriptor, LOCK_EX), 0)

    let started = DispatchSemaphore(value: 0)
    let completed = DispatchSemaphore(value: 0)
    let errorLock = NSLock()
    var saveError: Error?
    let state = CapturePauseState.indefinite(
      now: Date(timeIntervalSince1970: 2_000_000_000)
    )
    let store = try XCTUnwrap(store)
    DispatchQueue.global().async {
      started.signal()
      do {
        try store.save(state)
      } catch {
        errorLock.lock()
        saveError = error
        errorLock.unlock()
      }
      completed.signal()
    }

    XCTAssertEqual(started.wait(timeout: .now() + 1), .success)
    XCTAssertEqual(completed.wait(timeout: .now() + 0.1), .timedOut)
    XCTAssertEqual(capturePauseFlock(descriptor, LOCK_UN), 0)
    Darwin.close(descriptor)
    XCTAssertEqual(completed.wait(timeout: .now() + 1), .success)
    errorLock.lock()
    let observedError = saveError
    errorLock.unlock()
    XCTAssertNil(observedError)
    XCTAssertEqual(store.load(), state)
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
