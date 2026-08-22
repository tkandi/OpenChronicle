import Darwin
import Foundation
import XCTest

@testable import OpenChronicleApp

private enum FakeTransportError: Error {
  case connectFailed
  case flushFailed
  case sendFailed
}

private final class WeakReference {
  weak var value: AnyObject?

  init(_ value: AnyObject?) {
    self.value = value
  }
}

private final class FakePrivacyDiagnosticsTransport: PrivacyDiagnosticsTransport {
  var onMessage: ((ProtectionDiagnosticsWireMessage) -> Void)?
  var onDisconnect: ((Error?) -> Void)?
  private(set) var connectCount = 0
  private(set) var connectTimeouts: [TimeInterval] = []
  private(set) var closeCount = 0
  private(set) var flushCount = 0
  private(set) var flushTimeouts: [TimeInterval] = []
  private(set) var sent: [PrivacyDiagnosticsRequest] = []
  var connectDelay: TimeInterval = 0
  var failsConnect = false
  var failsFlush = false
  var failingActions: Set<PrivacyDiagnosticsAction> = []
  var flushDelay: TimeInterval = 0
  var onConnect: (() -> Void)?
  var onSend: ((PrivacyDiagnosticsRequest) -> Void)?

  func connect() throws {
    connectCount += 1
    onConnect?()
    if failsConnect {
      throw FakeTransportError.connectFailed
    }
  }

  func connect(timeout: TimeInterval) throws {
    connectTimeouts.append(timeout)
    if connectDelay > 0 {
      Thread.sleep(forTimeInterval: connectDelay)
    }
    try connect()
  }

  func send(_ request: PrivacyDiagnosticsRequest) throws {
    onSend?(request)
    if failingActions.contains(request.action) {
      throw FakeTransportError.sendFailed
    }
    sent.append(request)
  }

  func close() {
    closeCount += 1
  }

  func flushPendingWrites(timeout: TimeInterval) throws {
    flushCount += 1
    flushTimeouts.append(timeout)
    if flushDelay > 0 {
      Thread.sleep(forTimeInterval: flushDelay)
    }
    if failsFlush {
      throw FakeTransportError.flushFailed
    }
  }

  func deliverLease(
    id: String,
    displayID: Int,
    protectedGeneration: Int
  ) {
    onMessage?(
      .lease(
        leaseID: id,
        displayID: displayID,
        protectedGeneration: protectedGeneration,
        released: false
      )
    )
  }

  func deliverRelease(id: String) {
    onMessage?(
      .lease(
        leaseID: id,
        displayID: nil,
        protectedGeneration: nil,
        released: true
      )
    )
  }

  func deliverSnapshot(
    generation: Int,
    exact: Bool,
    displayID: Int = 2,
    diagnosticsProtected: Bool = true,
    exactValue: String = "Private window title"
  ) {
    let reasons = [
      ProtectionReasonDiagnostic(
        code: .diagnosticsReveal,
        displayID: displayID
      ),
      ProtectionReasonDiagnostic(
        code: .windowTitleRule,
        displayID: displayID,
        windowTitle: exact ? exactValue : nil,
        rule: exact ? "private-*" : nil
      ),
    ]
    let updatedAt = Date(timeIntervalSince1970: 1_777_000_000)
    onMessage?(
      .snapshot(
        ProtectionDiagnosticsSnapshot(
          generation: generation,
          state: .protected,
          indicatorConfirmed: true,
          diagnosticsGuardActive: diagnosticsProtected,
          createdAt: updatedAt,
          reasons: [],
          displays: [
            ProtectionDisplayDiagnostic(
              id: displayID,
              primary: true,
              state: .protected,
              screenshotBlocked: true,
              axBlocked: true,
              indicatorConfirmed: true,
              reasons: reasons,
              generation: generation,
              updatedAt: updatedAt
            )
          ]
        )
      )
    )
  }

  func disconnect(_ error: Error? = nil) {
    onDisconnect?(error)
  }
}

@MainActor
final class PrivacyDiagnosticsControllerTests: XCTestCase {
  func testExactValuesRemainHiddenUntilLeaseAndGenerationAreConfirmed() {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(transport: transport, detail: .exact)

    controller.setDisplay(2)
    controller.setPageVisible(true)
    XCTAssertEqual(transport.sent.last?.action, .acquireExact)
    XCTAssertFalse(controller.showsExactValues)

    transport.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    transport.deliverSnapshot(generation: 41, exact: true)
    XCTAssertFalse(controller.showsExactValues)
    XCTAssertNil(controller.displayDiagnostics.first?.reasons.last?.windowTitle)

    transport.deliverSnapshot(generation: 42, exact: true)
    XCTAssertTrue(controller.showsExactValues)
    XCTAssertEqual(
      controller.displayDiagnostics.first?.reasons.last?.windowTitle,
      "Private window title"
    )
  }

  func testCategorySubscribesWithoutRequestingLease() {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(transport: transport, detail: .category)

    controller.setDisplay(2)
    controller.setPageVisible(true)

    XCTAssertEqual(transport.sent.map(\.action), [.subscribe])
    XCTAssertEqual(transport.sent.first?.detail, .category)
    XCTAssertFalse(controller.showsExactValues)
  }

