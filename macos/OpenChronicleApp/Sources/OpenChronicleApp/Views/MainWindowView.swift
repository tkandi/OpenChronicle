import SwiftUI

struct MainWindowView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var permissions: PermissionController
  @ObservedObject var loginItem: LoginItemController
  @ObservedObject var statusDetails: StatusDetailsController
  @ObservedObject var configuration: ConfigurationController
  @ObservedObject var navigator: MainWindowNavigator

  var body: some View {
    NavigationSplitView {
      List(selection: $navigator.selection) {
        Section("Control") {
          sidebarRows(MainWindowSection.controlSections)
        }
        Section {
          sidebarRows(MainWindowSection.configurationSections)
        } header: {
          HStack {
            Text("Configuration")
            Spacer()
            if hasUnsavedConfiguration {
              Circle()
                .fill(.orange)
                .frame(width: 7, height: 7)
                .accessibilityLabel("Unsaved configuration changes")
            }
          }
        }
      }
      .listStyle(.sidebar)
      .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 260)
    } detail: {
      VStack(spacing: 0) {
        detailHeader
        Divider()
        detailContent
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
    .navigationSplitViewStyle(.balanced)
    .frame(minWidth: 900, minHeight: 680)
  }

  @ViewBuilder
  private func sidebarRows(_ sections: [MainWindowSection]) -> some View {
    ForEach(sections) { section in
      HStack(spacing: 8) {
        Label(section.title, systemImage: section.systemImage)
        Spacer()
        if section == .permissions && !permissions.criticalPermissionsGranted {
          Image(systemName: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
            .accessibilityLabel("Permissions need attention")
        }
      }
      .tag(section)
    }
  }

  private var detailHeader: some View {
    HStack(alignment: .center, spacing: 12) {
      Image(systemName: navigator.selectedSection.systemImage)
        .font(.title2)
        .foregroundStyle(.secondary)
        .frame(width: 28)
      VStack(alignment: .leading, spacing: 2) {
        Text(navigator.selectedSection.title)
          .font(.title2.bold())
        Text(navigator.selectedSection.subtitle)
          .font(.caption)
          .foregroundStyle(.secondary)
      }
      Spacer()
      if navigator.selectedSection.isConfiguration && hasUnsavedConfiguration {
        Label("Unsaved", systemImage: "pencil.circle.fill")
          .font(.caption)
          .foregroundStyle(.orange)
      }
    }
    .padding(.horizontal, 20)
    .padding(.vertical, 14)
  }

  @ViewBuilder
  private var detailContent: some View {
    switch navigator.selectedSection {
    case .overview:
      ControlCenterView(
        backend: backend,
        permissions: permissions,
        loginItem: loginItem,
        statusDetails: statusDetails,
        page: .overview
      )
    case .permissions:
      ControlCenterView(
        backend: backend,
        permissions: permissions,
        loginItem: loginItem,
        statusDetails: statusDetails,
        page: .permissions
      )
    case .runtime:
      ControlCenterView(
        backend: backend,
        permissions: permissions,
        loginItem: loginItem,
        statusDetails: statusDetails,
        page: .runtime
      )
    case .models:
      SettingsView(backend: backend, configuration: configuration, page: .models)
    case .capture:
      SettingsView(backend: backend, configuration: configuration, page: .capture)
    case .processing:
      SettingsView(backend: backend, configuration: configuration, page: .processing)
    case .mcp:
      SettingsView(backend: backend, configuration: configuration, page: .mcp)
    case .advanced:
      SettingsView(backend: backend, configuration: configuration, page: .advanced)
    }
  }

  private var hasUnsavedConfiguration: Bool {
    configuration.hasFormChanges || configuration.hasRawChanges
  }
}
