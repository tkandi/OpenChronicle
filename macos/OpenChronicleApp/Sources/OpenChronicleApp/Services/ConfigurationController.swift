import AppKit
import Combine
import Foundation

enum ConfigurationControllerError: LocalizedError {
  case backendNotFound
  case commandFailed(String)
  case invalidResponse(String)
  case invalidRequest(String)

  var errorDescription: String? {
    switch self {
    case .backendNotFound:
      return "OpenChronicle backend not found. Run bash install.sh first."
    case .commandFailed(let message):
      return message
    case .invalidResponse(let message):
      return "Could not read configuration response: \(message)"
    case .invalidRequest(let message):
      return message
    }
  }
}

struct ConfigurationCommandRunner {
  func snapshot(command: BackendCommand, root: URL) async throws -> ConfigurationSnapshot {
    let data = try await execute(
      command: command,
      root: root,
      arguments: ["config", "--json"],
      input: nil
    )
    do {
      return try JSONDecoder().decode(ConfigurationSnapshot.self, from: data)
    } catch {
      throw ConfigurationControllerError.invalidResponse(concise(data))
    }
  }

  func privacySnapshot(
    command: BackendCommand,
    root: URL
  ) async throws -> PrivacyConfigurationSnapshot {
    let data = try await execute(
      command: command,
      root: root,
      arguments: ["config", "--privacy-json"],
      input: nil
    )
    do {
      return try JSONDecoder().decode(PrivacyConfigurationSnapshot.self, from: data)
    } catch {
      throw ConfigurationControllerError.invalidResponse(concise(data))
    }
  }

  func mutate(
    command: BackendCommand,
    root: URL,
    action: String,
    request: [String: Any]
  ) async throws -> ConfigurationMutationResult {
    guard JSONSerialization.isValidJSONObject(request) else {
      throw ConfigurationControllerError.invalidRequest("Configuration request is not valid JSON.")
    }
    let input = try JSONSerialization.data(withJSONObject: request, options: [])
    let data = try await execute(
      command: command,
      root: root,
      arguments: ["config", action],
      input: input
    )
    do {
      return try JSONDecoder().decode(ConfigurationMutationResult.self, from: data)
    } catch {
      throw ConfigurationControllerError.invalidResponse(concise(data))
    }
  }

