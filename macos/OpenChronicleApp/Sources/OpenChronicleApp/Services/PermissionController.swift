import AppKit
import ApplicationServices
import Combine
import CoreGraphics
import Foundation

@MainActor
final class PermissionController: ObservableObject {
  @Published private(set) var accessibilityGranted = false
  @Published private(set) var screenRecordingGranted = false
  @Published private(set) var inputMonitoringGranted = false
  @Published private(set) var guidanceMessage: String?
  @Published private(set) var restartError: String?

  let signingMode: String

  var usesAdHocSignature: Bool {
    signingMode == "ad-hoc"
  }

  var criticalPermissionsGranted: Bool {
    accessibilityGranted && screenRecordingGranted
  }

  init(bundle: Bundle = .main) {
    signingMode =
      bundle.object(forInfoDictionaryKey: "OpenChronicleSigningMode") as? String
      ?? "unknown"
    refresh()
  }

  func refresh() {
    accessibilityGranted = AXIsProcessTrusted()
    screenRecordingGranted = CGPreflightScreenCaptureAccess()
    inputMonitoringGranted = CGPreflightListenEventAccess()
  }

  func requestAccessibility() {
    let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
    let options = [key: true] as CFDictionary
    let granted = AXIsProcessTrustedWithOptions(options)
    if !granted {
      guidanceMessage =
        "If OpenChronicle is already enabled here, remove the old row, add /Applications/OpenChronicle.app again, then restart the app."
      openAccessibilitySettings()
    }
    scheduleRefresh()
  }

  func requestScreenRecording() {
    let granted = CGRequestScreenCaptureAccess()
    if !granted {
      guidanceMessage = "Enable OpenChronicle in Screen Recording, then restart the app."
      openScreenRecordingSettings()
    }
    scheduleRefresh()
  }

  func requestInputMonitoring() {
    let granted = CGRequestListenEventAccess()
    if !granted {
      guidanceMessage =
        "macOS does not always show the Input Monitoring prompt again. Enable OpenChronicle in the opened settings page, then restart the app."
      openInputMonitoringSettings()
    }
    scheduleRefresh()
  }

  func restartApplication() {
    let relauncher = Process()
    relauncher.executableURL = URL(fileURLWithPath: "/bin/sh")
    relauncher.arguments = [
      "-c",
      "sleep 1; exec /usr/bin/open \"$1\"",
      "openchronicle-relaunch",
      Bundle.main.bundleURL.path,
    ]

    do {
      try relauncher.run()
      NSApplication.shared.terminate(nil)
    } catch {
      restartError = "Could not restart OpenChronicle: \(error.localizedDescription)"
    }
  }

  func openAccessibilitySettings() {
    openPrivacyPane("Privacy_Accessibility")
  }

  func openScreenRecordingSettings() {
    openPrivacyPane("Privacy_ScreenCapture")
  }

  func openInputMonitoringSettings() {
    openPrivacyPane("Privacy_ListenEvent")
  }

  private func scheduleRefresh() {
    for delay in [0.5, 1.5, 3.0] {
      DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
        self?.refresh()
      }
    }
  }

  private func openPrivacyPane(_ anchor: String) {
    guard
      let url = URL(
        string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)"
      )
    else {
      return
    }
    NSWorkspace.shared.open(url)
  }
}
