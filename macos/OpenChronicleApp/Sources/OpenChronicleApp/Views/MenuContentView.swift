import AppKit
import SwiftUI

struct MenuContentView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var permissions: PermissionController
  @ObservedObject var capturePause: CapturePauseController

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

      if capturePause.isPaused {
        Button {
          capturePause.resume()
        } label: {
          Label("Resume Capture", systemImage: "play.fill")
        }

        Menu {
          pauseDurationButtons
        } label: {
          Label("Change Pause…", systemImage: "clock.arrow.circlepath")
        }

        if let error = capturePause.lastError {
          Label(error, systemImage: "exclamationmark.triangle.fill")
        }
      } else if backend.snapshot.isRunning {
        Menu {
          pauseDurationButtons
        } label: {
          Label("Pause Capture…", systemImage: "pause.fill")
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
    if capturePause.isPaused { return capturePause.statusText }
    if !backend.snapshot.isRunning { return "Stopped" }
    if backend.snapshot.owner == .external { return "Running outside the app" }
    return "Capturing"
  }

  private var statusColor: Color {
    if backend.isTransitioning { return .orange }
    if capturePause.isPaused { return .orange }
    if !backend.snapshot.isRunning { return .red }
    return .green
  }

  @ViewBuilder
  private var pauseDurationButtons: some View {
    Button("30 Minutes") {
      capturePause.pause(for: 30 * 60)
    }
    Button("1 Hour") {
      capturePause.pause(for: 60 * 60)
    }
    Divider()
    Button("Until I Resume") {
      capturePause.pause(for: nil)
    }
  }
}
