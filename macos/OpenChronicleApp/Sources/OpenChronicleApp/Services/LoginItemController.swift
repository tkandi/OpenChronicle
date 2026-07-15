import AppKit
import Combine
import ServiceManagement

@MainActor
final class LoginItemController: ObservableObject {
  @Published private(set) var isEnabled = false
  @Published private(set) var requiresApproval = false
  @Published private(set) var lastError: String?

  init() {
    refresh()
  }

  func refresh() {
    switch SMAppService.mainApp.status {
    case .enabled:
      isEnabled = true
      requiresApproval = false
    case .requiresApproval:
      isEnabled = false
      requiresApproval = true
    case .notFound, .notRegistered:
      isEnabled = false
      requiresApproval = false
    @unknown default:
      isEnabled = false
      requiresApproval = false
    }
  }

  func setEnabled(_ enabled: Bool) {
    do {
      if enabled {
        try SMAppService.mainApp.register()
      } else {
        try SMAppService.mainApp.unregister()
      }
      lastError = nil
    } catch {
      lastError = error.localizedDescription
    }
    refresh()
  }

  func openLoginItemsSettings() {
    guard
      let url = URL(
        string: "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"
      )
    else {
      return
    }
    NSWorkspace.shared.open(url)
  }
}