  func testTieredOnlyRequestsLeaseAfterReveal() {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(transport: transport, detail: .tiered)
    controller.setDisplay(2)
    controller.setPageVisible(true)
    XCTAssertEqual(transport.sent.map(\.action), [.subscribe])

    controller.revealExact()

    XCTAssertEqual(transport.sent.map(\.action), [.subscribe, .acquireExact])
  }

  func testDisconnectHidesSynchronouslyWithoutSendingRelease() {
    let (controller, transport) = makeExactController(confirmedOn: 2)
    let requestCount = transport.sent.count

    transport.disconnect()

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertNil(controller.displayDiagnostics.first?.reasons.last?.windowTitle)
    XCTAssertEqual(transport.sent.count, requestCount)
  }

  func testPageLeaveDiscardsExactCandidateBeforeRelease() {
    let marker = "page-leave-private-marker"
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      exactValue: marker
    )
    let candidate = WeakReference(controller.debugExactCandidate)
    XCTAssertNotNil(candidate.value)
    XCTAssertTrue(controller.debugRetainsExactValue(marker))
    var discardedBeforeRelease = false
    transport.onSend = { request in
      if request.action == .releaseExact {
        discardedBeforeRelease =
          controller.debugExactCandidate == nil
          && !controller.debugRetainsExactValue(marker)
          && controller.displayDiagnostics.allSatisfy {
            $0.reasons.allSatisfy { $0.windowTitle == nil && $0.rule == nil }
          }
      }
    }

    controller.setPageVisible(false)

