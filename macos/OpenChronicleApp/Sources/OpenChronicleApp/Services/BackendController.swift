import AppKit
import Combine
import Darwin
import Foundation

@MainActor
final class BackendController: ObservableObject {
  @Published private(set) var snapshot: RuntimeSnapshot = .stopped
  @Published private(set) var backendPath: String?
  @Published private(set) var isTransitioning = false
  @Published private(set) var lastError: String?

  private let paths: RuntimePaths
  private let probe: RuntimeProbe
  private let locator: BackendLocator
  private var managedProcess: Process?
  private var processLogHandle: FileHandle?
  private var autoStartSuppressed = false

  init(
    paths: RuntimePaths = .live(),
    locator: BackendLocator = BackendLocator()
  ) {
    self.paths = paths
    probe = RuntimeProbe(paths: paths)
    self.locator = locator
    backendPath = locator.locate()?.displayPath
    refresh()
  }

  func refresh() {
    let managedPID =
      managedProcess?.isRunning == true
      ? managedProcess?.processIdentifier
      : nil
    snapshot = probe.snapshot(managedPID: managedPID)
    backendPath = locator.locate()?.displayPath
  }

  func startIfNeeded(accessibilityGranted: Bool) {
    refresh()
    guard accessibilityGranted,
      !snapshot.isRunning,
      managedProcess?.isRunning != true,
      !autoStartSuppressed,
      !isTransitioning
    else {
      return
    }
    startBackend()
  }

  func startBackend() {
    refresh()
    guard !snapshot.isRunning,
      managedProcess?.isRunning != true,
      !isTransitioning
    else { return }
    guard let command = locator.locate() else {
      lastError = "OpenChronicle backend not found. Run bash install.sh first."
      return
    }

    isTransitioning = true
    autoStartSuppressed = false
    lastError = nil

    do {
      try FileManager.default.createDirectory(
        at: paths.logsDirectory,
        withIntermediateDirectories: true
      )
      if !FileManager.default.fileExists(atPath: paths.appHostLog.path) {
        FileManager.default.createFile(atPath: paths.appHostLog.path, contents: nil)
      }
      let logHandle = try FileHandle(forWritingTo: paths.appHostLog)
      try logHandle.seekToEnd()

      let process = Process()
      process.executableURL = command.executableURL
      process.arguments = command.argumentsPrefix + ["start", "--foreground"]
      var environment = ProcessInfo.processInfo.environment
      environment["OPENCHRONICLE_APP_HOSTED"] = "1"
      environment["OPENCHRONICLE_ROOT"] = paths.root.path
      environment["PYTHONUNBUFFERED"] = "1"
      process.environment = environment
      process.standardOutput = logHandle
      process.standardError = logHandle
      process.terminationHandler = { [weak self] completed in
        DispatchQueue.main.async {
          guard let self else { return }
          if self.managedProcess === completed {
            self.managedProcess = nil
            try? self.processLogHandle?.close()
            self.processLogHandle = nil
            self.isTransitioning = false
            if completed.terminationStatus != 0 && !self.autoStartSuppressed {
              self.lastError = "Backend exited with status \(completed.terminationStatus)."
            }
            self.refresh()
          }
        }
      }

      processLogHandle = logHandle
      try process.run()
      managedProcess = process
      backendPath = command.displayPath
      isTransitioning = false
      DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
        self?.refresh()
      }
    } catch {
      try? processLogHandle?.close()
      processLogHandle = nil
      managedProcess = nil
      isTransitioning = false
      lastError = "Could not start backend: \(error.localizedDescription)"
      refresh()
    }
  }

  func stopBackend() {
    refresh()
    autoStartSuppressed = true
    guard let pid = snapshot.pid else { return }
    isTransitioning = true
    if Darwin.kill(pid, SIGTERM) != 0 {
      lastError = "Could not stop backend: \(String(cString: strerror(errno)))."
      isTransitioning = false
      return
    }
    pollForStop(startAfter: false)
  }

  func takeOverBackend() {
    refresh()
    guard snapshot.owner == .external, let pid = snapshot.pid else {
      startBackend()
      return
    }
    isTransitioning = true
    autoStartSuppressed = false
    lastError = nil
    if Darwin.kill(pid, SIGTERM) != 0 {
      lastError = "Could not stop external backend: \(String(cString: strerror(errno)))."
      isTransitioning = false
      return
    }
    pollForStop(startAfter: true)
  }

  func restartBackend() {
    refresh()
    guard !isTransitioning else { return }
    if snapshot.owner == .external {
      lastError = "Take over the backend in OpenChronicle.app before restarting it."
      return
    }
    guard let pid = snapshot.pid else {
      startBackend()
      return
    }

    isTransitioning = true
    autoStartSuppressed = false
    lastError = nil
    if Darwin.kill(pid, SIGTERM) != 0 {
      lastError = "Could not restart backend: \(String(cString: strerror(errno)))."
      isTransitioning = false
      return
    }
    pollForStop(startAfter: true)
  }

  func revealLogs() {
    ensureDirectoryAndReveal(paths.logsDirectory)
  }

  func revealData() {
    ensureDirectoryAndReveal(paths.root)
  }

  func shutdownManagedBackend() {
    autoStartSuppressed = true
    if let process = managedProcess, process.isRunning {
      process.terminate()
    }
  }

  private func pollForStop(startAfter: Bool) {
    Task { @MainActor [weak self] in
      guard let self else { return }
      for _ in 0..<30 {
        try? await Task.sleep(nanoseconds: 100_000_000)
        self.refresh()
        if !self.snapshot.isRunning {
          self.isTransitioning = false
          if startAfter {
            self.startBackend()
          }
          return
        }
      }
      self.isTransitioning = false
      self.lastError = "Backend did not stop within 3 seconds."
    }
  }

  private func ensureDirectoryAndReveal(_ url: URL) {
    do {
      try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
      NSWorkspace.shared.activateFileViewerSelecting([url])
    } catch {
      lastError = error.localizedDescription
    }
  }
}
