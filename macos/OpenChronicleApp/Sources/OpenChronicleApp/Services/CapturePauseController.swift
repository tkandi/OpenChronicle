import Combine
import Foundation
import UserNotifications

protocol CapturePauseNotificationCenter: AnyObject {
  func notificationSettings() async -> UNNotificationSettings
  func requestAuthorization(options: UNAuthorizationOptions) async throws -> Bool
  func add(_ request: UNNotificationRequest) async throws
  func setNotificationCategories(_ categories: Set<UNNotificationCategory>)
  func removeDeliveredNotifications(withIdentifiers identifiers: [String])
}

extension UNUserNotificationCenter: CapturePauseNotificationCenter {}

@MainActor
final class CapturePauseController: ObservableObject {
  static let notificationTypeKey = "openchronicle_notification_type"
  static let notificationType = "capture_pause"
  static let pauseIDKey = "openchronicle_capture_pause_id"
  static let categoryIdentifier = "CAPTURE_PAUSE_ACTIONS"
  static let resumeActionIdentifier = "CAPTURE_RESUME_NOW"
  static let extendActionIdentifier = "CAPTURE_EXTEND_30_MINUTES"
  static let keepPausedActionIdentifier = "CAPTURE_KEEP_PAUSED"

  @Published private(set) var state: CapturePauseState?
  @Published private(set) var displayNow = Date()
  @Published private(set) var lastError: String?

  private let store: CapturePauseStateStore
  private let backend: BackendController
  private let notificationCenter: any CapturePauseNotificationCenter
  private var isPostingNotification = false
  private var lastNotificationAttemptAt: Date?

  init(
    paths: RuntimePaths = .live(),
    backend: BackendController,
    notificationCenter: any CapturePauseNotificationCenter = UNUserNotificationCenter.current()
  ) {
    store = CapturePauseStateStore(fileURL: paths.pausedFlag)
    self.backend = backend
    self.notificationCenter = notificationCenter
    state = store.load()
  }

  var isPaused: Bool { state != nil }

  var statusText: String {
    guard let state else { return "Capturing" }
    switch state.mode {
    case .indefinite:
      return "Capture paused indefinitely"
    case .timed:
      if state.resumeArmedAt == nil,
        let resumeAt = state.resumeAt,
        displayNow >= resumeAt.addingTimeInterval(-CapturePauseState.warningLeadTime)
      {
        return "Paused · waiting to warn before resume"
      }
      guard let deadline = state.effectiveResumeAt ?? state.resumeAt else {
        return "Capture paused safely"
      }
      return "Paused · resumes in \(Self.durationText(deadline.timeIntervalSince(displayNow)))"
    }
  }

  func start() {
    registerNotificationActions()
    refreshState()
    if state != nil {
      ensureNotificationAuthorization()
    }
    tick()
  }

  func pause(for duration: TimeInterval?) {
    let now = Date()
    let newState = duration.map { CapturePauseState.timed(duration: $0, now: now) }
      ?? CapturePauseState.indefinite(now: now)
    persist(newState)
    ensureNotificationAuthorization()
  }

  func resume() {
    _ = resume(expectedPauseID: nil)
  }

  func extend(by duration: TimeInterval) {
    _ = extend(by: duration, expectedPauseID: nil)
  }

  @discardableResult
  private func extend(by duration: TimeInterval, expectedPauseID: String?) -> Bool {
    let now = Date()
    var updatedPauseID: String?
    let applied = mutatePause(
      at: now,
      expectedPauseID: expectedPauseID
    ) { existing in
      var current = existing ?? CapturePauseState.timed(duration: duration, now: now)
      if existing != nil {
        current.mode = .timed
        current.resumeAt = max(now, current.resumeAt ?? now).addingTimeInterval(duration)
        current.resumeArmedAt = nil
        current.appHeartbeatAt = now
        current.lastReminderAt = nil
      }
      updatedPauseID = current.id
      return current
    }
    if applied {
      if let updatedPauseID {
        notificationCenter.removeDeliveredNotifications(
          withIdentifiers: [warningIdentifier(for: updatedPauseID)]
        )
      }
      ensureNotificationAuthorization()
    }
    return applied
  }

  func keepPaused() {
    _ = keepPaused(expectedPauseID: nil)
  }

  @discardableResult
  private func keepPaused(expectedPauseID: String?) -> Bool {
    let now = Date()
    var updatedPauseID: String?
    let applied = mutatePause(
      at: now,
      expectedPauseID: expectedPauseID
    ) { existing in
      var current = existing ?? CapturePauseState.indefinite(now: now)
      current.mode = .indefinite
      current.resumeAt = nil
      current.resumeArmedAt = nil
      current.appHeartbeatAt = now
      current.lastReminderAt = now
      updatedPauseID = current.id
      return current
    }
    if applied, let updatedPauseID {
      notificationCenter.removeDeliveredNotifications(
        withIdentifiers: [warningIdentifier(for: updatedPauseID)]
      )
    }
    return applied
  }