    XCTAssertTrue(discardedBeforeRelease)
    XCTAssertNil(candidate.value)
    XCTAssertFalse(controller.debugRetainsExactValue(marker))
  }

  func testDisconnectDiscardsExactCandidateWithoutRelease() {
    let marker = "disconnect-private-marker"
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      exactValue: marker
    )
    let candidate = WeakReference(controller.debugExactCandidate)
    let requestCount = transport.sent.count

    transport.disconnect()

    XCTAssertNil(candidate.value)
    XCTAssertNil(controller.debugExactCandidate)
    XCTAssertFalse(controller.debugRetainsExactValue(marker))
    XCTAssertEqual(transport.sent.count, requestCount)
    XCTAssertTrue(
      controller.displayDiagnostics.allSatisfy {
        $0.reasons.allSatisfy { $0.windowTitle == nil && $0.rule == nil }
      }
    )
  }

  func testPageLeaveAfterDisconnectUsesCleanupTransportToReleaseKnownLease() {
    let marker = "disconnect-page-leave-private-marker"
    let scheduler = ReconnectSchedulerRecorder()
    let first = FakePrivacyDiagnosticsTransport()
    let cleanup = FakePrivacyDiagnosticsTransport()
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 },
      reconnectScheduler: scheduler.schedule
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    first.deliverSnapshot(generation: 42, exact: true, exactValue: marker)
    XCTAssertTrue(controller.debugRetainsExactValue(marker))

    first.disconnect()
    XCTAssertFalse(controller.debugRetainsExactValue(marker))
    var discardedBeforeRelease = false
    var releaseSentBeforeClose = false
    cleanup.onSend = { request in
      if request.action == .releaseExact {
        discardedBeforeRelease =
          controller.debugExactCandidate == nil
          && !controller.debugRetainsExactValue(marker)
          && !controller.showsExactValues
          && controller.displayDiagnostics.allSatisfy {
            $0.reasons.allSatisfy { $0.windowTitle == nil && $0.rule == nil }
          }
        releaseSentBeforeClose = cleanup.closeCount == 0
      }
    }

    controller.setPageVisible(false)

    XCTAssertTrue(discardedBeforeRelease)
    XCTAssertTrue(releaseSentBeforeClose)
    XCTAssertEqual(cleanup.connectCount, 1)
    XCTAssertEqual(cleanup.sent.map(\.action), [.releaseExact])
    XCTAssertEqual(cleanup.flushCount, 1)
    XCTAssertEqual(cleanup.closeCount, 1)
    XCTAssertEqual(scheduler.delays, [0.25])
  }

  func testPageLeaveBeforeDisconnectCallbackFallsBackAfterDeadTransportSendFails() {
    let marker = "dead-transport-private-marker"
    let first = FakePrivacyDiagnosticsTransport()
    first.failingActions = [.releaseExact]
    let cleanup = FakePrivacyDiagnosticsTransport()
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 }
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    first.deliverSnapshot(generation: 42, exact: true, exactValue: marker)
    XCTAssertTrue(controller.debugRetainsExactValue(marker))

    var exactDiscardedBeforeFirstSend = false
    first.onSend = { request in
      guard request.action == .releaseExact else { return }
      exactDiscardedBeforeFirstSend =
        controller.debugExactCandidate == nil
        && !controller.debugRetainsExactValue(marker)
        && !controller.showsExactValues
    }
    var exactDiscardedBeforeCleanupConnect = false
    cleanup.onConnect = {
      exactDiscardedBeforeCleanupConnect =
        controller.debugExactCandidate == nil
        && !controller.debugRetainsExactValue(marker)
        && !controller.showsExactValues
    }

    controller.setPageVisible(false)

    XCTAssertTrue(exactDiscardedBeforeFirstSend)
    XCTAssertTrue(exactDiscardedBeforeCleanupConnect)
    XCTAssertEqual(cleanup.connectCount, 1)
    XCTAssertEqual(cleanup.sent, [.releaseExact(pid: 123, leaseID: "lease-1")])
    XCTAssertEqual(cleanup.flushCount, 1)
    XCTAssertEqual(cleanup.closeCount, 1)
    XCTAssertEqual(first.closeCount, 1)
  }

  func testPageLeaveFallsBackWhenExistingTransportFlushFails() {
    let first = FakePrivacyDiagnosticsTransport()
    first.failsFlush = true
    let cleanup = FakePrivacyDiagnosticsTransport()
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 }
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)

    controller.setPageVisible(false)

    XCTAssertEqual(first.sent.last, .releaseExact(pid: 123, leaseID: "lease-1"))
    XCTAssertEqual(first.flushCount, 1)
    XCTAssertEqual(cleanup.connectCount, 1)
    XCTAssertEqual(cleanup.sent, [.releaseExact(pid: 123, leaseID: "lease-1")])
    XCTAssertEqual(cleanup.flushCount, 1)
    XCTAssertEqual(cleanup.closeCount, 1)
  }

  func testCleanupConnectAndFlushShareOneDeadline() throws {
    let first = FakePrivacyDiagnosticsTransport()
    first.failsFlush = true
    first.flushDelay = 0.04
    let cleanup = FakePrivacyDiagnosticsTransport()
    cleanup.connectDelay = 0.04
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 }
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)

    controller.setPageVisible(false)

    let firstFlushBudget = try XCTUnwrap(first.flushTimeouts.first)
    let cleanupConnectBudget = try XCTUnwrap(cleanup.connectTimeouts.first)
    let cleanupFlushBudget = try XCTUnwrap(cleanup.flushTimeouts.first)
    XCTAssertLessThanOrEqual(firstFlushBudget, 0.25)
    XCTAssertGreaterThan(firstFlushBudget, 0)
    XCTAssertLessThan(cleanupConnectBudget, firstFlushBudget - 0.02)
    XCTAssertLessThan(cleanupFlushBudget, cleanupConnectBudget - 0.02)
    XCTAssertGreaterThan(cleanupFlushBudget, 0)
  }

  func testPageLeaveHidesBeforeSendingRelease() {
    let (controller, transport) = makeExactController(confirmedOn: 2)
    var exactAtSend: Bool?
    transport.onSend = { request in
      if request.action == .releaseExact {
        exactAtSend = controller.showsExactValues
      }
    }

    controller.setPageVisible(false)

    XCTAssertEqual(exactAtSend, false)
    XCTAssertEqual(transport.sent.last?.action, .releaseExact)
    XCTAssertFalse(controller.showsExactValues)
  }

  func testOverlayModeDoesNotCreateTransportOrSubscribe() {
    var factoryCalls = 0
    let controller = PrivacyDiagnosticsController(
      transportFactory: {
        factoryCalls += 1
        return FakePrivacyDiagnosticsTransport()
      },
      displayModeProvider: { .overlay },
      detailProvider: { .exact },
      pidProvider: { 123 }
    )

    controller.setDisplay(2)
    controller.setPageVisible(true)
    controller.revealExact()

    XCTAssertEqual(factoryCalls, 0)
    XCTAssertFalse(controller.showsExactValues)
  }

  func testActivePolicyChangeToOverlayReleasesKnownLeaseExactlyOnce() {
    let transport = FakePrivacyDiagnosticsTransport()
    var displayMode = PrivacyReasonDisplayOption.hybrid
    var detail = PrivacyReasonDetailOption.exact
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transport },
      displayModeProvider: { displayMode },
      detailProvider: { detail },
      pidProvider: { 123 }
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    transport.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    transport.deliverSnapshot(generation: 42, exact: true)
    XCTAssertTrue(controller.showsExactValues)

    displayMode = .overlay
    detail = .category
    controller.activeConfigurationDidChange()

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertEqual(
      transport.sent.filter { $0.action == .releaseExact },
      [.releaseExact(pid: 123, leaseID: "lease-1")]
    )
    XCTAssertEqual(transport.flushCount, 1)
    XCTAssertEqual(transport.closeCount, 1)
  }

  func testStaleLeaseAcknowledgementForOldDisplayIsRejected() {
    let (controller, transport) = makeExactController(confirmedOn: 1)
    controller.setDisplay(2)

    transport.deliverLease(id: "lease-1", displayID: 1, protectedGeneration: 50)
    transport.deliverSnapshot(generation: 50, exact: true, displayID: 2)

    XCTAssertFalse(controller.showsExactValues)
  }

  func testUnavailableDisplayHidesAndDoesNotAcquireAnotherLease() {
    let (controller, transport) = makeExactController(confirmedOn: 2)
    let acquireCount = transport.sent.filter { $0.action == .acquireExact }.count

    controller.setDisplay(nil)

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertEqual(
      transport.sent.filter { $0.action == .acquireExact }.count,
      acquireCount
    )
    XCTAssertEqual(transport.sent.last?.action, .releaseExact)
  }

  func testWrongLeaseIDDuringMoveCannotRevealExactValues() {
    let (controller, transport) = makeExactController(confirmedOn: 1)
    controller.setDisplay(2)

    transport.deliverLease(id: "wrong-lease", displayID: 2, protectedGeneration: 50)
    transport.deliverSnapshot(generation: 50, exact: true, displayID: 2)

    XCTAssertFalse(controller.showsExactValues)
  }

  func testRevealWhileReleaseIsPendingReacquiresAfterReleaseAcknowledgement() {
    let (controller, transport) = makeExactController(confirmedOn: 2)
    controller.hideExact()
    XCTAssertEqual(transport.sent.last?.action, .releaseExact)

    controller.revealExact()
    transport.deliverRelease(id: "lease-1")

    XCTAssertEqual(transport.sent.last?.action, .acquireExact)
    XCTAssertFalse(controller.showsExactValues)
  }

  func testQueuedCallbackFromOldConnectionIsRejectedAfterReconnect() {
    let scheduler = ReconnectSchedulerRecorder()
    let first = FakePrivacyDiagnosticsTransport()
    let second = FakePrivacyDiagnosticsTransport()
    var transports = [first, second]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 },
      reconnectScheduler: scheduler.schedule
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    let queuedOldCallback = first.onMessage
    first.disconnect()
    scheduler.runNext()

    queuedOldCallback?(
      .lease(
        leaseID: "stale-lease",
        displayID: 2,
        protectedGeneration: 100,
        released: false
      )
    )
    second.deliverSnapshot(generation: 100, exact: true)

    XCTAssertFalse(controller.showsExactValues)
  }

  func testMalformedTransportMessageHidesAndSchedulesReconnect() {
    let scheduler = ReconnectSchedulerRecorder()
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      reconnectScheduler: scheduler.schedule
    )

    transport.disconnect(PrivacyDiagnosticsTransportError.invalidMessage)

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertEqual(scheduler.delays, [0.25])
  }

  func testReconnectUsesBoundedExponentialBackoff() {
    let scheduler = ReconnectSchedulerRecorder()
    let first = FakePrivacyDiagnosticsTransport()
    let second = FakePrivacyDiagnosticsTransport()
    let third = FakePrivacyDiagnosticsTransport()
    var transports = [first, second, third]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .diagnostics },
      detailProvider: { .category },
      pidProvider: { 123 },
      reconnectScheduler: scheduler.schedule
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)

    first.disconnect()
    XCTAssertEqual(scheduler.delays, [0.25])
    scheduler.runNext()
    second.disconnect()
    XCTAssertEqual(scheduler.delays, [0.25, 0.5])
    scheduler.runNext()
    third.disconnect()
    XCTAssertEqual(scheduler.delays, [0.25, 0.5, 1.0])
  }

  func testMoveHidesBeforeProtectingNewDisplay() {
    let (controller, transport) = makeExactController(confirmedOn: 1)
    XCTAssertTrue(controller.showsExactValues)
    var exactAtMove: Bool?
    transport.onSend = { request in
      if request.action == .moveExact {
        exactAtMove = controller.showsExactValues
      }
    }

    controller.setDisplay(2)

    XCTAssertEqual(exactAtMove, false)
    XCTAssertFalse(controller.showsExactValues)
    XCTAssertEqual(transport.sent.last?.action, .moveExact)
    XCTAssertEqual(transport.sent.last?.displayID, 2)
    transport.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 50)
    transport.deliverSnapshot(generation: 50, exact: true, displayID: 2)
    XCTAssertTrue(controller.showsExactValues)
  }

  func testMoveDiscardsExactCandidateBeforeMoveRequest() {
    let marker = "move-private-marker"
    let (controller, transport) = makeExactController(
      confirmedOn: 1,
      exactValue: marker
    )
    let candidate = WeakReference(controller.debugExactCandidate)
    var discardedBeforeMove = false
    transport.onSend = { request in
      if request.action == .moveExact {
        discardedBeforeMove =
          controller.debugExactCandidate == nil
          && !controller.debugRetainsExactValue(marker)
          && controller.displayDiagnostics.allSatisfy {
            $0.reasons.allSatisfy { $0.windowTitle == nil && $0.rule == nil }
          }
      }
    }

    controller.setDisplay(2)

    XCTAssertTrue(discardedBeforeMove)
    XCTAssertNil(candidate.value)
    XCTAssertFalse(controller.debugRetainsExactValue(marker))
  }

  func testSnapshotWithoutDiagnosticsSelfProtectionStaysRedacted() {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(transport: transport, detail: .exact)
    controller.setDisplay(2)
    controller.setPageVisible(true)
    transport.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)

    transport.deliverSnapshot(
      generation: 42,
      exact: true,
      diagnosticsProtected: false
    )

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertNil(controller.displayDiagnostics.first?.reasons.last?.windowTitle)
  }

  func testExactStringsAreSanitizedAgainBeforePublication() {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(transport: transport, detail: .exact)
    controller.setDisplay(2)
    controller.setPageVisible(true)
    transport.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    transport.deliverSnapshot(
      generation: 42,
      exact: true,
      exactValue: String(repeating: "x", count: 170) + "\nsecret"
    )

    let value = controller.displayDiagnostics.first?.reasons.last?.windowTitle
    XCTAssertEqual(value?.count, 160)
    XCTAssertFalse(value?.contains("\n") == true)
  }

  func testShutdownClearsPublishedModelsBeforeReleaseAndNeverReconnects() {
    let scheduler = ReconnectSchedulerRecorder()
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      reconnectScheduler: scheduler.schedule
    )
    var displaysAtRelease: [ProtectionDisplayDiagnostic]?
    transport.onSend = { request in
      if request.action == .releaseExact {
        displaysAtRelease = controller.displayDiagnostics
      }
    }
    transport.failingActions = [.releaseExact]

    controller.shutdown()

    XCTAssertEqual(displaysAtRelease, [])
    XCTAssertEqual(controller.displayDiagnostics, [])
    XCTAssertFalse(controller.showsExactValues)
    transport.disconnect()
    XCTAssertEqual(scheduler.delays, [])
  }

  func testShutdownDiscardsExactCandidateBeforeReleaseAndClose() {
    let marker = "shutdown-private-marker"
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      exactValue: marker
    )
    let candidate = WeakReference(controller.debugExactCandidate)
    var discardedBeforeRelease = false
    transport.onSend = { request in
      if request.action == .releaseExact {
        discardedBeforeRelease =
          controller.debugExactCandidate == nil
          && !controller.debugRetainsExactValue(marker)
          && controller.displayDiagnostics.isEmpty
          && controller.globalReasons.isEmpty
      }
    }

    controller.shutdown()

    XCTAssertTrue(discardedBeforeRelease)
    XCTAssertNil(candidate.value)
    XCTAssertNil(controller.debugExactCandidate)
    XCTAssertFalse(controller.debugRetainsExactValue(marker))
  }

  func testReauthorizationRequiresFreshSnapshotAtProtectedGeneration() {
    let oldMarker = "stale-private-marker"
    let freshMarker = "fresh-private-marker"
    let (controller, transport) = makeExactController(
      confirmedOn: 2,
      exactValue: oldMarker
    )
    controller.setPageVisible(false)
    controller.setPageVisible(true)
    XCTAssertEqual(transport.sent.last?.action, .releaseExact)
    transport.deliverRelease(id: "lease-1")
    XCTAssertEqual(transport.sent.last?.action, .acquireExact)
    transport.deliverLease(id: "lease-2", displayID: 2, protectedGeneration: 50)

    XCTAssertFalse(controller.showsExactValues)
    XCTAssertNil(controller.debugExactCandidate)
    transport.deliverSnapshot(
      generation: 49,
      exact: true,
      exactValue: oldMarker
    )
    XCTAssertFalse(controller.showsExactValues)
    XCTAssertNil(controller.debugExactCandidate)
    XCTAssertFalse(controller.debugRetainsExactValue(oldMarker))

    transport.deliverSnapshot(
      generation: 50,
      exact: true,
      exactValue: freshMarker
    )
    XCTAssertTrue(controller.showsExactValues)
    XCTAssertTrue(controller.debugRetainsExactValue(freshMarker))
    XCTAssertFalse(controller.debugRetainsExactValue(oldMarker))
  }

  func testShutdownAfterDisconnectUsesCleanupConnectionToReleaseKnownLease() {
    let scheduler = ReconnectSchedulerRecorder()
    let first = FakePrivacyDiagnosticsTransport()
    let cleanup = FakePrivacyDiagnosticsTransport()
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 },
      reconnectScheduler: scheduler.schedule
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    first.deliverSnapshot(generation: 42, exact: true)
    first.disconnect()
    XCTAssertFalse(first.sent.contains { $0.action == .releaseExact })
    cleanup.onSend = { _ in
      XCTAssertEqual(controller.displayDiagnostics, [])
    }

    controller.shutdown()

    XCTAssertEqual(cleanup.sent.map(\.action), [.releaseExact])
    XCTAssertEqual(cleanup.connectCount, 1)
    XCTAssertEqual(cleanup.closeCount, 1)
    XCTAssertEqual(scheduler.delays, [0.25])
  }

  func testShutdownRetainsLeaseStateWhenCleanupConnectionFails() {
    let scheduler = ReconnectSchedulerRecorder()
    let first = FakePrivacyDiagnosticsTransport()
    let cleanup = FakePrivacyDiagnosticsTransport()
    cleanup.failsConnect = true
    var transports = [first, cleanup]
    let controller = PrivacyDiagnosticsController(
      transportFactory: { transports.removeFirst() },
      displayModeProvider: { .hybrid },
      detailProvider: { .exact },
      pidProvider: { 123 },
      reconnectScheduler: scheduler.schedule
    )
    controller.setDisplay(2)
    controller.setPageVisible(true)
    first.deliverLease(id: "lease-1", displayID: 2, protectedGeneration: 42)
    first.disconnect()

    controller.shutdown()

    XCTAssertEqual(controller.debugLeaseID, "lease-1")
    XCTAssertEqual(cleanup.connectCount, 1)
    XCTAssertEqual(cleanup.sent, [])
    XCTAssertEqual(cleanup.closeCount, 1)
  }

  private func makeController(
    transport: FakePrivacyDiagnosticsTransport,
    detail: PrivacyReasonDetailOption,
    reconnectScheduler: @escaping PrivacyDiagnosticsReconnectScheduler =
      PrivacyDiagnosticsController.defaultReconnectScheduler
  ) -> PrivacyDiagnosticsController {
    PrivacyDiagnosticsController(
      transportFactory: { transport },
      displayModeProvider: { .hybrid },
      detailProvider: { detail },
      pidProvider: { 123 },
      reconnectScheduler: reconnectScheduler
    )
  }

  private func makeExactController(
    confirmedOn displayID: Int,
    exactValue: String = "Private window title",
    reconnectScheduler: @escaping PrivacyDiagnosticsReconnectScheduler =
      PrivacyDiagnosticsController.defaultReconnectScheduler
  ) -> (PrivacyDiagnosticsController, FakePrivacyDiagnosticsTransport) {
    let transport = FakePrivacyDiagnosticsTransport()
    let controller = makeController(
      transport: transport,
      detail: .exact,
      reconnectScheduler: reconnectScheduler
    )
    controller.setDisplay(displayID)
    controller.setPageVisible(true)
    transport.deliverLease(
      id: "lease-1",
      displayID: displayID,
      protectedGeneration: 42
    )
    transport.deliverSnapshot(
      generation: 42,
      exact: true,
      displayID: displayID,
      exactValue: exactValue
    )
    return (controller, transport)
  }
}

