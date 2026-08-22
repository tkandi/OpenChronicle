import Combine
import Foundation

enum MainWindowSection: String, CaseIterable, Identifiable {
  case overview
  case permissions
  case runtime
  case protectionDiagnostics
  case models
  case capture
  case processing
  case mcp
  case advanced

  static let controlSections: [MainWindowSection] = [
    .overview,
    .permissions,
    .runtime,
    .protectionDiagnostics,
  ]

  static let configurationSections: [MainWindowSection] = [
    .models,
    .capture,
    .processing,
    .mcp,
    .advanced,
  ]

  var id: String { rawValue }

  var title: String {
    switch self {
    case .overview: return "Overview"
    case .permissions: return "Permissions"
    case .runtime: return "Runtime & Storage"
    case .protectionDiagnostics: return "Protection Diagnostics"
    case .models: return "Models"
    case .capture: return "Capture"
    case .processing: return "Processing"
    case .mcp: return "MCP"
    case .advanced: return "Advanced"
    }
  }

  var sidebarTitle: String {
    switch self {
    case .protectionDiagnostics: return "Diagnostics"
    default: return title
    }
  }

  var subtitle: String {
    switch self {
    case .overview: return "Capture state and backend controls"
    case .permissions: return "Privacy access and launch behavior"
    case .runtime: return "Health, local data, and model diagnostics"
    case .protectionDiagnostics: return "Per-display reasons and privacy guard state"
    case .models: return "Provider and per-stage model selection"
    case .capture: return "Timing, screenshots, privacy, and retention"
    case .processing: return "Timeline, sessions, memory, and search"
    case .mcp: return "Embedded local MCP server"
    case .advanced: return "Complete config.toml editor"
    }
  }

  var systemImage: String {
    switch self {
    case .overview: return "gauge"
    case .permissions: return "lock.shield"
    case .runtime: return "internaldrive"
    case .protectionDiagnostics: return "checkmark.shield"
    case .models: return "cpu"
    case .capture: return "camera.viewfinder"
    case .processing: return "flowchart"
    case .mcp: return "network"
    case .advanced: return "chevron.left.forwardslash.chevron.right"
    }
  }

  var isConfiguration: Bool {
    Self.configurationSections.contains(self)
  }
}

@MainActor
final class MainWindowNavigator: ObservableObject {
  @Published var selection: MainWindowSection?

  init(selection: MainWindowSection = .overview) {
    self.selection = selection
  }

  var selectedSection: MainWindowSection {
    selection ?? .overview
  }

  func select(_ section: MainWindowSection?) {
    if let section {
      selection = section
    }
  }
}
