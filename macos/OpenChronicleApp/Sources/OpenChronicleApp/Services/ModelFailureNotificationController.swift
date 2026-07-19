import AppKit
import Combine
import Foundation
import UserNotifications

@MainActor
final class ModelFailureNotificationController: ObservableObject {
  @Published private(set) var isEnabled: Bool
  @Published private(set) var authorizationStatus: UNAuthorizationStatus = .notDetermined
  @Published private(set) var lastNotificationDate: Date?
  @Published private(set) var lastNotificationEventID: String?
  @Published private(set) var lastError: String?

  private static let enabledKey = "notifyOnModelFailures"
  private static let lastNotificationDateKey = "lastModelFailureNotificationAt"
  private static let lastNotificationEventIDKey = "lastModelFailureNotificationID"

  private let notificationCenter: UNUserNotificationCenter
  private let userDefaults: UserDefaults
  private var reader: ModelFailureEventReader
  private var pendingEvents: [ModelFailureEvent] = []
  private var started = false
  private var isRequestingAuthorization = false

  init(
    paths: RuntimePaths = .live(),
    notificationCenter: UNUserNotificationCenter = .current(),
    userDefaults: UserDefaults = .standard
  ) {
    self.notificationCenter = notificationCenter
    self.userDefaults = userDefaults
    reader = ModelFailureEventReader(fileURL: paths.modelFailureEvents)
    if userDefaults.object(forKey: Self.enabledKey) == nil {
      userDefaults.set(true, forKey: Self.enabledKey)
      isEnabled = true
    } else {
      isEnabled = userDefaults.bool(forKey: Self.enabledKey)
    }
    if let timestamp = userDefaults.object(forKey: Self.lastNotificationDateKey) as? NSNumber {
      lastNotificationDate = Date(timeIntervalSince1970: timestamp.doubleValue)
    }
    lastNotificationEventID = userDefaults.string(forKey: Self.lastNotificationEventIDKey)
  }

  var authorizationText: String {
    guard isEnabled else { return "Disabled" }
    switch authorizationStatus {
    case .notDetermined:
      return "Waiting for permission"
    case .denied:
      return "Denied in System Settings"
    case .authorized:
      return "Allowed"
    case .provisional:
      return "Provisional"
    case .ephemeral:
      return "Temporary"
    @unknown default:
      return "Unknown"
    }
  }

  var canOpenNotificationSettings: Bool {
    authorizationStatus == .denied
  }

  func start() {
    guard !started else { return }
    started = true
    reader.skipExistingEvents()
    refreshAuthorization(requestIfNeeded: isEnabled)
  }

  func setEnabled(_ enabled: Bool) {
    isEnabled = enabled
    userDefaults.set(enabled, forKey: Self.enabledKey)
    lastError = nil
    if enabled {
      refreshAuthorization(requestIfNeeded: true)
    } else {
      pendingEvents.removeAll()
    }
  }

  func poll() {
    do {
      let events = try reader.readNewEvents()
      guard isEnabled, !events.isEmpty else { return }
      for event in events {
        enqueueOrDeliver(event)
      }
    } catch {
      lastError = "Could not read model failure events: \(error.localizedDescription)"
    }
  }

  func sendTestNotification() {
    let event = ModelFailureEvent(
      schemaVersion: 1,
      id: "test-\(UUID().uuidString)",
      timestamp: ISO8601DateFormatter().string(from: Date()),
      stage: "test",
      model: "Configured model",
      errorType: "TestNotification",
      message: "Model failure alerts are enabled."
    )
    enqueueOrDeliver(event)
  }

  func openNotificationSettings() {
    guard let url = URL(
      string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=com.openchronicle.desktop"
    ) else { return }
    NSWorkspace.shared.open(url)
  }

  private var canDeliver: Bool {
    switch authorizationStatus {
    case .authorized, .provisional, .ephemeral:
      return true
    default:
      return false
    }
  }

  private func enqueueOrDeliver(_ event: ModelFailureEvent) {
    guard isEnabled else { return }
    if canDeliver {
      deliver(event)
      return
    }
    if authorizationStatus == .denied {
      lastError = "Notifications are disabled for OpenChronicle in System Settings."
      return
    }
    pendingEvents.append(event)
    requestAuthorization()
  }

  private func refreshAuthorization(requestIfNeeded: Bool = false) {
    notificationCenter.getNotificationSettings { [weak self] settings in
      Task { @MainActor in
        guard let self else { return }
        self.authorizationStatus = settings.authorizationStatus
        if self.canDeliver {
          self.flushPendingEvents()
        } else if requestIfNeeded && self.authorizationStatus == .notDetermined {
          self.requestAuthorization()
        }
      }
    }
  }

  private func requestAuthorization() {
    guard isEnabled, authorizationStatus == .notDetermined,
      !isRequestingAuthorization
    else { return }
    isRequestingAuthorization = true
    notificationCenter.requestAuthorization(options: [.alert, .sound]) { [weak self] _, error in
      Task { @MainActor in
        guard let self else { return }
        self.isRequestingAuthorization = false
        if let error {
          self.lastError = "Could not request notification permission: \(error.localizedDescription)"
        }
        self.refreshAuthorization()
      }
    }
  }

  private func flushPendingEvents() {
    guard canDeliver else { return }
    let events = pendingEvents
    pendingEvents.removeAll()
    for event in events {
      deliver(event)
    }
  }

  private func deliver(_ event: ModelFailureEvent) {
    let content = UNMutableNotificationContent()
    content.title = event.notificationTitle
    content.body = event.notificationBody
    content.sound = .default
    content.userInfo = ["openchronicle_section": "runtime"]
    let request = UNNotificationRequest(
      identifier: "model-failure-\(event.id)",
      content: content,
      trigger: nil
    )
    notificationCenter.add(request) { [weak self] error in
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.lastError = "Could not post model failure notification: \(error.localizedDescription)"
        } else {
          self.lastError = nil
          let deliveredAt = Date()
          self.lastNotificationDate = deliveredAt
          self.lastNotificationEventID = event.id
          self.userDefaults.set(
            deliveredAt.timeIntervalSince1970,
            forKey: Self.lastNotificationDateKey
          )
          self.userDefaults.set(event.id, forKey: Self.lastNotificationEventIDKey)
        }
      }
    }
  }
}