@MainActor
private final class ReconnectSchedulerRecorder {
  private(set) var delays: [TimeInterval] = []
  private var actions: [() -> Void] = []

  func schedule(delay: TimeInterval, action: @escaping @Sendable () -> Void) {
    delays.append(delay)
    actions.append(action)
  }

  func runNext() {
    actions.removeFirst()()
  }
}

final class ProtectionDiagnosticsWireTests: XCTestCase {
  func testDecodesCompleteSchemaV1SnapshotAndRFC3339Timestamp() throws {
    let data = Data(
      #"{"schema_version":1,"type":"snapshot","generation":42,"state":"protected","indicator_confirmed":true,"diagnostics_guard_active":true,"created_at":"2026-08-22T04:05:06.789000Z","reasons":[{"code":"manual_pause","display_id":null,"effective_resume_at":"2026-08-22T05:05:06+00:00"}],"displays":[{"id":2,"primary":true,"state":"protected","screenshot_blocked":true,"ax_blocked":true,"indicator_confirmed":true,"reasons":[{"code":"window_title_rule","display_id":2,"source_display_id":1,"app_name":"Safari","bundle_id":"com.apple.Safari","window_title":"Private","rule":"private-*"}]}]}"#.utf8
    )

    let message = try JSONDecoder().decode(ProtectionDiagnosticsWireMessage.self, from: data)
    guard case .snapshot(let snapshot) = message else {
      return XCTFail("Expected snapshot message")
    }
    XCTAssertEqual(snapshot.generation, 42)
    XCTAssertEqual(snapshot.createdAt.timeIntervalSince1970, 1_787_371_506.789, accuracy: 0.001)
    XCTAssertEqual(snapshot.displays.first?.generation, 42)
    XCTAssertEqual(snapshot.displays.first?.updatedAt, snapshot.createdAt)
    XCTAssertEqual(snapshot.displays.first?.reasons.first?.sourceDisplayID, 1)
    XCTAssertEqual(snapshot.displays.first?.reasons.first?.windowTitle, "Private")
    let effectiveResumeAt = try XCTUnwrap(snapshot.reasons.first?.effectiveResumeAt)
    XCTAssertEqual(effectiveResumeAt.timeIntervalSince1970, 1_787_375_106, accuracy: 0.001)
    let roundTrip = try JSONDecoder().decode(
      ProtectionDiagnosticsWireMessage.self,
      from: JSONEncoder().encode(message)
    )
    XCTAssertEqual(roundTrip, message)
  }

