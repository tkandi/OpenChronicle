import Foundation
import XCTest

@testable import OpenChronicleApp

final class StatusDetailsTests: XCTestCase {
  private let payload = #"""
    {
      "schema_version": 1,
      "generated_at": "2026-07-16T00:00:00+08:00",
      "version": "0.1.0",
      "root": "/tmp/openchronicle",
      "daemon": {"running": true, "pid": 1234, "uptime": "2h 5m"},
      "health": {"label": "healthy", "state": "healthy"},
      "capture": {"state": "active", "paused": false},
      "last_capture": {
        "timestamp": "2026-07-16T00:00:00+08:00",
        "relative": "just now",
        "app": "Safari",
        "file": "capture.json"
      },
      "buffer": {"count": 42, "last_file": "capture.json"},
      "sessions": {"total": 12, "reduced": 10, "ended": 1, "failed": 1},
      "memory": {"active_files": 7, "dormant_files": 2, "entries": 99},
      "timeline": {"blocks": 123, "last_end": "2026-07-16T00:00:00+08:00"},
      "models": {
        "timeline": {
          "model": "gpt-test", "checked": false, "ok": null,
          "latency_ms": null, "error": null, "mocked": false
        },
        "reducer": {
          "model": "gpt-test", "checked": false, "ok": null,
          "latency_ms": null, "error": null, "mocked": false
        },
        "classifier": {
          "model": "gpt-test", "checked": false, "ok": null,
          "latency_ms": null, "error": null, "mocked": false
        },
        "compact": {
          "model": "gpt-test", "checked": false, "ok": null,
          "latency_ms": null, "error": null, "mocked": false
        }
      }
    }
    """#

  func testStatusDetailsDecodesCLIJSON() throws {
    let details = try JSONDecoder().decode(StatusDetails.self, from: Data(payload.utf8))

    XCTAssertEqual(details.schemaVersion, 1)
    XCTAssertEqual(details.daemon.pid, 1234)
    XCTAssertEqual(details.health.state, "healthy")
    XCTAssertEqual(details.lastCapture.app, "Safari")
    XCTAssertEqual(details.buffer.count, 42)
    XCTAssertEqual(details.sessions.failed, 1)
    XCTAssertEqual(details.memory.entries, 99)
    XCTAssertEqual(details.timeline.blocks, 123)
    XCTAssertEqual(details.models["timeline"]?.model, "gpt-test")
  }

  @MainActor
  func testStatusDetailsControllerRunsFastStatusCommand() async throws {
    let root = FileManager.default.temporaryDirectory
      .appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }

    let executable = root.appendingPathComponent("fake-openchronicle")
    let script = """
      #!/bin/sh
      case "$*" in
        *"status --json --no-model-checks"*) ;;
        *) exit 9 ;;
      esac
      printf '%s\\n' '\(payload)'
      """
    try Data(script.utf8).write(to: executable)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o755],
      ofItemAtPath: executable.path
    )

    let paths = RuntimePaths(root: root)
    let locator = BackendLocator(
      environment: ["OPENCHRONICLE_BIN": executable.path],
      homeDirectory: root,
      bundleResources: nil
    )
    let controller = StatusDetailsController(paths: paths, locator: locator)

    await controller.refresh()

    XCTAssertNil(controller.lastError)
    XCTAssertEqual(controller.snapshot?.daemon.pid, 1234)
    XCTAssertEqual(controller.snapshot?.buffer.count, 42)
    XCTAssertNotNil(controller.lastUpdated)
  }
}
