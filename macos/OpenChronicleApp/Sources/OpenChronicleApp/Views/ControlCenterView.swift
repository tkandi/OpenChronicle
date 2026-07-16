import Combine
import SwiftUI

enum ControlCenterPage: Hashable {
  case overview
  case permissions
  case runtime
}

struct ControlCenterView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var permissions: PermissionController
  @ObservedObject var loginItem: LoginItemController
  @ObservedObject var statusDetails: StatusDetailsController
  let page: ControlCenterPage
  @State private var controlCenterVisible = false

  private let statusRefreshTimer = Timer.publish(
    every: 60,
    on: .main,
    in: .common
  ).autoconnect()

  var body: some View {
    Form {
      if page == .overview {
        Section("Capture") {
          LabeledContent("Status") {
            HStack(spacing: 6) {
              Circle()
                .fill(runtimeColor)
                .frame(width: 8, height: 8)
              Text(runtimeStatus)
            }
          }
          LabeledContent("Backend") {
            Text(backend.snapshot.owner.label)
          }
          if let pid = backend.snapshot.pid {
            LabeledContent("PID", value: String(pid))
          }
          if let details = matchingDetails {
            LabeledContent("Health") {
              Text(details.health.label)
                .foregroundStyle(healthColor(details.health.state))
            }
            LabeledContent("Uptime", value: details.daemon.uptime)
          }
          LabeledContent("Last capture", value: lastCaptureText)

          HStack {
            if backend.snapshot.owner == .external {
              Button("Take Over in App") {
                backend.takeOverBackend()
              }
            } else if backend.snapshot.isRunning {
              Button("Stop") {
                backend.stopBackend()
              }
            } else {
              Button("Start") {
                backend.startBackend()
              }
              .disabled(!permissions.accessibilityGranted)
            }

            if backend.snapshot.isRunning {
              Button(backend.snapshot.isPaused ? "Resume Capture" : "Pause Capture") {
                backend.setPaused(!backend.snapshot.isPaused)
              }
            }

            Spacer()

            Button("Open Data") {
              backend.revealData()
            }
            Button("Open Logs") {
              backend.revealLogs()
            }
          }
          .disabled(backend.isTransitioning)
        }
      }

      if page == .runtime {
        Section("Runtime & Storage") {
          if let details = statusDetails.snapshot {
            LabeledContent("Version", value: details.version)
            LabeledContent("Data root") {
              Text(details.root)
                .font(.caption.monospaced())
                .textSelection(.enabled)
            }
            LabeledContent("Buffer") {
              VStack(alignment: .trailing, spacing: 2) {
                Text("\(details.buffer.count.formatted()) files")
                if let lastFile = details.buffer.lastFile {
                  Text("Latest: \(lastFile)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                }
              }
            }
            LabeledContent("Sessions") {
              Text(
                "\(details.sessions.total.formatted()) total · "
                  + "\(details.sessions.reduced.formatted()) reduced · "
                  + "\(details.sessions.ended.formatted()) ended · "
                  + "\(details.sessions.failed.formatted()) failed"
              )
            }
            LabeledContent("Memory") {
              Text(
                "\(details.memory.activeFiles.formatted()) active files · "
                  + "\(details.memory.dormantFiles.formatted()) dormant · "
                  + "\(details.memory.entries.formatted()) entries"
              )
            }
            LabeledContent("Timeline") {
              VStack(alignment: .trailing, spacing: 2) {
                Text("\(details.timeline.blocks.formatted()) blocks")
                if let lastEnd = details.timeline.lastEnd {
                  Text("Last end: \(lastEnd)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                }
              }
            }
          } else if statusDetails.isRefreshing {
            ProgressView("Loading detailed status…")
          } else {
            Text("Detailed status has not been loaded yet.")
              .foregroundStyle(.secondary)
          }

          HStack {
            if let lastUpdated = statusDetails.lastUpdated {
              Text("Updated \(relativeText(for: lastUpdated))")
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button(statusDetails.isRefreshing ? "Refreshing…" : "Refresh Status") {
              Task {
                await statusDetails.refresh()
              }
            }
            .disabled(statusDetails.isRefreshing || statusDetails.isCheckingModels)
          }
        }

        Section("Model Diagnostics") {
          if let details = statusDetails.snapshot {
            ForEach(["timeline", "reducer", "classifier", "compact"], id: \.self) { stage in
              if let diagnostic = details.models[stage] {
                LabeledContent(stage.capitalized) {
                  ModelDiagnosticView(diagnostic: diagnostic)
                }
              }
            }
          } else {
            Text("Load detailed status to see the configured models.")
              .foregroundStyle(.secondary)
          }

          HStack {
            Text("Runs a small real request for each distinct model configuration.")
              .font(.caption)
              .foregroundStyle(.secondary)
            Spacer()
            Button(statusDetails.isCheckingModels ? "Checking…" : "Run Model Diagnostics") {
              Task {
                await statusDetails.refresh(modelChecks: true)
              }
            }
            .disabled(statusDetails.isRefreshing || statusDetails.isCheckingModels)
          }
        }
      }

      if page == .permissions {
        Section("Privacy Permissions") {
          PermissionRow(
            title: "Accessibility",
            detail: "Reads the focused app's accessibility tree.",
            granted: permissions.accessibilityGranted,
            request: permissions.requestAccessibility,
            openSettings: permissions.openAccessibilitySettings
          )
          PermissionRow(
            title: "Screen Recording",
            detail: "Captures screenshots and checks visible window metadata.",
            granted: permissions.screenRecordingGranted,
            request: permissions.requestScreenRecording,
            openSettings: permissions.openScreenRecordingSettings
          )
          PermissionRow(
            title: "Input Monitoring (Optional)",
            detail: "Detects interaction timing; raw keystrokes are not stored.",
            granted: permissions.inputMonitoringGranted,
            request: permissions.requestInputMonitoring,
            openSettings: permissions.openInputMonitoringSettings
          )

          if let guidance = permissions.guidanceMessage {
            VStack(alignment: .leading, spacing: 8) {
              Label("Permission action required", systemImage: "arrow.up.forward.app")
                .font(.headline)
              Text(guidance)
                .font(.caption)
                .foregroundStyle(.secondary)
              Button("Restart OpenChronicle") {
                permissions.restartApplication()
              }
            }
            .padding(.vertical, 4)
          }

          if permissions.usesAdHocSignature {
            Label {
              Text(
                "Development signature: privacy permissions must be re-added after every rebuilt binary. Configure a stable signing identity before regular use."
              )
              .font(.caption)
              .foregroundStyle(.secondary)
            } icon: {
              Image(systemName: "signature")
                .foregroundStyle(.orange)
            }
          }
        }

        Section("Startup") {
          Toggle(
            "Launch OpenChronicle at login",
            isOn: Binding(
              get: { loginItem.isEnabled },
              set: { loginItem.setEnabled($0) }
            )
          )
          if loginItem.requiresApproval {
            HStack {
              Text("macOS is waiting for approval in Login Items.")
                .foregroundStyle(.secondary)
              Spacer()
              Button("Open Login Items") {
                loginItem.openLoginItemsSettings()
              }
            }
          }
        }
      }

      if page == .overview, let backendError = backend.lastError {
        Section("Backend Error") {
          Text(backendError)
            .foregroundStyle(.red)
          if let path = backend.backendPath {
            Text(path)
              .font(.caption.monospaced())
              .textSelection(.enabled)
          }
        }
      }

      if page == .runtime, let statusError = statusDetails.lastError {
        Section("Status Error") {
          Text(statusError)
            .foregroundStyle(.red)
        }
      }

      if page == .permissions, let loginError = loginItem.lastError {
        Section("Login Item Error") {
          Text(loginError)
            .foregroundStyle(.red)
        }
      }

      if page == .permissions, let restartError = permissions.restartError {
        Section("Restart Error") {
          Text(restartError)
            .foregroundStyle(.red)
        }
      }
    }
    .formStyle(.grouped)
    .padding(8)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .onAppear {
      controlCenterVisible = true
      permissions.refresh()
      backend.refresh()
      loginItem.refresh()
    }
    .onDisappear {
      controlCenterVisible = false
    }
    .task {
      await statusDetails.refresh()
    }
    .onChange(of: backend.snapshot.pid) { _ in
      Task {
        await statusDetails.refresh()
      }
    }
    .onChange(of: backend.snapshot.isPaused) { _ in
      Task {
        await statusDetails.refresh()
      }
    }
    .onReceive(statusRefreshTimer) { _ in
      guard controlCenterVisible else { return }
      Task {
        await statusDetails.refresh()
      }
    }
  }

  private var runtimeStatus: String {
    if backend.isTransitioning { return "Changing state…" }
    if !backend.snapshot.isRunning { return "Stopped" }
    if backend.snapshot.isPaused { return "Paused" }
    return "Active"
  }

  private var runtimeColor: Color {
    if backend.isTransitioning { return .orange }
    if !backend.snapshot.isRunning { return .red }
    if backend.snapshot.isPaused { return .orange }
    if let state = matchingDetails?.health.state {
      return healthColor(state)
    }
    return .green
  }

  private var lastCaptureText: String {
    if let details = matchingDetails {
      if let app = details.lastCapture.app, !app.isEmpty {
        return "\(details.lastCapture.relative) (\(app))"
      }
      return details.lastCapture.relative
    }
    guard let date = backend.snapshot.lastCaptureDate else { return "None" }
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .full
    return formatter.localizedString(for: date, relativeTo: Date())
  }

  private var matchingDetails: StatusDetails? {
    guard let details = statusDetails.snapshot else { return nil }
    if details.daemon.pid == backend.snapshot.pid {
      return details
    }
    if !details.daemon.running && !backend.snapshot.isRunning {
      return details
    }
    return nil
  }

  private func healthColor(_ state: String) -> Color {
    switch state {
    case "healthy":
      return .green
    case "warning":
      return .orange
    default:
      return .red
    }
  }

  private func relativeText(for date: Date) -> String {
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .abbreviated
    return formatter.localizedString(for: date, relativeTo: Date())
  }
}

private struct ModelDiagnosticView: View {
  let diagnostic: ModelDiagnostic

  var body: some View {
    VStack(alignment: .trailing, spacing: 2) {
      Text(diagnostic.model)
        .font(.body.monospaced())
        .textSelection(.enabled)
      Text(resultText)
        .font(.caption)
        .foregroundStyle(resultColor)
        .lineLimit(2)
        .multilineTextAlignment(.trailing)
    }
  }

  private var resultText: String {
    if !diagnostic.checked { return "Not checked" }
    if diagnostic.mocked { return "Available (mocked)" }
    if diagnostic.ok == true {
      if let latency = diagnostic.latencyMs {
        return "Available · \(latency) ms"
      }
      return "Available"
    }
    return diagnostic.error ?? "Check failed"
  }

  private var resultColor: Color {
    if !diagnostic.checked { return .secondary }
    return diagnostic.ok == true ? .green : .red
  }
}

private struct PermissionRow: View {
  let title: String
  let detail: String
  let granted: Bool
  let request: () -> Void
  let openSettings: () -> Void

  var body: some View {
    HStack(alignment: .center, spacing: 12) {
      Image(systemName: granted ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
        .foregroundStyle(granted ? .green : .orange)
        .font(.title3)

      VStack(alignment: .leading, spacing: 2) {
        Text(title)
        Text(detail)
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Spacer()

      if granted {
        Text("Granted")
          .foregroundStyle(.secondary)
      } else {
        Button("Request") {
          request()
        }
        Button("Open Settings") {
          openSettings()
        }
      }
    }
  }
}