  private func execute(
    command: BackendCommand,
    root: URL,
    arguments: [String],
    input: Data?
  ) async throws -> Data {
    try await withCheckedThrowingContinuation { continuation in
      DispatchQueue.global(qos: .utility).async {
        do {
          continuation.resume(
            returning: try run(
              command: command,
              root: root,
              arguments: arguments,
              input: input
            )
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
    arguments: [String],
    input: Data?
  ) throws -> Data {
    let process = Process()
    let stdout = Pipe()
    let stderr = Pipe()
    let stdin = Pipe()
    process.executableURL = command.executableURL
    process.arguments = command.argumentsPrefix + arguments
    var environment = ProcessInfo.processInfo.environment
    environment["OPENCHRONICLE_ROOT"] = root.path
    environment["NO_COLOR"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    process.environment = environment
    process.standardOutput = stdout
    process.standardError = stderr
    if input != nil {
      process.standardInput = stdin
    }

    try process.run()
    if let input {
      stdin.fileHandleForWriting.write(input)
      try stdin.fileHandleForWriting.close()
    }
    process.waitUntilExit()

    let outputData = stdout.fileHandleForReading.readDataToEndOfFile()
    let errorData = stderr.fileHandleForReading.readDataToEndOfFile()
    guard process.terminationStatus == 0 else {
      if let response = try? JSONDecoder().decode(
        ConfigurationMutationResult.self,
        from: outputData
      ), let message = response.error {
        throw ConfigurationControllerError.commandFailed(message)
      }
      let message = concise(errorData).isEmpty ? concise(outputData) : concise(errorData)
      throw ConfigurationControllerError.commandFailed(
        message.isEmpty ? "Configuration command failed." : message
      )
    }
    return outputData
  }

  private func concise(_ data: Data) -> String {
    guard let text = String(data: data, encoding: .utf8) else { return "non-text output" }
    let compact = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return compact.count > 500 ? String(compact.prefix(500)) + "…" : compact
  }
}

@MainActor
final class ConfigurationController: ObservableObject {
  @Published private(set) var snapshot: ConfigurationSnapshot?
  @Published private(set) var activeSnapshot: ConfigurationSnapshot?
  @Published var draft: ConfigurationDraft?
  @Published private(set) var privacySnapshot: PrivacyConfigurationSnapshot?
  @Published var privacyDraft: PrivacyConfigurationDraft?
  @Published var rawText = ""
  @Published private(set) var isLoading = false
  @Published private(set) var isLoadingPrivacy = false
  @Published private(set) var isSaving = false
  @Published private(set) var isValidating = false
  @Published private(set) var lastError: String?
  @Published private(set) var statusMessage: String?

  private let paths: RuntimePaths
  private let locator: BackendLocator
  private let runner: ConfigurationCommandRunner
  private var originalDraft: ConfigurationDraft?
  private var originalPrivacyDraft: PrivacyConfigurationDraft?
  private var savedRawText = ""
  private var observedBackendPID: Int32?

  init(
    paths: RuntimePaths = .live(),
    locator: BackendLocator = BackendLocator(),
    runner: ConfigurationCommandRunner = ConfigurationCommandRunner()
  ) {
    self.paths = paths
    self.locator = locator
    self.runner = runner
  }

  var hasCommonChanges: Bool {
    guard let draft, let originalDraft else { return false }
    return draft != originalDraft
  }

  var hasRawChanges: Bool {
    rawText != savedRawText
  }

  var hasPrivacyChanges: Bool {
    guard let privacyDraft, let originalPrivacyDraft else { return false }
    return privacyDraft != originalPrivacyDraft
  }

  var hasFormChanges: Bool {
    hasCommonChanges || hasPrivacyChanges
  }

  var privacyValidationError: String? {
    privacyDraft?.validationError
  }

  var isBusy: Bool {
    isLoading || isLoadingPrivacy || isSaving || isValidating
  }

  @discardableResult
  func observeBackendPID(_ pid: Int32?) -> Bool {
    guard pid != observedBackendPID else { return false }
    observedBackendPID = pid
    guard pid != nil, let snapshot, snapshot.valid else { return false }
    activeSnapshot = snapshot
    return true
  }

  func updateDraft<Value>(
    _ keyPath: WritableKeyPath<ConfigurationDraft, Value>,
    value: Value
  ) {
    guard var current = draft else { return }
    current[keyPath: keyPath] = value
    draft = current
    lastError = nil
    statusMessage = nil
  }

  func updatePrivacyDraft<Value>(
    _ keyPath: WritableKeyPath<PrivacyConfigurationDraft, Value>,
    value: Value
  ) {
    guard var current = privacyDraft else { return }
    current[keyPath: keyPath] = value
    privacyDraft = current
    lastError = nil
    statusMessage = nil
  }

  func load() async {
    await load(resetMessage: true)
  }

  func reloadDiscardingChanges() async {
    await load(resetMessage: true)
  }

  func loadPrivacy() async {
    guard !isBusy else { return }
    guard let snapshot else {
      lastError = "Load the configuration before opening Privacy Denylists."
      return
    }
    guard let command = locator.locate() else {
      lastError = ConfigurationControllerError.backendNotFound.localizedDescription
      return
    }

    isLoadingPrivacy = true
    lastError = nil
    statusMessage = nil
    defer { isLoadingPrivacy = false }
    do {
      let fetched = try await runner.privacySnapshot(command: command, root: paths.root)
      try installPrivacySnapshot(fetched, matching: snapshot)
    } catch {
      lastError = error.localizedDescription
    }
  }

  @discardableResult
  func saveCommon() async -> Bool {
    guard !isBusy else { return false }
    guard !hasRawChanges else {
      lastError = "Reload or save the Advanced TOML draft before saving form settings."
      return false
    }
    guard let snapshot, let draft, let originalDraft else {
      lastError = "Load a valid configuration before saving form settings."
      return false
    }
    if let validationError = privacyValidationError {
      lastError = validationError
      return false
    }
    var updates = draft.updates(comparedTo: originalDraft)
    if let privacyDraft, let originalPrivacyDraft {
      updates.merge(privacyDraft.updates(comparedTo: originalPrivacyDraft)) {
        _, new in new
      }
    }
    guard !updates.isEmpty else {
      statusMessage = "No form setting changes to save."
      return false
    }
    return await save(
      action: "--patch-json",
      request: [
        "expected_sha256": snapshot.sha256,
        "updates": updates,
      ]
    )
  }

  @discardableResult
  func saveRaw() async -> Bool {
    guard !isBusy else { return false }
    guard !hasFormChanges else {
      lastError = "Save or reload form setting changes before replacing the Advanced TOML."
      return false
    }
    guard let snapshot else {
      lastError = "Load the configuration before saving."
      return false
    }
    guard hasRawChanges else {
      statusMessage = "No Advanced TOML changes to save."
      return false
    }
    return await save(
      action: "--write-json",
      request: [
        "expected_sha256": snapshot.sha256,
        "content": rawText,
      ]
    )
  }

  func validateRaw() async {
    guard !isBusy else { return }
    guard let command = locator.locate() else {
      lastError = ConfigurationControllerError.backendNotFound.localizedDescription
      return
    }
    isValidating = true
    lastError = nil
    statusMessage = nil
    defer { isValidating = false }
    do {
      let response = try await runner.mutate(
        command: command,
        root: paths.root,
        action: "--validate-json",
        request: ["content": rawText]
      )
      if response.ok {
        statusMessage = "Configuration is valid."
      } else {
        lastError = response.error ?? "Configuration validation failed."
      }
    } catch {
      lastError = error.localizedDescription
    }
  }

  func revealConfig() {
    let file = snapshot.map { URL(fileURLWithPath: $0.path) } ?? paths.configFile
    NSWorkspace.shared.activateFileViewerSelecting([file])
  }

  private func save(action: String, request: [String: Any]) async -> Bool {
    guard let command = locator.locate() else {
      lastError = ConfigurationControllerError.backendNotFound.localizedDescription
      return false
    }
    isSaving = true
    lastError = nil
    statusMessage = nil
    defer { isSaving = false }
    do {
      let response = try await runner.mutate(
        command: command,
        root: paths.root,
        action: action,
        request: request
      )
      guard response.ok else {
        lastError = response.error ?? "Configuration save failed."
        return false
      }
      let changed = response.changed == true
      let message: String
      if changed, let backup = response.backup {
        message = "Saved. Backup: \(URL(fileURLWithPath: backup).lastPathComponent)"
      } else if changed {
        message = "Saved."
      } else {
        message = "No file changes were needed."
      }
      await load(resetMessage: false)
      statusMessage = message
      return changed
    } catch {
      lastError = error.localizedDescription
      return false
    }
  }

  private func load(resetMessage: Bool) async {
    guard !isLoading else { return }
    guard let command = locator.locate() else {
      lastError = ConfigurationControllerError.backendNotFound.localizedDescription
      return
    }
    isLoading = true
    lastError = nil
    if resetMessage {
      statusMessage = nil
    }
    let reloadPrivacy = privacySnapshot != nil || privacyDraft != nil
    defer { isLoading = false }
    do {
      let fetched = try await runner.snapshot(command: command, root: paths.root)
      let configURL = URL(fileURLWithPath: fetched.path)
      let content = try String(contentsOf: configURL, encoding: .utf8)
      snapshot = fetched
      if activeSnapshot == nil, fetched.valid {
        activeSnapshot = fetched
      }
      rawText = content
      savedRawText = content
      privacySnapshot = nil
      privacyDraft = nil
      originalPrivacyDraft = nil
      if fetched.valid, let newDraft = ConfigurationDraft(snapshot: fetched) {
        draft = newDraft
        originalDraft = newDraft
        if reloadPrivacy {
          let fetchedPrivacy = try await runner.privacySnapshot(
            command: command,
            root: paths.root
          )
          try installPrivacySnapshot(fetchedPrivacy, matching: fetched)
        }
      } else {
        draft = nil
        originalDraft = nil
        lastError = fetched.error ?? "Configuration is invalid. Use Advanced TOML to repair it."
      }
    } catch {
      if let content = try? String(contentsOf: paths.configFile, encoding: .utf8) {
        rawText = content
        savedRawText = content
      }
      lastError = error.localizedDescription
    }
  }

  private func installPrivacySnapshot(
    _ fetched: PrivacyConfigurationSnapshot,
    matching snapshot: ConfigurationSnapshot
  ) throws {
    guard fetched.path == snapshot.path, fetched.sha256 == snapshot.sha256 else {
      throw ConfigurationControllerError.invalidRequest(
        "config.toml changed while Privacy Denylists were loading. Reload Settings and try again."
      )
    }
    guard fetched.valid, let newDraft = PrivacyConfigurationDraft(snapshot: fetched) else {
      throw ConfigurationControllerError.invalidRequest(
        fetched.error ?? "Privacy Denylists could not be loaded from this configuration."
      )
    }
    privacySnapshot = fetched
    privacyDraft = newDraft
    originalPrivacyDraft = newDraft
  }
}
