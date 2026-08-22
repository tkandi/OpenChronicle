import Combine
import Darwin
import Foundation

typealias PrivacyDiagnosticsReconnectScheduler = (
  _ delay: TimeInterval,
  _ action: @escaping @Sendable () -> Void
) -> Void

@MainActor
final class PrivacyDiagnosticsController: ObservableObject {
  private static let cleanupFlushTimeout: TimeInterval = 0.25

  nonisolated(unsafe) static let defaultReconnectScheduler: PrivacyDiagnosticsReconnectScheduler = {
    delay,
    action in
    DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: action)
  }

  @Published private(set) var displayDiagnostics: [ProtectionDisplayDiagnostic] = []
  @Published private(set) var globalReasons: [ProtectionReasonDiagnostic] = []
  @Published private(set) var showsExactValues = false
  @Published private(set) var lastErrorCode: String?

  private enum LeaseOperation: Equatable {
    case acquire(displayID: Int)
    case move(leaseID: String, displayID: Int)
    case release(leaseID: String, reacquire: Bool)
  }

  private final class ExactSnapshotCandidate {
    let snapshot: ProtectionDiagnosticsSnapshot

    init(snapshot: ProtectionDiagnosticsSnapshot) {
      self.snapshot = snapshot
    }
  }

  private let transportFactory: () -> PrivacyDiagnosticsTransport
  private let displayModeProvider: () -> PrivacyReasonDisplayOption
  private let detailProvider: () -> PrivacyReasonDetailOption
  private let pidProvider: () -> Int32
  private let reconnectScheduler: PrivacyDiagnosticsReconnectScheduler

  private var transport: PrivacyDiagnosticsTransport?
  private var connectionGeneration = 0
  private var reconnectToken = 0
  private var reconnectAttempt = 0
  private var reconnectScheduled = false

  private var pageVisible = false
  private var currentDisplayID: Int?
  private var exactRequested = false
  private var stopped = false

  private var latestExactCandidate: ExactSnapshotCandidate?
  private var latestCategorySnapshot: ProtectionDiagnosticsSnapshot?
  private var leaseID: String?
  private var leaseDisplayID: Int?
  private var leaseConnectionGeneration: Int?
  private var protectedGeneration: Int?
  private var pendingLeaseOperation: LeaseOperation?

  init(
    transportFactory: @escaping () -> PrivacyDiagnosticsTransport,
    displayModeProvider: @escaping () -> PrivacyReasonDisplayOption,
    detailProvider: @escaping () -> PrivacyReasonDetailOption,
    pidProvider: @escaping () -> Int32 = { Darwin.getpid() },
    reconnectScheduler: @escaping PrivacyDiagnosticsReconnectScheduler =
      PrivacyDiagnosticsController.defaultReconnectScheduler
  ) {
    self.transportFactory = transportFactory
    self.displayModeProvider = displayModeProvider
    self.detailProvider = detailProvider
    self.pidProvider = pidProvider
    self.reconnectScheduler = reconnectScheduler
  }

  convenience init(
    paths: RuntimePaths = .live(),
    activeConfigurationProvider: @escaping () -> ConfigurationSnapshot?,
    pidProvider: @escaping () -> Int32 = { Darwin.getpid() },
    reconnectScheduler: @escaping PrivacyDiagnosticsReconnectScheduler =
      PrivacyDiagnosticsController.defaultReconnectScheduler
  ) {
    self.init(
      transportFactory: {
        UnixPrivacyDiagnosticsTransport(socketURL: paths.privacyDiagnosticsSocket)
      },
      displayModeProvider: {
        let rawValue = activeConfigurationProvider()?.values?.capture.privacyReasonDisplay
        return PrivacyReasonDisplayOption(rawValue: rawValue ?? "") ?? .defaultValue
      },
      detailProvider: {
        let rawValue = activeConfigurationProvider()?.values?.capture.privacyReasonDetail
        return PrivacyReasonDetailOption(rawValue: rawValue ?? "") ?? .defaultValue
      },
      pidProvider: pidProvider,
      reconnectScheduler: reconnectScheduler
    )
  }

  func setPageVisible(_ visible: Bool) {
    guard !stopped else { return }
    pageVisible = visible
    if visible {
      exactRequested = detailProvider() == .exact
      applyActivePolicy()
    } else {
      exactRequested = false
      cancelReconnect()
      hideExactSynchronously(clearPublishedModels: false)
      releaseAndCloseConnection()
    }
  }

  func setDisplay(_ displayID: Int?) {
    guard !stopped else { return }
    let validDisplayID = displayID.flatMap(Self.validDisplayID)
    guard validDisplayID != currentDisplayID else { return }

    currentDisplayID = validDisplayID
    hideExactSynchronously(clearPublishedModels: false)
    guard shouldSubscribe else { return }
    guard let validDisplayID else {
      releaseLeaseIfPossible(reacquire: false)
      return
    }
    guard shouldRequestExact else { return }

    if pendingLeaseOperation != nil {
      return
    }
    if let leaseID {
      moveLease(leaseID: leaseID, to: validDisplayID)
    } else {
      acquireLease(for: validDisplayID)
    }
  }

  func revealExact() {
    guard !stopped, pageVisible, detailProvider() != .category else { return }
    exactRequested = true
    applyActivePolicy()
  }

  func hideExact() {
    guard !stopped else { return }
    exactRequested = false
    hideExactSynchronously(clearPublishedModels: false)
    releaseLeaseIfPossible(reacquire: false)
  }

  func shutdown() {
    guard !stopped else { return }
    stopped = true
    pageVisible = false
    exactRequested = false
    cancelReconnect()
    hideExactSynchronously(clearPublishedModels: true)
    bestEffortRelease(connectForCleanup: true)
    closeConnection()
    clearLeaseState()
    latestCategorySnapshot = nil
  }

  var debugExactCandidate: AnyObject? {
    latestExactCandidate
  }

  func debugRetainsExactValue(_ marker: String) -> Bool {
    let internalSnapshots = [latestExactCandidate?.snapshot, latestCategorySnapshot]
      .compactMap { $0 }
    let internalReasons = internalSnapshots.flatMap { snapshot in
      snapshot.reasons + snapshot.displays.flatMap(\.reasons)
    }
    let reasons = internalReasons + globalReasons + displayDiagnostics.flatMap(\.reasons)
    return reasons.contains { reason in
      [reason.appName, reason.bundleID, reason.windowTitle, reason.rule]
        .compactMap { $0 }
        .contains(marker)
    }
  }

  private var shouldSubscribe: Bool {
    guard pageVisible, !stopped else { return false }
    switch displayModeProvider() {
    case .overlay:
      return false
    case .diagnostics, .hybrid:
      return true
    }
  }

  private var shouldRequestExact: Bool {
    shouldSubscribe
      && exactRequested
      && detailProvider() != .category
      && currentDisplayID != nil
  }

  private func applyActivePolicy() {
    guard shouldSubscribe else {
      cancelReconnect()
      hideExactSynchronously(clearPublishedModels: false)
      releaseAndCloseConnection()
      return
    }
    connectIfNeeded()
    requestExactIfNeeded()
  }

  private func connectIfNeeded() {
    guard shouldSubscribe, transport == nil else { return }
    reconnectScheduled = false
    connectionGeneration += 1
    let generation = connectionGeneration
    let newTransport = transportFactory()
    transport = newTransport
    newTransport.onMessage = { [weak self] message in
      MainActor.assumeIsolated {
        self?.handle(message, connectionGeneration: generation)
      }
    }
    newTransport.onDisconnect = { [weak self] error in
      MainActor.assumeIsolated {
        self?.handleDisconnect(error, connectionGeneration: generation)
      }
    }

    do {
      try newTransport.connect()
      try newTransport.send(.subscribe(detail: .category))
    } catch {
      failConnection(generation: generation)
    }
  }

  private func requestExactIfNeeded() {
    guard shouldRequestExact, let displayID = currentDisplayID, transport != nil else {
      return
    }
    guard pendingLeaseOperation == nil else { return }

    if let leaseID {
      if leaseConnectionGeneration != connectionGeneration {
        releaseLeaseIfPossible(reacquire: true)
      } else if leaseDisplayID != displayID {
        moveLease(leaseID: leaseID, to: displayID)
      }
      return
    }
    acquireLease(for: displayID)
  }

  private func acquireLease(for displayID: Int) {
    guard pendingLeaseOperation == nil, let transport else { return }
    pendingLeaseOperation = .acquire(displayID: displayID)
    do {
      try transport.send(.acquireExact(pid: pidProvider(), displayID: displayID))
    } catch {
      failConnection(generation: connectionGeneration)
    }
  }

  private func moveLease(leaseID: String, to displayID: Int) {
    guard pendingLeaseOperation == nil, let transport else { return }
    hideExactSynchronously(clearPublishedModels: false)
    pendingLeaseOperation = .move(leaseID: leaseID, displayID: displayID)
    do {
      try transport.send(
        .moveExact(pid: pidProvider(), leaseID: leaseID, displayID: displayID)
      )
    } catch {
      failConnection(generation: connectionGeneration)
    }
  }

  private func releaseLeaseIfPossible(reacquire: Bool) {
    guard pendingLeaseOperation == nil, let leaseID, let transport else { return }
    hideExactSynchronously(clearPublishedModels: false)
    pendingLeaseOperation = .release(leaseID: leaseID, reacquire: reacquire)
    do {
      try transport.send(.releaseExact(pid: pidProvider(), leaseID: leaseID))
    } catch {
      failConnection(generation: connectionGeneration)
    }
  }

  private func handle(
    _ message: ProtectionDiagnosticsWireMessage,
    connectionGeneration: Int
  ) {
    guard connectionGeneration == self.connectionGeneration, transport != nil, !stopped else {
      return
    }
    reconnectAttempt = 0
    lastErrorCode = nil
    switch message {
    case .snapshot(let snapshot):
      latestCategorySnapshot = snapshot.categoryOnly()
      latestExactCandidate = canAuthorizeExactSnapshot(snapshot)
        ? ExactSnapshotCandidate(snapshot: snapshot)
        : nil
      publishLatestSnapshot()
    case .lease(let leaseID, let displayID, let protectedGeneration, let released):
      handleLease(
        leaseID: leaseID,
        displayID: displayID,
        protectedGeneration: protectedGeneration,
        released: released
      )
    case .error(let code):
      handleServerError(code)
    }
  }

  private func handleLease(
    leaseID acknowledgedLeaseID: String,
    displayID: Int?,
    protectedGeneration acknowledgedGeneration: Int?,
    released: Bool
  ) {
    switch pendingLeaseOperation {
    case .release(let expectedLeaseID, let reacquire):
      guard released, acknowledgedLeaseID == expectedLeaseID else { return }
      pendingLeaseOperation = nil
      if leaseID == acknowledgedLeaseID {
        clearLeaseState()
      }
      if reacquire || shouldRequestExact {
        requestExactIfNeeded()
      }

    case .acquire(let expectedDisplayID):
      guard !released,
        displayID == expectedDisplayID,
        let acknowledgedGeneration,
        acknowledgedGeneration > 0
      else { return }
      pendingLeaseOperation = nil
      installLease(
        id: acknowledgedLeaseID,
        displayID: expectedDisplayID,
        protectedGeneration: acknowledgedGeneration
      )
      finishLeaseTransition(acknowledgedDisplayID: expectedDisplayID)

    case .move(let expectedLeaseID, let expectedDisplayID):
      guard !released,
        acknowledgedLeaseID == expectedLeaseID,
        displayID == expectedDisplayID,
        let acknowledgedGeneration,
        acknowledgedGeneration > 0
      else { return }
      pendingLeaseOperation = nil
      installLease(
        id: acknowledgedLeaseID,
        displayID: expectedDisplayID,
        protectedGeneration: acknowledgedGeneration
      )
      finishLeaseTransition(acknowledgedDisplayID: expectedDisplayID)

    case nil:
      return
    }
  }

  private func finishLeaseTransition(acknowledgedDisplayID: Int) {
    guard shouldRequestExact, let currentDisplayID else {
      releaseLeaseIfPossible(reacquire: false)
      return
    }
    guard currentDisplayID == acknowledgedDisplayID else {
      if let leaseID {
        moveLease(leaseID: leaseID, to: currentDisplayID)
      }
      return
    }
    publishLatestSnapshot()
  }

  private func installLease(id: String, displayID: Int, protectedGeneration: Int) {
    leaseID = id
    leaseDisplayID = displayID
    leaseConnectionGeneration = connectionGeneration
    self.protectedGeneration = protectedGeneration
  }

  private func handleServerError(_ code: String) {
    lastErrorCode = code
    hideExactSynchronously(clearPublishedModels: false)
    let operation = pendingLeaseOperation
    pendingLeaseOperation = nil

    if code == "invalid_lease" {
      clearLeaseState()
      requestExactIfNeeded()
    } else if case .release(_, let reacquire) = operation, reacquire {
      requestExactIfNeeded()
    }
  }

  private func handleDisconnect(_ error: Error?, connectionGeneration: Int) {
    guard connectionGeneration == self.connectionGeneration, transport != nil else { return }
    lastErrorCode = (error as? PrivacyDiagnosticsTransportError)?.rawValue
    hideExactSynchronously(clearPublishedModels: false)
    pendingLeaseOperation = nil
    protectedGeneration = nil
    leaseConnectionGeneration = nil
    closeConnection()
    scheduleReconnect()
  }

  private func failConnection(generation: Int) {
    guard generation == connectionGeneration else { return }
    hideExactSynchronously(clearPublishedModels: false)
    pendingLeaseOperation = nil
    protectedGeneration = nil
    leaseConnectionGeneration = nil
    closeConnection()
    scheduleReconnect()
  }

  private func scheduleReconnect() {
    guard shouldSubscribe, !reconnectScheduled else { return }
    let delay = min(8.0, 0.25 * pow(2.0, Double(reconnectAttempt)))
    reconnectAttempt = min(reconnectAttempt + 1, 6)
    reconnectScheduled = true
    reconnectToken += 1
    let token = reconnectToken
    reconnectScheduler(delay) { [weak self] in
      guard let self else { return }
      MainActor.assumeIsolated {
        guard self.reconnectToken == token, self.reconnectScheduled else { return }
        self.reconnectScheduled = false
        self.connectIfNeeded()
        self.requestExactIfNeeded()
      }
    }
  }

  private func cancelReconnect() {
    reconnectToken += 1
    reconnectScheduled = false
  }

  private func publishLatestSnapshot() {
    guard let categorySnapshot = latestCategorySnapshot else {
      showsExactValues = false
      return
    }

    guard let candidate = latestExactCandidate,
      canAuthorizeExactSnapshot(candidate.snapshot)
    else {
      publish(categorySnapshot, exact: false)
      return
    }
    publish(candidate.snapshot.sanitizedForPublication(), exact: true)
  }

  private func canAuthorizeExactSnapshot(_ snapshot: ProtectionDiagnosticsSnapshot) -> Bool {
    guard shouldRequestExact,
      let leaseID,
      !leaseID.isEmpty,
      let displayID = currentDisplayID,
      leaseDisplayID == displayID,
      leaseConnectionGeneration == connectionGeneration,
      let protectedGeneration,
      snapshot.generation >= protectedGeneration,
      snapshot.diagnosticsGuardActive,
      let display = snapshot.displays.first(where: { $0.id == displayID }),
      display.screenshotBlocked,
      display.reasons.contains(where: { $0.code == .diagnosticsReveal })
    else {
      return false
    }
    return true
  }

  private func publish(_ snapshot: ProtectionDiagnosticsSnapshot, exact: Bool) {
    displayDiagnostics = snapshot.displays
    globalReasons = snapshot.reasons
    showsExactValues = exact
  }

  private func hideExactSynchronously(clearPublishedModels: Bool) {
    latestExactCandidate = nil
    protectedGeneration = nil
    showsExactValues = false
    if clearPublishedModels {
      displayDiagnostics = []
      globalReasons = []
    } else if let latestCategorySnapshot {
      displayDiagnostics = latestCategorySnapshot.displays
      globalReasons = latestCategorySnapshot.reasons
    } else {
      displayDiagnostics = []
      globalReasons = []
    }
  }

  private func releaseAndCloseConnection() {
    bestEffortRelease(connectForCleanup: true)
    closeConnection()
  }

  private func bestEffortRelease(connectForCleanup: Bool) {
    guard let leaseID else { return }
    if let transport {
      try? transport.send(.releaseExact(pid: pidProvider(), leaseID: leaseID))
      try? transport.flushPendingWrites(timeout: Self.cleanupFlushTimeout)
      return
    }
    guard connectForCleanup else { return }
    let cleanupTransport = transportFactory()
    defer { cleanupTransport.close() }
    do {
      try cleanupTransport.connect()
      try cleanupTransport.send(.releaseExact(pid: pidProvider(), leaseID: leaseID))
      try cleanupTransport.flushPendingWrites(timeout: Self.cleanupFlushTimeout)
    } catch {
      return
    }
  }

  private func closeConnection() {
    connectionGeneration += 1
    let oldTransport = transport
    transport = nil
    oldTransport?.onMessage = nil
    oldTransport?.onDisconnect = nil
    oldTransport?.close()
    pendingLeaseOperation = nil
    protectedGeneration = nil
    leaseConnectionGeneration = nil
  }

  private func clearLeaseState() {
    leaseID = nil
    leaseDisplayID = nil
    leaseConnectionGeneration = nil
    protectedGeneration = nil
    pendingLeaseOperation = nil
  }

  private static func validDisplayID(_ displayID: Int) -> Int? {
    guard displayID > 0, UInt64(displayID) <= UInt64(UInt32.max) else { return nil }
    return displayID
  }
}