  func prepareForSleep() {
    var updatedPauseID: String?
    _ = mutatePause(refreshBackend: false) { existing in
      guard var current = existing, current.mode == .timed else { return existing }
      current.resumeArmedAt = nil
      current.appHeartbeatAt = nil
      updatedPauseID = current.id
      return current
    }
    if let updatedPauseID {
      notificationCenter.removeDeliveredNotifications(
        withIdentifiers: [warningIdentifier(for: updatedPauseID)]
      )
    }
  }

  func tick(now: Date = Date()) {
    displayNow = now
    refreshState(now: now)
    guard let current = state else { return }

    if current.needsWakeRearm(at: now) {
      _ = mutatePause(
        at: now,
        expectedPauseID: current.id,
        refreshBackend: false
      ) { existing in
        guard var updated = existing else { return nil }
        updated.resumeArmedAt = nil
        updated.appHeartbeatAt = now
        return updated
      }
    } else if current.appHeartbeatAt == nil
      || now.timeIntervalSince(current.appHeartbeatAt!) >= CapturePauseState.heartbeatInterval
    {
      _ = mutatePause(
        at: now,
        expectedPauseID: current.id,
        refreshBackend: false
      ) { existing in
        guard var updated = existing else { return nil }
        updated.appHeartbeatAt = now
        return updated
      }
    }

    guard let refreshed = state else { return }
    switch refreshed.mode {
    case .timed:
      if let deadline = refreshed.effectiveResumeAt, now >= deadline {
        autoResume(previousState: refreshed)
      } else if refreshed.resumeArmedAt == nil,
        let resumeAt = refreshed.resumeAt,
        now >= resumeAt.addingTimeInterval(-CapturePauseState.warningLeadTime)
      {
        postResumeWarning(for: refreshed, now: now)
      }
    case .indefinite:
      if let reminderAt = refreshed.nextIndefiniteReminderAt, now >= reminderAt {
        postIndefiniteReminder(for: refreshed, now: now)
      }
    }
  }

  func handleNotificationAction(_ identifier: String, pauseID: String?) -> Bool {
    guard let pauseID else { return false }
    switch identifier {
    case Self.resumeActionIdentifier:
      return resume(expectedPauseID: pauseID)
    case Self.extendActionIdentifier:
      return extend(by: 30 * 60, expectedPauseID: pauseID)
    case Self.keepPausedActionIdentifier:
      return keepPaused(expectedPauseID: pauseID)
    default:
      return false
    }
  }

  private func refreshState(now: Date = Date()) {
    let loaded = store.load(now: now)
    if loaded != state {
      state = loaded
    }
  }

  private func persist(_ newState: CapturePauseState, refreshBackend: Bool = true) {
    do {
      try store.save(newState)
      state = newState
      lastError = nil
      if refreshBackend {
        backend.refresh()
      }
    } catch {
      lastError = "Could not update capture pause: \(error.localizedDescription)"
    }
  }

  @discardableResult
  private func mutatePause(
    at now: Date = Date(),
    expectedPauseID: String? = nil,
    refreshBackend: Bool = true,
    errorPrefix: String = "Could not update capture pause",
    _ transform: (CapturePauseState?) -> CapturePauseState?
  ) -> Bool {
    var applied = false
    do {
      let updated = try store.update(now: now) { current in
        guard expectedPauseID == nil || current?.id == expectedPauseID else {
          return current
        }
        applied = true
        return transform(current)
      }
      guard applied else { return false }
      state = updated
      lastError = nil
      if refreshBackend {
        backend.refresh()
      }
      return true
    } catch {
      lastError = "\(errorPrefix): \(error.localizedDescription)"
      return false
    }
  }

  @discardableResult
  private func resume(expectedPauseID: String?) -> Bool {
    var previousID: String?
    let applied = mutatePause(
      expectedPauseID: expectedPauseID,
      errorPrefix: "Could not resume capture"
    ) { current in
      previousID = current?.id
      return nil
    }
    if applied, let previousID {
      notificationCenter.removeDeliveredNotifications(
        withIdentifiers: [warningIdentifier(for: previousID)]
      )
    }
    return applied
  }

  private func autoResume(previousState: CapturePauseState) {
    if resume(expectedPauseID: previousState.id) {
      postInformationalNotification(
        identifier: "capture-resumed-\(previousState.id)",
        title: "OpenChronicle capture resumed",
        body: "The scheduled privacy pause has ended."
      )
    }
  }

  private func postResumeWarning(for pause: CapturePauseState, now: Date) {
    postActionableNotification(
      identifier: warningIdentifier(for: pause.id),
      pauseID: pause.id,
      title: "OpenChronicle capture is paused",
      body: "Capture will resume in 1 minute. Extend the pause if sensitive content is still visible.",
      now: now
    ) { [weak self] deliveredAt in
      guard let self else { return }
      _ = self.mutatePause(
        at: deliveredAt,
        expectedPauseID: pause.id,
        refreshBackend: false
      ) { existing in
        guard var current = existing,
          current.mode == .timed,
          current.resumeArmedAt == nil
        else { return existing }
        current.resumeArmedAt = deliveredAt
        current.appHeartbeatAt = deliveredAt
        return current
      }
    }
  }

