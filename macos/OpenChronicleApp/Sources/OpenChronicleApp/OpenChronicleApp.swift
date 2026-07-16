import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  static weak var instance: AppDelegate?
  static var backend: BackendController?
  static var permissions: PermissionController?
  static var loginItem: LoginItemController?
  static var statusDetails: StatusDetailsController?
  static var configuration: ConfigurationController?
  static var mainWindowNavigator: MainWindowNavigator?

  private var refreshTimer: Timer?
  private var mainWindowController: NSWindowController?

  func applicationDidFinishLaunching(_ notification: Notification) {
    Self.instance = self
    Self.permissions?.refresh()
    Self.backend?.refresh()
    Self.loginItem?.refresh()
    Self.backend?.startIfNeeded(
      accessibilityGranted: Self.permissions?.accessibilityGranted == true
    )

    refreshTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
      Task { @MainActor in
        Self.permissions?.refresh()
        Self.backend?.refresh()
        Self.loginItem?.refresh()
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
    Self.backend?.shutdownManagedBackend()
  }

  func applicationShouldHandleReopen(
    _ sender: NSApplication,
    hasVisibleWindows flag: Bool
  ) -> Bool {
    showMainWindow()
    return true
  }

  static func showMainWindow() {
    instance?.showMainWindow()
  }

  private func showMainWindow(section: MainWindowSection? = nil) {
    guard let backend = Self.backend,
      let permissions = Self.permissions,
      let loginItem = Self.loginItem,
      let statusDetails = Self.statusDetails,
      let configuration = Self.configuration,
      let navigator = Self.mainWindowNavigator
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
        navigator: navigator
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

@main
struct OpenChronicleDesktopApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var backend: BackendController
  @StateObject private var permissions: PermissionController
  @StateObject private var loginItem: LoginItemController
  @StateObject private var statusDetails: StatusDetailsController
  @StateObject private var configuration: ConfigurationController
  @StateObject private var mainWindowNavigator: MainWindowNavigator

  init() {
    let backend = BackendController()
    let permissions = PermissionController()
    let loginItem = LoginItemController()
    let statusDetails = StatusDetailsController()
    let configuration = ConfigurationController()
    let mainWindowNavigator = MainWindowNavigator()
    _backend = StateObject(wrappedValue: backend)
    _permissions = StateObject(wrappedValue: permissions)
    _loginItem = StateObject(wrappedValue: loginItem)
    _statusDetails = StateObject(wrappedValue: statusDetails)
    _configuration = StateObject(wrappedValue: configuration)
    _mainWindowNavigator = StateObject(wrappedValue: mainWindowNavigator)
    AppDelegate.backend = backend
    AppDelegate.permissions = permissions
    AppDelegate.loginItem = loginItem
    AppDelegate.statusDetails = statusDetails
    AppDelegate.configuration = configuration
    AppDelegate.mainWindowNavigator = mainWindowNavigator
  }

  var body: some Scene {
    MenuBarExtra {
      MenuContentView(backend: backend, permissions: permissions)
    } label: {
      Image(systemName: menuBarSymbol)
        .accessibilityLabel("OpenChronicle")
    }
    .menuBarExtraStyle(.menu)
  }

  private var menuBarSymbol: String {
    if !backend.snapshot.isRunning { return "books.vertical" }
    if backend.snapshot.isPaused { return "pause.circle.fill" }
    return "record.circle.fill"
  }
}
