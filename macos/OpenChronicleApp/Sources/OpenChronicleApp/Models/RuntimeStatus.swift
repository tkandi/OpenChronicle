import Darwin
import Foundation

enum RuntimeOwner: Equatable {
  case stopped
  case app
  case external

  var label: String {
    switch self {
    case .stopped:
      return "Stopped"
    case .app:
      return "Managed by OpenChronicle.app"
    case .external:
      return "Started outside the app"
    }
  }
}

struct RuntimeSnapshot: Equatable {
  var pid: Int32?
  var isRunning: Bool
  var isPaused: Bool
  var owner: RuntimeOwner
  var lastCaptureDate: Date?
  var lastCaptureFileName: String?

  static let stopped = RuntimeSnapshot(
    pid: nil,
    isRunning: false,
    isPaused: false,
    owner: .stopped,
    lastCaptureDate: nil,
    lastCaptureFileName: nil
  )
}

struct RuntimePaths {
  let root: URL

  var pidFile: URL { root.appendingPathComponent(".pid") }
  var pausedFlag: URL { root.appendingPathComponent(".paused") }
  var configFile: URL { root.appendingPathComponent("config.toml") }
  var captureBuffer: URL { root.appendingPathComponent("capture-buffer", isDirectory: true) }
  var logsDirectory: URL { root.appendingPathComponent("logs", isDirectory: true) }
  var captureLog: URL { logsDirectory.appendingPathComponent("capture.log") }
  var appHostLog: URL { logsDirectory.appendingPathComponent("app-host.log") }

  static func live(fileManager: FileManager = .default) -> RuntimePaths {
    if let override = ProcessInfo.processInfo.environment["OPENCHRONICLE_ROOT"],
      !override.isEmpty
    {
      return RuntimePaths(
        root: URL(fileURLWithPath: NSString(string: override).expandingTildeInPath)
      )
    }
    return RuntimePaths(
      root: fileManager.homeDirectoryForCurrentUser.appendingPathComponent(
        ".openchronicle",
        isDirectory: true
      )
    )
  }
}

struct RuntimeProbe {
  let paths: RuntimePaths
  var fileManager: FileManager = .default

  func snapshot(managedPID: Int32?) -> RuntimeSnapshot {
    let pid = readPID()
    let running = pid.map(Self.isProcessAlive) ?? false
    let capture = latestCaptureFromLog()
    let owner: RuntimeOwner
    if !running {
      owner = .stopped
    } else if pid == managedPID {
      owner = .app
    } else {
      owner = .external
    }

    return RuntimeSnapshot(
      pid: running ? pid : nil,
      isRunning: running,
      isPaused: fileManager.fileExists(atPath: paths.pausedFlag.path),
      owner: owner,
      lastCaptureDate: capture.date,
      lastCaptureFileName: capture.fileName
    )
  }

  func readPID() -> Int32? {
    guard let data = try? Data(contentsOf: paths.pidFile),
      let value = String(data: data, encoding: .utf8)?.trimmingCharacters(
        in: .whitespacesAndNewlines
      ),
      let pid = Int32(value),
      pid > 0
    else {
      return nil
    }
    return pid
  }

  static func isProcessAlive(_ pid: Int32) -> Bool {
    guard pid > 0 else { return false }
    if Darwin.kill(pid, 0) == 0 {
      return true
    }
    return errno == EPERM
  }

  func latestCaptureFromLog() -> (date: Date?, fileName: String?) {
    guard let handle = try? FileHandle(forReadingFrom: paths.captureLog) else {
      return (nil, nil)
    }
    defer { try? handle.close() }

    guard let attributes = try? fileManager.attributesOfItem(atPath: paths.captureLog.path),
      let sizeNumber = attributes[.size] as? NSNumber
    else {
      return (nil, nil)
    }

    let size = sizeNumber.uint64Value
    let tailSize = min(size, 64 * 1024)
    do {
      try handle.seek(toOffset: size - tailSize)
      let data = try handle.readToEnd() ?? Data()
      guard let text = String(data: data, encoding: .utf8) else {
        return (attributes[.modificationDate] as? Date, nil)
      }
      for line in text.split(separator: "\n", omittingEmptySubsequences: true).reversed() {
        guard let marker = line.range(of: "capture ok: ") else { continue }
        let suffix = line[marker.upperBound...]
        guard let token = suffix.split(whereSeparator: \.isWhitespace).first else { continue }
        let fileName = String(token)
        let captureURL = paths.captureBuffer.appendingPathComponent(fileName)
        let captureAttributes = try? fileManager.attributesOfItem(atPath: captureURL.path)
        let date =
          captureAttributes?[.modificationDate] as? Date
          ?? attributes[.modificationDate] as? Date
        return (date, fileName)
      }
      return (attributes[.modificationDate] as? Date, nil)
    } catch {
      return (attributes[.modificationDate] as? Date, nil)
    }
  }
}

struct BackendCommand: Equatable {
  let executableURL: URL
  let argumentsPrefix: [String]
  let displayPath: String
}

struct BackendLocator {
  var fileManager: FileManager = .default
  var environment: [String: String] = ProcessInfo.processInfo.environment
  var homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser
  var bundleResources: URL? = Bundle.main.resourceURL

  func locate() -> BackendCommand? {
    var candidates: [URL] = []
    if let override = environment["OPENCHRONICLE_BIN"], !override.isEmpty {
      candidates.append(
        URL(fileURLWithPath: NSString(string: override).expandingTildeInPath)
      )
    }
    if let bundleResources {
      candidates.append(
        bundleResources.appendingPathComponent("backend/bin/openchronicle")
      )
    }
    candidates.append(
      homeDirectory.appendingPathComponent(".local/bin/openchronicle")
    )
    candidates.append(
      homeDirectory.appendingPathComponent(".openchronicle/venv/bin/openchronicle")
    )

    guard
      let executable = candidates.first(where: {
        fileManager.isExecutableFile(atPath: $0.path)
      })
    else {
      return nil
    }
    return BackendCommand(
      executableURL: executable,
      argumentsPrefix: [],
      displayPath: executable.path
    )
  }
}
