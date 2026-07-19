import Darwin
import Foundation
import XCTest

@testable import OpenChronicleApp

final class RuntimeProbeTests: XCTestCase {
  private var root: URL!
  private var paths: RuntimePaths!

  override func setUpWithError() throws {
    root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    paths = RuntimePaths(root: root)
    try FileManager.default.createDirectory(
      at: paths.logsDirectory,
      withIntermediateDirectories: true
    )
    try FileManager.default.createDirectory(
      at: paths.captureBuffer,
      withIntermediateDirectories: true
    )
  }

  override func tearDownWithError() throws {
    try? FileManager.default.removeItem(at: root)
  }

  func testSnapshotRecognizesCurrentProcessAsManaged() throws {
    let pid = getpid()
    try Data("\(pid)\n".utf8).write(to: paths.pidFile)
    let snapshot = RuntimeProbe(paths: paths).snapshot(managedPID: pid)

    XCTAssertTrue(snapshot.isRunning)
    XCTAssertEqual(snapshot.pid, pid)
    XCTAssertEqual(snapshot.owner, .app)
  }

  func testSnapshotIgnoresStalePID() throws {
    try Data("999999\n".utf8).write(to: paths.pidFile)
    let snapshot = RuntimeProbe(paths: paths).snapshot(managedPID: nil)

    XCTAssertFalse(snapshot.isRunning)
    XCTAssertNil(snapshot.pid)
    XCTAssertEqual(snapshot.owner, .stopped)
  }

  func testPauseAndLatestCaptureAreReported() throws {
    let fileName = "2026-07-15T18-18-29p08-00.json"
    let capture = paths.captureBuffer.appendingPathComponent(fileName)
    try Data("{}".utf8).write(to: capture)
    try Data("paused".utf8).write(to: paths.pausedFlag)
    let log = "2026-07-15 18:18:29 [INFO] capture ok: \(fileName) trigger=manual\n"
    try Data(log.utf8).write(to: paths.captureLog)

    let snapshot = RuntimeProbe(paths: paths).snapshot(managedPID: nil)

    XCTAssertTrue(snapshot.isPaused)
    XCTAssertEqual(snapshot.lastCaptureFileName, fileName)
    XCTAssertNotNil(snapshot.lastCaptureDate)
  }

  func testBackendLocatorUsesExplicitOverrideFirst() throws {
    let executable = root.appendingPathComponent("openchronicle")
    FileManager.default.createFile(atPath: executable.path, contents: Data("#!/bin/sh\n".utf8))
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )
    let locator = BackendLocator(
      environment: ["OPENCHRONICLE_BIN": executable.path],
      homeDirectory: root,
      bundleResources: nil
    )

    XCTAssertEqual(locator.locate()?.executableURL, executable)
  }

  @MainActor
  func testBackendControllerHostsForegroundChildAndStopsIt() async throws {
    let executable = root.appendingPathComponent("fake-openchronicle")
    let script = """
      #!/bin/sh
      mkdir -p "$OPENCHRONICLE_ROOT/logs"
      echo $$ > "$OPENCHRONICLE_ROOT/.pid"
      trap 'rm -f "$OPENCHRONICLE_ROOT/.pid"; exit 0' TERM INT
      while true; do sleep 0.1; done
      """
    try Data(script.utf8).write(to: executable)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )
    let locator = BackendLocator(
      environment: ["OPENCHRONICLE_BIN": executable.path],
      homeDirectory: root,
      bundleResources: nil
    )
    let controller = BackendController(paths: paths, locator: locator)

    controller.startBackend()
    for _ in 0..<30 where !controller.snapshot.isRunning {
      try await Task.sleep(nanoseconds: 100_000_000)
      controller.refresh()
    }

    XCTAssertTrue(controller.snapshot.isRunning)
    XCTAssertEqual(controller.snapshot.owner, .app)
    let firstPID = try XCTUnwrap(controller.snapshot.pid)

    controller.restartBackend()
    for _ in 0..<50 {
      try await Task.sleep(nanoseconds: 100_000_000)
      controller.refresh()
      if controller.snapshot.isRunning, controller.snapshot.pid != firstPID {
        break
      }
    }

    XCTAssertTrue(controller.snapshot.isRunning)
    XCTAssertEqual(controller.snapshot.owner, .app)
    XCTAssertNotEqual(controller.snapshot.pid, firstPID)

    controller.stopBackend()
    for _ in 0..<40 where controller.snapshot.isRunning {
      try await Task.sleep(nanoseconds: 100_000_000)
      controller.refresh()
    }

    XCTAssertFalse(controller.snapshot.isRunning)
    controller.shutdownManagedBackend()
  }

  @MainActor
  func testBackendStartIsDeduplicatedBeforePIDFileAppears() async throws {
    let executable = root.appendingPathComponent("slow-openchronicle")
    let launches = root.appendingPathComponent("launches")
    let script = """
      #!/bin/sh
      echo launch >> "$OPENCHRONICLE_ROOT/launches"
      sleep 0.4
      echo $$ > "$OPENCHRONICLE_ROOT/.pid"
      trap 'rm -f "$OPENCHRONICLE_ROOT/.pid"; exit 0' TERM INT
      while true; do sleep 0.1; done
      """
    try Data(script.utf8).write(to: executable)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )
    let locator = BackendLocator(
      environment: ["OPENCHRONICLE_BIN": executable.path],
      homeDirectory: root,
      bundleResources: nil
    )
    let controller = BackendController(paths: paths, locator: locator)
    defer { controller.shutdownManagedBackend() }

    controller.startBackend()
    controller.startBackend()
    for _ in 0..<20 where !FileManager.default.fileExists(atPath: launches.path) {
      try await Task.sleep(nanoseconds: 50_000_000)
    }

    let launchText = try String(contentsOf: launches, encoding: .utf8)
    XCTAssertEqual(launchText.split(separator: "\n").count, 1)

    for _ in 0..<20 where !controller.snapshot.isRunning {
      try await Task.sleep(nanoseconds: 100_000_000)
      controller.refresh()
    }
    XCTAssertTrue(controller.snapshot.isRunning)

    controller.stopBackend()
    for _ in 0..<30 where controller.snapshot.isRunning {
      try await Task.sleep(nanoseconds: 100_000_000)
      controller.refresh()
    }
  }
}
