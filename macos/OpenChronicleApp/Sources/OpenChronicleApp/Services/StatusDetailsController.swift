import Combine
import Foundation

enum StatusDetailsError: LocalizedError {
  case backendNotFound
  case commandFailed(String)
  case invalidResponse(String)

  var errorDescription: String? {
    switch self {
    case .backendNotFound:
      return "OpenChronicle backend not found. Run bash install.sh first."
    case .commandFailed(let message):
      return "Status command failed: \(message)"
    case .invalidResponse(let message):
      return "Could not read backend status: \(message)"
    }
  }
}

struct StatusCommandRunner {
  func fetch(
    command: BackendCommand,
    root: URL,
    modelChecks: Bool
  ) async throws -> StatusDetails {
    try await withCheckedThrowingContinuation { continuation in
      DispatchQueue.global(qos: .utility).async {
        do {
          continuation.resume(
            returning: try run(command: command, root: root, modelChecks: modelChecks)
          )
        } catch {
          continuation.resume(throwing: error)
        }
      }
    }
  }

  private func run(
    command: BackendCommand,
    root: URL,
    modelChecks: Bool
  ) throws -> StatusDetails {
    let process = Process()
    let stdout = Pipe()
    let stderr = Pipe()
    process.executableURL = command.executableURL
    process.arguments =
      command.argumentsPrefix + [
        "status",
        "--json",
        modelChecks ? "--model-checks" : "--no-model-checks",
      ]
    var environment = ProcessInfo.processInfo.environment
    environment["OPENCHRONICLE_ROOT"] = root.path
    environment["LITELLM_LOG"] = "ERROR"
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    process.environment = environment
    process.standardOutput = stdout
    process.standardError = stderr

    try process.run()
    process.waitUntilExit()

    let outputData = stdout.fileHandleForReading.readDataToEndOfFile()
    let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
    guard process.terminationStatus == 0 else {
      let message = conciseMessage(from: errorData, fallback: outputData)
      throw StatusDetailsError.commandFailed(message)
    }
    return try decode(outputData)
  }

  private func decode(_ data: Data) throws -> StatusDetails {
    let decoder = JSONDecoder()
    if let status = try? decoder.decode(StatusDetails.self, from: data) {
      return status
    }

    // Be tolerant of a dependency writing an informational line before the
    // single-line JSON payload. The CLI itself guarantees clean JSON, but this
    // keeps the UI useful with older installed environments as well.
    if let text = String(data: data, encoding: .utf8) {
      for line in text.split(whereSeparator: \.isNewline).reversed() {
        let candidate = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard candidate.hasPrefix("{"), candidate.hasSuffix("}") else { continue }
        if let candidateData = candidate.data(using: .utf8),
          let status = try? decoder.decode(StatusDetails.self, from: candidateData)
        {
          return status
        }
      }
      throw StatusDetailsError.invalidResponse(truncated(text))
    }
    throw StatusDetailsError.invalidResponse("backend returned non-text output")
  }

  private func conciseMessage(from preferred: Data, fallback: Data) -> String {
    for data in [preferred, fallback] {
      if let text = String(data: data, encoding: .utf8),
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      {
        return truncated(text)
      }
    }
    return "exit status was non-zero"
  }

  private func truncated(_ value: String) -> String {
    let compact = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return compact.count > 500 ? String(compact.prefix(500)) + "…" : compact
  }
}

@MainActor
final class StatusDetailsController: ObservableObject {
  @Published private(set) var snapshot: StatusDetails?
  @Published private(set) var isRefreshing = false
  @Published private(set) var isCheckingModels = false
  @Published private(set) var lastUpdated: Date?
  @Published private(set) var lastError: String?

  private let paths: RuntimePaths
  private let locator: BackendLocator
  private let runner: StatusCommandRunner

  init(
    paths: RuntimePaths = .live(),
    locator: BackendLocator = BackendLocator(),
    runner: StatusCommandRunner = StatusCommandRunner()
  ) {
    self.paths = paths
    self.locator = locator
    self.runner = runner
  }

  func refresh(modelChecks: Bool = false) async {
    guard !isRefreshing, !isCheckingModels else { return }
    guard let command = locator.locate() else {
      lastError = StatusDetailsError.backendNotFound.localizedDescription
      return
    }

    if modelChecks {
      isCheckingModels = true
    } else {
      isRefreshing = true
    }
    lastError = nil
    defer {
      isRefreshing = false
      isCheckingModels = false
    }

    do {
      var fetched = try await runner.fetch(
        command: command,
        root: paths.root,
        modelChecks: modelChecks
      )
      if !modelChecks, let previous = snapshot {
        for (stage, diagnostic) in previous.models where diagnostic.checked {
          guard fetched.models[stage]?.model == diagnostic.model else { continue }
          fetched.models[stage] = diagnostic
        }
      }
      snapshot = fetched
      lastUpdated = Date()
    } catch {
      lastError = error.localizedDescription
    }
  }
}
