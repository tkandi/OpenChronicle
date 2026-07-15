import SwiftUI

struct ControlCenterView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var permissions: PermissionController
  @ObservedObject var loginItem: LoginItemController

  var body: some View {
    Form {
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
          title: "Input Monitoring",
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

      if let backendError = backend.lastError {
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

      if let loginError = loginItem.lastError {
        Section("Login Item Error") {
          Text(loginError)
            .foregroundStyle(.red)
        }
      }

      if let restartError = permissions.restartError {
        Section("Restart Error") {
          Text(restartError)
            .foregroundStyle(.red)
        }
      }
    }
    .formStyle(.grouped)
    .padding(8)
    .frame(width: 620, height: 610)
    .onAppear {
      permissions.refresh()
      backend.refresh()
      loginItem.refresh()
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
    return .green
  }

  private var lastCaptureText: String {
    guard let date = backend.snapshot.lastCaptureDate else { return "None" }
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .full
    return formatter.localizedString(for: date, relativeTo: Date())
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