  func testDecodesLeaseAndErrorMessageVariants() throws {
    let lease = try JSONDecoder().decode(
      ProtectionDiagnosticsWireMessage.self,
      from: Data(
        #"{"schema_version":1,"type":"lease","lease_id":"lease-1","display_id":2,"protected_generation":42}"#.utf8
      )
    )
    let release = try JSONDecoder().decode(
      ProtectionDiagnosticsWireMessage.self,
      from: Data(
        #"{"schema_version":1,"type":"lease","lease_id":"lease-1","released":true}"#.utf8
      )
    )
    let error = try JSONDecoder().decode(
      ProtectionDiagnosticsWireMessage.self,
      from: Data(
        #"{"schema_version":1,"type":"error","code":"protection_timeout"}"#.utf8
      )
    )

    XCTAssertEqual(
      lease,
      .lease(
        leaseID: "lease-1",
        displayID: 2,
        protectedGeneration: 42,
        released: false
      )
    )
    XCTAssertEqual(
      release,
      .lease(
        leaseID: "lease-1",
        displayID: nil,
        protectedGeneration: nil,
        released: true
      )
    )
    XCTAssertEqual(error, .error(code: "protection_timeout"))
  }

  func testUnknownReasonCodeBecomesFixedCategoryAndDropsExactFields() throws {
    let data = Data(
      #"{"schema_version":1,"type":"snapshot","generation":1,"state":"protected","indicator_confirmed":true,"diagnostics_guard_active":false,"created_at":"2026-08-22T04:05:06Z","reasons":[],"displays":[{"id":2,"primary":true,"state":"protected","screenshot_blocked":true,"ax_blocked":false,"indicator_confirmed":true,"reasons":[{"code":"future_private_reason","display_id":2,"window_title":"must not survive","rule":"must not survive"}]}]}"#.utf8
    )

    let message = try JSONDecoder().decode(ProtectionDiagnosticsWireMessage.self, from: data)
    guard case .snapshot(let snapshot) = message,
      let reason = snapshot.displays.first?.reasons.first
    else {
      return XCTFail("Expected decoded reason")
    }
    XCTAssertEqual(reason.code, .unknown)
    XCTAssertNil(reason.windowTitle)
    XCTAssertNil(reason.rule)
  }

