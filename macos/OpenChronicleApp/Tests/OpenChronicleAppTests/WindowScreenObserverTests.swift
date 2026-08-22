import CoreGraphics
import XCTest

@testable import OpenChronicleApp

private final class TestWindowScreenSettleScheduler {
  final class Job {
    let delay: TimeInterval
    let action: () -> Void
    var cancelled = false

    init(delay: TimeInterval, action: @escaping () -> Void) {
      self.delay = delay
      self.action = action
    }
  }

  private(set) var jobs: [Job] = []

  func schedule(delay: TimeInterval, action: @escaping () -> Void) -> () -> Void {
    let job = Job(delay: delay, action: action)
    jobs.append(job)
    return { job.cancelled = true }
  }
}

@MainActor
final class WindowScreenObserverTests: XCTestCase {
  func testSelectionRequiresExactlyOnePositiveValidScreenIntersection() {
    let screens = [
      WindowScreenGeometry(
        displayID: 1,
        frame: CGRect(x: 0, y: 0, width: 100, height: 100)
      ),
      WindowScreenGeometry(
        displayID: 2,
        frame: CGRect(x: 100, y: 0, width: 100, height: 100)
      ),
    ]

    XCTAssertEqual(
      WindowDisplaySelection.singleIntersectingDisplayID(
        windowFrame: CGRect(x: 10, y: 10, width: 80, height: 80),
        screens: screens
      ),
      1
    )
    XCTAssertNil(
      WindowDisplaySelection.singleIntersectingDisplayID(
        windowFrame: CGRect(x: 80, y: 10, width: 40, height: 80),
        screens: screens
      )
    )
    XCTAssertNil(
      WindowDisplaySelection.singleIntersectingDisplayID(
        windowFrame: CGRect(x: -80, y: 10, width: 80, height: 80),
        screens: screens
      )
    )
    XCTAssertNil(
      WindowDisplaySelection.singleIntersectingDisplayID(
        windowFrame: CGRect(x: 210, y: 10, width: 80, height: 80),
        screens: screens
      )
    )
    XCTAssertNil(
      WindowDisplaySelection.singleIntersectingDisplayID(
        windowFrame: CGRect(x: 10, y: 10, width: 80, height: 80),
        screens: [WindowScreenGeometry(displayID: nil, frame: screens[0].frame)]
      )
    )
  }

  func testMoveAndResizeConcealSynchronouslyUntilOneBoundedSettle() {
    let window = NSWindow(
      contentRect: CGRect(x: 0, y: 0, width: 80, height: 80),
      styleMask: [.titled, .resizable],
      backing: .buffered,
      defer: false
    )
    var frame = CGRect(x: 10, y: 10, width: 80, height: 80)
    let screens = [
      WindowScreenGeometry(displayID: 1, frame: CGRect(x: 0, y: 0, width: 100, height: 100)),
      WindowScreenGeometry(displayID: 2, frame: CGRect(x: 100, y: 0, width: 100, height: 100)),
    ]
    var changes: [UInt32?] = []
    let scheduler = TestWindowScreenSettleScheduler()
    let coordinator = WindowScreenObserver.Coordinator(
      onDisplayChange: { changes.append($0) },
      screenGeometryProvider: { screens },
      windowFrameProvider: { _ in frame },
      settleScheduler: scheduler.schedule
    )

    coordinator.attach(to: window)
    XCTAssertEqual(changes, [1])

    NotificationCenter.default.post(name: NSWindow.willMoveNotification, object: window)
    XCTAssertNil(changes.last!)
    frame = CGRect(x: 80, y: 10, width: 40, height: 80)
    NotificationCenter.default.post(name: NSWindow.didChangeScreenNotification, object: window)
    XCTAssertTrue(scheduler.jobs.isEmpty)
    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: window)
    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: window)

    XCTAssertEqual(scheduler.jobs.count, 2)
    XCTAssertTrue(scheduler.jobs[0].cancelled)
    XCTAssertGreaterThan(scheduler.jobs[1].delay, 0)
    XCTAssertLessThanOrEqual(scheduler.jobs[1].delay, 0.5)
    let countBeforeStaleMove = changes.count
    scheduler.jobs[0].action()
    XCTAssertEqual(changes.count, countBeforeStaleMove)
    scheduler.jobs[1].action()
    XCTAssertNil(changes.last!)

    NotificationCenter.default.post(
      name: NSWindow.willStartLiveResizeNotification,
      object: window
    )
    frame = CGRect(x: 110, y: 10, width: 80, height: 80)
    NotificationCenter.default.post(name: NSWindow.didResizeNotification, object: window)
    XCTAssertEqual(scheduler.jobs.count, 2)
    XCTAssertNil(changes.last!)
    NotificationCenter.default.post(name: NSWindow.didEndLiveResizeNotification, object: window)
    XCTAssertEqual(scheduler.jobs.count, 3)
    XCTAssertNil(changes.last!)
    scheduler.jobs[2].action()
    XCTAssertEqual(changes.last!, 2)
  }

  func testWindowReplacementAndDetachRejectStaleCallbacks() {
    let first = NSWindow(
      contentRect: CGRect(x: 0, y: 0, width: 80, height: 80),
      styleMask: [.titled],
      backing: .buffered,
      defer: false
    )
    let second = NSWindow(
      contentRect: CGRect(x: 100, y: 0, width: 80, height: 80),
      styleMask: [.titled],
      backing: .buffered,
      defer: false
    )
    let frames = [
      ObjectIdentifier(first): CGRect(x: 10, y: 10, width: 80, height: 80),
      ObjectIdentifier(second): CGRect(x: 110, y: 10, width: 80, height: 80),
    ]
    let screens = [
      WindowScreenGeometry(displayID: 1, frame: CGRect(x: 0, y: 0, width: 100, height: 100)),
      WindowScreenGeometry(displayID: 2, frame: CGRect(x: 100, y: 0, width: 100, height: 100)),
    ]
    var changes: [UInt32?] = []
    let scheduler = TestWindowScreenSettleScheduler()
    let coordinator = WindowScreenObserver.Coordinator(
      onDisplayChange: { changes.append($0) },
      screenGeometryProvider: { screens },
      windowFrameProvider: { frames[ObjectIdentifier($0)]! },
      settleScheduler: scheduler.schedule
    )

    coordinator.attach(to: first)
    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: first)
    let staleFirstJob = scheduler.jobs[0]
    coordinator.attach(to: second)
    XCTAssertTrue(staleFirstJob.cancelled)
    XCTAssertEqual(changes.last!, 2)

    let countAfterReplacement = changes.count
    staleFirstJob.action()
    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: first)
    XCTAssertEqual(changes.count, countAfterReplacement)

    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: second)
    let staleSecondJob = scheduler.jobs[1]
    coordinator.detach()
    XCTAssertTrue(staleSecondJob.cancelled)
    let countAfterDetach = changes.count
    staleSecondJob.action()
    NotificationCenter.default.post(name: NSWindow.didMoveNotification, object: second)
    XCTAssertEqual(changes.count, countAfterDetach)
  }
}
