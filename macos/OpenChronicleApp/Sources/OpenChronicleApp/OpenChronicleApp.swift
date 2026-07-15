import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
  static weak var instance: AppDelegate?
  static var backend: BackendController?
  static var permissions: PermissionController?
  static var loginItem: LoginItemController?

  private var refreshTimer: Timer?
  private var controlCenterWindowController: NSWindowController?

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
        self?.showControlCenterWindow()
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
    showControlCenterWindow()
    return true
  }

  static func showControlCenter() {
    instance?.showControlCenterWindow()
  }

  private func showControlCenterWindow() {
    guard let backend = Self.backend,
      let permissions = Self.permissions,
      let loginItem = Self.loginItem
    else {
      return
    }

    if controlCenterWindowController == nil {
      let rootView = ControlCenterView(
        backend: backend,
        permissions: permissions,
        loginItem: loginItem
      )
      let hostingController = NSHostingController(rootView: rootView)
      let window = NSWindow(contentViewController: hostingController)
      window.title = "OpenChronicle Control Center"
      window.styleMask = [.titled, .closable, .miniaturizable]
      window.isReleasedWhenClosed = false
      window.center()
      controlCenterWindowController = NSWindowController(window: window)
    }

    controlCenterWindowController?.showWindow(nil)
    controlCenterWindowController?.window?.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }
}

@main
struct OpenChronicleDesktopApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var backend: BackendController
  @StateObject private var permissions: PermissionController
  @StateObject private var loginItem: LoginItemController

  init() {
    let backend = BackendController()
    let permissions = PermissionController()
    let loginItem = LoginItemController()
    _backend = StateObject(wrappedValue: backend)
    _permissions = StateObject(wrappedValue: permissions)
    _loginItem = StateObject(wrappedValue: loginItem)
    AppDelegate.backend = backend
    AppDelegate.permissions = permissions
    AppDelegate.loginItem = loginItem
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