  func testRejectsMalformedOrUnsupportedWireMessages() {
    let invalidMessages = [
      #"{"schema_version":2,"type":"error","code":"unavailable"}"#,
      #"{"schema_version":1,"type":"future"}"#,
      #"{"schema_version":1,"type":"snapshot","generation":1}"#,
    ]

    for message in invalidMessages {
      XCTAssertThrowsError(
        try JSONDecoder().decode(
          ProtectionDiagnosticsWireMessage.self,
          from: Data(message.utf8)
        )
      )
    }
  }

  func testRequestEncodingMatchesTask5ActionsAndFields() throws {
    let requests = [
      PrivacyDiagnosticsRequest.subscribe(detail: .category),
      PrivacyDiagnosticsRequest.acquireExact(pid: 123, displayID: 2),
      PrivacyDiagnosticsRequest.moveExact(pid: 123, leaseID: "lease-1", displayID: 3),
      PrivacyDiagnosticsRequest.releaseExact(pid: 123, leaseID: "lease-1"),
    ]
    let expected: [[String: AnyHashable]] = [
      ["schema_version": 1, "action": "subscribe", "detail": "category"],
      ["schema_version": 1, "action": "acquire_exact", "pid": 123, "display_id": 2],
      [
        "schema_version": 1, "action": "move_exact", "pid": 123,
        "lease_id": "lease-1", "display_id": 3,
      ],
      [
        "schema_version": 1, "action": "release_exact", "pid": 123,
        "lease_id": "lease-1",
      ],
    ]

    for (request, expectedObject) in zip(requests, expected) {
      let data = try JSONEncoder().encode(request)
      let object = try XCTUnwrap(
        JSONSerialization.jsonObject(with: data) as? [String: AnyHashable]
      )
      XCTAssertEqual(object, expectedObject)
    }
  }

