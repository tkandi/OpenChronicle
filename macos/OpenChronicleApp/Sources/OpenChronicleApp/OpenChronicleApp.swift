import AppKit
import SwiftUI
import UserNotifications

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  static weak var instance: AppDelegate?
  static var backend: BackendController?
  static var permissions: PermissionController?
  static var loginItem: LoginItemController?
  static var statusDetails: StatusDetailsController?
  static var configuration: ConfigurationController?
  static var mainWindowNavigator: MainWindowNavigator?
  static var modelFailureNotifications: ModelFailureNotificationController?
  static var capturePause: CapturePauseController?

  private var refreshTimer: Timer?
  private var mainWindowController: NSWindowController?

  func applicationDidFinishLaunching(_ notification: Notification) {
    Self.instance = self
    UNUserNotificationCenter.current().delegate = self
    Self.modelFailureNotifications?.start()
    Self.capturePause?.start()
    NSWorkspace.shared.notificationCenter.addObserver(
      self,
      selector: #selector(workspaceDidWake(_:)),
      name: NSWorkspace.didWakeNotification,
      object: nil
    )
    NSWorkspace.shared.notificationCenter.addObserver(
      self,
      selector: #selector(workspaceWillSleep(_:)),
      name: NSWorkspace.willSleepNotification,
      object: nil
    )
    Self.permissions?.refresh()
    Self.backend?.refresh()
    Self.loginItem?.refresh()
    Self.backend?.startIfNeeded(
      accessibilityGranted: Self.permissions?.accessibilityGranted == true
    )

    refreshTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
      Task { @MainActor in
        Self.permissions?.refresh()
        Self.capturePause?.tick()
        Self.backend?.refresh()
        Self.loginItem?.refresh()
        Self.modelFailureNotifications?.poll()
        Self.backend?.startIfNeeded(
          accessibilityGranted: Self.permissions?.accessibilityGranted == true
        )
      }
    }

    if Self.permissions?.criticalPermissionsGranted != true {
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
        self?.showMainWindow(section: .permissions)
      }
    }
  }

  func applicationWillTerminate(_ notification: Notification) {
    refreshTimer?.invalidate()
    NSWorkspace.shared.notificationCenter.removeObserver(self)
    Self.backend?.shutdownManagedBackend()
  }

  @objc private func workspaceDidWake(_ notification: Notification) {
    Self.capturePause?.tick()
  }

  @objc private func workspaceWillSleep(_ notification: Notification) {
    Self.capturePause?.prepareForSleep()
  }

  func applicationShouldHandleReopen(
    _ sender: NSApplication,
    hasVisibleWindows flag: Bool
  ) -> Bool {
    showMainWindow()
    return true
  }

  static func showMainWindow(section: MainWindowSection? = nil) {
    instance?.showMainWindow(section: section)
  }

  private func showMainWindow(section: MainWindowSection? = nil) {
    guard let backend = Self.backend,
      let permissions = Self.permissions,
      let loginItem = Self.loginItem,
      let statusDetails = Self.statusDetails,
      let configuration = Self.configuration,
      let navigator = Self.mainWindowNavigator,
      let modelFailureNotifications = Self.modelFailureNotifications,
      let capturePause = Self.capturePause
    else {
      return
    }

    navigator.select(section)
    if mainWindowController == nil {
      let rootView = MainWindowView(
        backend: backend,
        permissions: permissions,
        loginItem: loginItem,
        statusDetails: statusDetails,
        configuration: configuration,
        navigator: navigator,
        modelFailureNotifications: modelFailureNotifications,
        capturePause: capturePause
      )
      let hostingController = NSHostingController(rootView: rootView)
      let window = NSWindow(contentViewController: hostingController)
      window.title = "OpenChronicle"
      window.styleMask = [.titled, .closable, .miniaturizable]
      window.isReleasedWhenClosed = false
      window.setContentSize(NSSize(width: 1040, height: 760))
      window.minSize = NSSize(width: 900, height: 680)
      window.center()
      mainWindowController = NSWindowController(window: window)
    }

    mainWindowController?.showWindow(nil)
    mainWindowController?.window?.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }
}

extension AppDelegate: UNUserNotificationCenterDelegate {
  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification,
    withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
  ) {
    completionHandler([.banner, .sound])
  }

  nonisolated func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse,
    withCompletionHandler completionHandler: @escaping () -> Void
  ) {
    Task { @MainActor in
      let handled = Self.capturePause?.handleNotificationAction(
        response.actionIdentifier,
        pauseID: response.notification.request.content.userInfo[
          CapturePauseController.pauseIDKey
        ] as? String
      ) == true
      if !handled {
        Self.showMainWindow(section: .runtime)
      }
      completionHandler()
    }
  }
}

@main
struct OpenChronicleDesktopApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var backend: BackendController
  @StateObject private var permissions: PermissionController
  @StateObject private var loginItem: LoginItemController
  @StateObject private var statusDetails: StatusDetailsController
  @StateObject private var configuration: ConfigurationController
  @StateObject private var mainWindowNavigator: MainWindowNavigator
  @StateObject private var modelFailureNotifications: ModelFailureNotificationController
  @StateObject private var capturePause: CapturePauseController

  init() {
    let backend = BackendController()
    let permissions = PermissionController()
    let loginItem = LoginItemController()
    let statusDetails = StatusDetailsController()
    let configuration = ConfigurationController()
    let mainWindowNavigator = MainWindowNavigator()
    let modelFailureNotifications = ModelFailureNotificationController()
    let capturePause = CapturePauseController(backend: backend)
    _backend = StateObject(wrappedValue: backend)
    _permissions = StateObject(wrappedValue: permissions)
    _loginItem = StateObject(wrappedValue: loginItem)
    _statusDetails = StateObject(wrappedValue: statusDetails)
    _configuration = StateObject(wrappedValue: configuration)
    _mainWindowNavigator = StateObject(wrappedValue: mainWindowNavigator)
    _modelFailureNotifications = StateObject(wrappedValue: modelFailureNotifications)
    _capturePause = StateObject(wrappedValue: capturePause)
    AppDelegate.backend = backend
    AppDelegate.permissions = permissions
    AppDelegate.loginItem = loginItem
    AppDelegate.statusDetails = statusDetails
    AppDelegate.configuration = configuration
    AppDelegate.mainWindowNavigator = mainWindowNavigator
    AppDelegate.modelFailureNotifications = modelFailureNotifications
    AppDelegate.capturePause = capturePause
  }

  var body: some Scene {
    MenuBarExtra {
      MenuContentView(
        backend: backend,
        permissions: permissions,
        capturePause: capturePause
      )
    } label: {
      Image(systemName: menuBarSymbol)
        .accessibilityLabel("OpenChronicle")
    }
    .menuBarExtraStyle(.menu)
  }

  private var menuBarSymbol: String {
    if !backend.snapshot.isRunning { return "books.vertical" }
    if capturePause.isPaused { return "pause.circle.fill" }
    return "record.circle.fill"
  }
}
