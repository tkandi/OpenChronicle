import AppKit
import SwiftUI

struct MenuContentView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var permissions: PermissionController

  var body: some View {
    Group {
      statusHeader

      if !permissions.criticalPermissionsGranted {
        Label("Permissions need attention", systemImage: "exclamationmark.triangle.fill")
      }

      if backend.snapshot.owner == .external {
        Button {
          backend.takeOverBackend()
        } label: {
          Label("Take Over Backend", systemImage: "arrow.triangle.2.circlepath")
        }
        .disabled(backend.isTransitioning)
      } else if !backend.snapshot.isRunning {
        Button {
          backend.startBackend()
        } label: {
          Label("Start", systemImage: "play.fill")
        }
        .disabled(backend.isTransitioning || !permissions.accessibilityGranted)
      }

      if backend.snapshot.isRunning {
        Button {
          backend.setPaused(!backend.snapshot.isPaused)
        } label: {
          Label(
            backend.snapshot.isPaused ? "Resume Capture" : "Pause Capture",
            systemImage: backend.snapshot.isPaused ? "play.fill" : "pause.fill"
          )
        }
      }

      Divider()

      Button {
        AppDelegate.showMainWindow()
      } label: {
        Label("Open OpenChronicle…", systemImage: "macwindow")
      }

      Button {
        backend.revealLogs()
      } label: {
        Label("Open Logs", systemImage: "doc.text.magnifyingglass")
      }

      Divider()

      Button("Quit OpenChronicle") {
        NSApplication.shared.terminate(nil)
      }
    }
  }

  private var statusHeader: some View {
    VStack(alignment: .leading, spacing: 3) {
      Text("OpenChronicle")
        .font(.headline)
      HStack(spacing: 6) {
        Circle()
          .fill(statusColor)
          .frame(width: 8, height: 8)
        Text(statusText)
          .foregroundStyle(.secondary)
      }
    }
    .padding(.vertical, 4)
  }

  private var statusText: String {
    if backend.isTransitioning { return "Changing backend state…" }
    if !backend.snapshot.isRunning { return "Stopped" }
    if backend.snapshot.isPaused { return "Capture paused" }
    if backend.snapshot.owner == .external { return "Running outside the app" }
    return "Capturing"
  }

  private var statusColor: Color {
    if backend.isTransitioning { return .orange }
    if !backend.snapshot.isRunning { return .red }
    if backend.snapshot.isPaused { return .orange }
    return .green
  }
}