  func testRuntimePathsIncludePrivateDiagnosticsSocket() {
    let paths = RuntimePaths(root: URL(fileURLWithPath: "/tmp/openchronicle-test"))
    XCTAssertEqual(paths.runtimeDirectory.path, "/tmp/openchronicle-test/runtime")
    XCTAssertEqual(
      paths.privacyDiagnosticsSocket.path,
      "/tmp/openchronicle-test/runtime/privacy-diagnostics.sock"
    )
  }
}

final class UnixPrivacyDiagnosticsTransportTests: XCTestCase {
  func testTransportSendsAndReceivesBoundedNDJSONOnTheMainThread() throws {
    let fixture = try UnixSocketFixture()
    defer { fixture.close() }
    let requestReceived = expectation(description: "server received request")
    let messageReceived = expectation(description: "client received message")
    var requestObject: [String: AnyHashable]?
    fixture.acceptOne { data, client in
      requestObject = try? JSONSerialization.jsonObject(with: data) as? [String: AnyHashable]
      requestReceived.fulfill()
      let response =
        #"{"schema_version":1,"type":"error","code":"unavailable"}"# + "\n"
      response.withCString { pointer in
        _ = Darwin.write(client, pointer, strlen(pointer))
      }
    }

    let transport = UnixPrivacyDiagnosticsTransport(socketURL: fixture.socketURL)
    transport.onMessage = { message in
      XCTAssertTrue(Thread.isMainThread)
      guard case .error(let code) = message else {
        return XCTFail("Expected error message")
      }
      XCTAssertEqual(code, "unavailable")
      messageReceived.fulfill()
    }
    try transport.connect()
    try transport.send(.subscribe(detail: .category))

    wait(for: [requestReceived, messageReceived], timeout: 2.0)
    XCTAssertEqual(
      requestObject,
      ["schema_version": 1, "action": "subscribe", "detail": "category"]
    )
    transport.close()
    transport.close()
  }

