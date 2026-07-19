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
    let previousID = state?.id
    do {
      try store.clear()
      state = nil
      lastError = nil
      backend.refresh()
      if let previousID {
        notificationCenter.removeDeliveredNotifications(
          withIdentifiers: [warningIdentifier(for: previousID)]
        )
      }
    } catch {
      lastError = "Could not resume capture: \(error.localizedDescription)"
    }
  }

  func extend(by duration: TimeInterval) {
    let now = Date()
    guard var current = store.load(now: now) else {
      pause(for: duration)
      return
    }
    current.mode = .timed
    current.resumeAt = max(now, current.resumeAt ?? now).addingTimeInterval(duration)
    current.resumeArmedAt = nil
    current.appHeartbeatAt = now
    current.lastReminderAt = nil
    notificationCenter.removeDeliveredNotifications(
      withIdentifiers: [warningIdentifier(for: current.id)]
    )
    persist(current)
    ensureNotificationAuthorization()
  }

  func keepPaused() {
    let now = Date()
    guard var current = store.load(now: now) else {
      pause(for: nil)
      return
    }
    current.mode = .indefinite
    current.resumeAt = nil
    current.resumeArmedAt = nil
    current.appHeartbeatAt = now
    current.lastReminderAt = now
    notificationCenter.removeDeliveredNotifications(
      withIdentifiers: [warningIdentifier(for: current.id)]
    )
    persist(current)
  }

  func prepareForSleep() {
    guard var current = store.load(), current.mode == .timed else { return }
    current.resumeArmedAt = nil
    current.appHeartbeatAt = nil
    notificationCenter.removeDeliveredNotifications(
      withIdentifiers: [warningIdentifier(for: current.id)]
    )
    persist(current, refreshBackend: false)
  }

  func tick(now: Date = Date()) {
    displayNow = now
    refreshState(now: now)
    guard var current = state else { return }

    if current.needsWakeRearm(at: now) {
      current.resumeArmedAt = nil
      current.appHeartbeatAt = now
      persist(current, refreshBackend: false)
    } else if current.appHeartbeatAt == nil
      || now.timeIntervalSince(current.appHeartbeatAt!) >= CapturePauseState.heartbeatInterval
    {
      current.appHeartbeatAt = now
      persist(current, refreshBackend: false)
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
    guard let current = store.load(), current.id == pauseID else { return false }
    switch identifier {
    case Self.resumeActionIdentifier:
      resume()
      return true
    case Self.extendActionIdentifier:
      extend(by: 30 * 60)
      return true
    case Self.keepPausedActionIdentifier:
      keepPaused()
      return true
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

  private func autoResume(previousState: CapturePauseState) {
    do {
      try store.clear()
      state = nil
      lastError = nil
      backend.refresh()
      notificationCenter.removeDeliveredNotifications(
        withIdentifiers: [warningIdentifier(for: previousState.id)]
      )
      postInformationalNotification(
        identifier: "capture-resumed-\(previousState.id)",
        title: "OpenChronicle capture resumed",
        body: "The scheduled privacy pause has ended."
      )
    } catch {
      lastError = "Could not resume capture: \(error.localizedDescription)"
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
      guard let self, var current = self.store.load(now: deliveredAt),
        current.id == pause.id,
        current.mode == .timed,
        current.resumeArmedAt == nil
      else { return }
      current.resumeArmedAt = deliveredAt
      current.appHeartbeatAt = deliveredAt
      self.persist(current, refreshBackend: false)
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
      guard let self, var current = self.store.load(now: deliveredAt),
        current.id == pause.id,
        current.mode == .indefinite
      else { return }
      current.lastReminderAt = deliveredAt
      current.appHeartbeatAt = deliveredAt
      self.persist(current, refreshBackend: false)
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