  private func postIndefiniteReminder(for pause: CapturePauseState, now: Date) {
    let elapsed = Self.durationText(now.timeIntervalSince(pause.startedAt))
    postActionableNotification(
      identifier: "capture-pause-reminder-\(pause.id)-\(Int(now.timeIntervalSince1970))",
      pauseID: pause.id,
      title: "OpenChronicle capture is still paused",
      body: "Capture has been paused for \(elapsed). Resume it if your privacy-sensitive work is finished.",
      now: now
    ) { [weak self] deliveredAt in
      guard let self else { return }
      _ = self.mutatePause(
        at: deliveredAt,
        expectedPauseID: pause.id,
        refreshBackend: false
      ) { existing in
        guard var current = existing, current.mode == .indefinite else {
          return existing
        }
        current.lastReminderAt = deliveredAt
        current.appHeartbeatAt = deliveredAt
        return current
      }
    }
  }

  private func postActionableNotification(
    identifier: String,
    pauseID: String,
    title: String,
    body: String,
    now: Date,
    onSuccess: @escaping @MainActor (Date) -> Void
  ) {
    guard !isPostingNotification else { return }
    if let lastNotificationAttemptAt,
      now.timeIntervalSince(lastNotificationAttemptAt) < 30
    {
      return
    }
    isPostingNotification = true
    lastNotificationAttemptAt = now

    deliverNotification(
      identifier: identifier,
      title: title,
      body: body,
      categoryIdentifier: Self.categoryIdentifier,
      pauseID: pauseID
    ) { [weak self] error in
      Task { @MainActor in
        guard let self else { return }
        self.isPostingNotification = false
        if let error {
          self.lastError = error.localizedDescription
        } else {
          self.lastError = nil
          onSuccess(Date())
        }
      }
    }
  }

  private func postInformationalNotification(
    identifier: String,
    title: String,
    body: String
  ) {
    deliverNotification(
      identifier: identifier,
      title: title,
      body: body,
      categoryIdentifier: "",
      pauseID: nil
    ) { _ in }
  }

  private func deliverNotification(
    identifier: String,
    title: String,
    body: String,
    categoryIdentifier: String,
    pauseID: String?,
    completion: @escaping @MainActor (Error?) -> Void
  ) {
    Task { @MainActor [weak self] in
      guard let self else { return }
      do {
        let settings = await self.notificationCenter.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
          break
        case .notDetermined:
          let granted = try await self.notificationCenter.requestAuthorization(
            options: [.alert, .sound]
          )
          guard granted else {
            completion(CapturePauseNotificationError.permissionDenied)
            return
          }
        case .denied:
          completion(CapturePauseNotificationError.permissionDenied)
          return
        @unknown default:
          completion(CapturePauseNotificationError.permissionUnavailable)
          return
        }

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.categoryIdentifier = categoryIdentifier
        var userInfo = [Self.notificationTypeKey: Self.notificationType]
        if let pauseID {
          userInfo[Self.pauseIDKey] = pauseID
        }
        content.userInfo = userInfo
        let request = UNNotificationRequest(
          identifier: identifier,
          content: content,
          trigger: nil
        )
        try await self.notificationCenter.add(request)
        completion(nil)
      } catch {
        completion(error)
      }
    }
  }

  private func ensureNotificationAuthorization() {
    Task { @MainActor [weak self] in
      guard let self else { return }
      let settings = await self.notificationCenter.notificationSettings()
      guard settings.authorizationStatus == .notDetermined else { return }
      _ = try? await self.notificationCenter.requestAuthorization(options: [.alert, .sound])
    }
  }

  private func registerNotificationActions() {
    let resume = UNNotificationAction(
      identifier: Self.resumeActionIdentifier,
      title: "Resume Now"
    )
    let extend = UNNotificationAction(
      identifier: Self.extendActionIdentifier,
      title: "Extend 30 Minutes"
    )
    let keepPaused = UNNotificationAction(
      identifier: Self.keepPausedActionIdentifier,
      title: "Keep Paused"
    )
    let category = UNNotificationCategory(
      identifier: Self.categoryIdentifier,
      actions: [resume, extend, keepPaused],
      intentIdentifiers: []
    )
    notificationCenter.setNotificationCategories([category])
  }

  private func warningIdentifier(for pauseID: String) -> String {
    "capture-resume-warning-\(pauseID)"
  }

  private static func durationText(_ interval: TimeInterval) -> String {
    let seconds = max(0, Int(interval.rounded(.up)))
    if seconds < 60 { return "\(seconds)s" }
    let minutes = Int(ceil(Double(seconds) / 60))
    if minutes < 60 { return "\(minutes)m" }
    let hours = minutes / 60
    let remainder = minutes % 60
    return remainder == 0 ? "\(hours)h" : "\(hours)h \(remainder)m"
  }
}

private enum CapturePauseNotificationError: LocalizedError {
  case permissionDenied
  case permissionUnavailable

  var errorDescription: String? {
    switch self {
    case .permissionDenied:
      return "Capture remains paused because notifications are disabled in System Settings."
    case .permissionUnavailable:
      return "Capture remains paused because notification permission is unavailable."
    }
  }
}