  func testFlushPendingWritesDeliversReleaseBeforeImmediateClose() throws {
    let fixture = try UnixSocketFixture()
    defer { fixture.close() }
    let requestReceived = expectation(description: "server received release")
    var requestObject: [String: AnyHashable]?
    fixture.acceptOne { data, _client in
      requestObject = try? JSONSerialization.jsonObject(with: data) as? [String: AnyHashable]
      requestReceived.fulfill()
    }

    let transport = UnixPrivacyDiagnosticsTransport(socketURL: fixture.socketURL)
    try transport.connect()
    try transport.send(.releaseExact(pid: 123, leaseID: "lease-1"))
    try transport.flushPendingWrites(timeout: 1.0)
    transport.close()

    wait(for: [requestReceived], timeout: 2.0)
    XCTAssertEqual(
      requestObject,
      [
        "schema_version": 1,
        "action": "release_exact",
        "pid": 123,
        "lease_id": "lease-1",
      ]
    )
  }

  func testTimedConnectSucceedsWithNonblockingUnixSocket() throws {
    let fixture = try UnixSocketFixture()
    defer { fixture.close() }
    fixture.acceptOne { _, _ in }
    let transport = UnixPrivacyDiagnosticsTransport(socketURL: fixture.socketURL)
    defer { transport.close() }

    try transport.connect(timeout: 0.25)

    let flags = try XCTUnwrap(transport.debugDescriptorFlags)
    XCTAssertNotEqual(flags & O_NONBLOCK, 0)
    try transport.send(.subscribe(detail: .category))
    try transport.flushPendingWrites(timeout: 0.25)
  }

  func testConnectionPollTimesOutWithinItsBudget() throws {
    var descriptors = [Int32](repeating: -1, count: 2)
    XCTAssertEqual(Darwin.socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
    defer {
      Darwin.close(descriptors[0])
      Darwin.close(descriptors[1])
    }
    let flags = Darwin.fcntl(descriptors[0], F_GETFL)
    XCTAssertGreaterThanOrEqual(flags, 0)
    XCTAssertEqual(Darwin.fcntl(descriptors[0], F_SETFL, flags | O_NONBLOCK), 0)
    var buffer = [UInt8](repeating: 0, count: 4096)
    while Darwin.write(descriptors[0], &buffer, buffer.count) > 0 {}
    XCTAssertTrue(errno == EAGAIN || errno == EWOULDBLOCK)

    let started = ProcessInfo.processInfo.systemUptime
    XCTAssertThrowsError(
      try UnixPrivacyDiagnosticsTransport.waitForConnection(
        descriptor: descriptors[0],
        timeout: 0.02
      )
    ) { error in
      XCTAssertEqual(error as? PrivacyDiagnosticsTransportError, .connectionFailed)
    }
    let elapsed = ProcessInfo.processInfo.systemUptime - started
    XCTAssertGreaterThanOrEqual(elapsed, 0.01)
    XCTAssertLessThan(elapsed, 0.25)
  }
}

private final class UnixSocketFixture {
  let socketURL: URL
  private let listener: Int32
  private let queue = DispatchQueue(label: "privacy-diagnostics-test-server")

  init() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(
      UUID().uuidString,
      isDirectory: true
    )
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    socketURL = directory.appendingPathComponent("diagnostics.sock")
    listener = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
    guard listener >= 0 else { throw POSIXError(.EIO) }

    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    let path = Array(socketURL.path.utf8CString)
    guard path.count <= MemoryLayout.size(ofValue: address.sun_path) else {
      Darwin.close(listener)
      throw POSIXError(.ENAMETOOLONG)
    }
    withUnsafeMutablePointer(to: &address.sun_path.0) { destination in
      path.withUnsafeBufferPointer { source in
        _ = memcpy(destination, source.baseAddress, path.count)
      }
    }
    let bound = withUnsafePointer(to: &address) { pointer in
      pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
        Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
      }
    }
    guard bound == 0, Darwin.listen(listener, 1) == 0 else {
      Darwin.close(listener)
      throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
    }
  }

  func acceptOne(handler: @escaping (Data, Int32) -> Void) {
    queue.async { [listener] in
      let client = Darwin.accept(listener, nil, nil)
      guard client >= 0 else { return }
      defer { Darwin.close(client) }
      var data = Data()
      var byte: UInt8 = 0
      while Darwin.read(client, &byte, 1) == 1 {
        if byte == 0x0A { break }
        data.append(byte)
      }
      handler(data, client)
    }
  }

  func close() {
    Darwin.shutdown(listener, SHUT_RDWR)
    Darwin.close(listener)
    try? FileManager.default.removeItem(at: socketURL.deletingLastPathComponent())
  }
}
