import Foundation

enum ProtectionDiagnosticState: String, Codable, Equatable {
  case inactive
  case protected
  case paused
  case failed
}

enum ProtectionReasonDiagnosticCode: String, Codable, Equatable {
  case appRule = "app_rule"
  case bundleRule = "bundle_rule"
  case windowTitleRule = "window_title_rule"
  case windowTitleUnknown = "window_title_unknown"
  case modeAllInherited = "mode_all_inherited"
  case diagnosticsReveal = "diagnostics_reveal"
  case diagnosticsGuardInvalid = "diagnostics_guard_invalid"
  case manualPause = "manual_pause"
  case timedPause = "timed_pause"
  case timedPauseWaiting = "timed_pause_waiting"
  case pauseStateUnavailable = "pause_state_unavailable"
  case inventoryUnavailable = "inventory_unavailable"
  case helperExit = "helper_exit"
  case helperParse = "helper_parse"
  case emptyDisplays = "empty_displays"
  case invalidDisplayInventory = "invalid_display_inventory"
  case multipleActiveWindows = "multiple_active_windows"
  case activeWindowUnmapped = "active_window_unmapped"
  case sensitiveWindowUnmapped = "sensitive_window_unmapped"
  case indicatorUnconfirmed = "indicator_unconfirmed"
  case unknown
}

struct ProtectionReasonDiagnostic: Codable, Equatable {
  let code: ProtectionReasonDiagnosticCode
  let displayID: Int?
  let sourceDisplayID: Int?
  let appName: String?
  let bundleID: String?
  let windowTitle: String?
  let rule: String?
  let effectiveResumeAt: Date?

  init(
    code: ProtectionReasonDiagnosticCode,
    displayID: Int?,
    sourceDisplayID: Int? = nil,
    appName: String? = nil,
    bundleID: String? = nil,
    windowTitle: String? = nil,
    rule: String? = nil,
    effectiveResumeAt: Date? = nil
  ) {
    self.code = code
    self.displayID = displayID
    self.sourceDisplayID = sourceDisplayID
    self.appName = appName
    self.bundleID = bundleID
    self.windowTitle = windowTitle
    self.rule = rule
    self.effectiveResumeAt = effectiveResumeAt
  }

  enum CodingKeys: String, CodingKey {
    case code
    case displayID = "display_id"
    case sourceDisplayID = "source_display_id"
    case appName = "app_name"
    case bundleID = "bundle_id"
    case windowTitle = "window_title"
    case rule
    case effectiveResumeAt = "effective_resume_at"
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    let rawCode = try container.decode(String.self, forKey: .code)
    let decodedCode = ProtectionReasonDiagnosticCode(rawValue: rawCode) ?? .unknown
    code = decodedCode
    displayID = try container.decodeIfPresent(Int.self, forKey: .displayID)

    guard decodedCode != .unknown else {
      sourceDisplayID = nil
      appName = nil
      bundleID = nil
      windowTitle = nil
      rule = nil
      effectiveResumeAt = nil
      return
    }

    sourceDisplayID = try container.decodeIfPresent(Int.self, forKey: .sourceDisplayID)
    appName = try container.decodeIfPresent(String.self, forKey: .appName)
    bundleID = try container.decodeIfPresent(String.self, forKey: .bundleID)
    windowTitle = try container.decodeIfPresent(String.self, forKey: .windowTitle)
    rule = try container.decodeIfPresent(String.self, forKey: .rule)
    if let value = try container.decodeIfPresent(String.self, forKey: .effectiveResumeAt) {
      effectiveResumeAt = try ProtectionDiagnosticsDateCodec.decode(value, codingPath: decoder.codingPath)
    } else {
      effectiveResumeAt = nil
    }
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(code.rawValue, forKey: .code)
    try container.encodeIfPresent(displayID, forKey: .displayID)
    guard code != .unknown else { return }
    try container.encodeIfPresent(sourceDisplayID, forKey: .sourceDisplayID)
    try container.encodeIfPresent(appName, forKey: .appName)
    try container.encodeIfPresent(bundleID, forKey: .bundleID)
    try container.encodeIfPresent(windowTitle, forKey: .windowTitle)
    try container.encodeIfPresent(rule, forKey: .rule)
    if let effectiveResumeAt {
      try container.encode(
        ProtectionDiagnosticsDateCodec.encode(effectiveResumeAt),
        forKey: .effectiveResumeAt
      )
    }
  }

  func categoryOnly() -> ProtectionReasonDiagnostic {
    ProtectionReasonDiagnostic(code: code, displayID: displayID)
  }

  func sanitizedForPublication() -> ProtectionReasonDiagnostic {
    ProtectionReasonDiagnostic(
      code: code,
      displayID: displayID,
      sourceDisplayID: sourceDisplayID,
      appName: appName.map(sanitizeProtectionDiagnosticValue),
      bundleID: bundleID.map(sanitizeProtectionDiagnosticValue),
      windowTitle: windowTitle.map(sanitizeProtectionDiagnosticValue),
      rule: rule.map(sanitizeProtectionDiagnosticValue),
      effectiveResumeAt: effectiveResumeAt
    )
  }
}

struct ProtectionDisplayDiagnostic: Codable, Equatable {
  let id: Int
  let primary: Bool
  let state: ProtectionDiagnosticState
  let screenshotBlocked: Bool
  let axBlocked: Bool
  let indicatorConfirmed: Bool
  let reasons: [ProtectionReasonDiagnostic]
  let generation: Int
  let updatedAt: Date

  init(
    id: Int,
    primary: Bool,
    state: ProtectionDiagnosticState,
    screenshotBlocked: Bool,
    axBlocked: Bool,
    indicatorConfirmed: Bool,
    reasons: [ProtectionReasonDiagnostic],
    generation: Int,
    updatedAt: Date
  ) {
    self.id = id
    self.primary = primary
    self.state = state
    self.screenshotBlocked = screenshotBlocked
    self.axBlocked = axBlocked
    self.indicatorConfirmed = indicatorConfirmed
    self.reasons = reasons
    self.generation = generation
    self.updatedAt = updatedAt
  }

  enum CodingKeys: String, CodingKey {
    case id
    case primary
    case state
    case screenshotBlocked = "screenshot_blocked"
    case axBlocked = "ax_blocked"
    case indicatorConfirmed = "indicator_confirmed"
    case reasons
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    id = try container.decode(Int.self, forKey: .id)
    primary = try container.decode(Bool.self, forKey: .primary)
    state = try container.decode(ProtectionDiagnosticState.self, forKey: .state)
    screenshotBlocked = try container.decode(Bool.self, forKey: .screenshotBlocked)
    axBlocked = try container.decode(Bool.self, forKey: .axBlocked)
    indicatorConfirmed = try container.decode(Bool.self, forKey: .indicatorConfirmed)
    reasons = try container.decode([ProtectionReasonDiagnostic].self, forKey: .reasons)
    generation = 0
    updatedAt = .distantPast
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(id, forKey: .id)
    try container.encode(primary, forKey: .primary)
    try container.encode(state, forKey: .state)
    try container.encode(screenshotBlocked, forKey: .screenshotBlocked)
    try container.encode(axBlocked, forKey: .axBlocked)
    try container.encode(indicatorConfirmed, forKey: .indicatorConfirmed)
    try container.encode(reasons, forKey: .reasons)
  }

  func withSnapshotContext(generation: Int, updatedAt: Date) -> ProtectionDisplayDiagnostic {
    ProtectionDisplayDiagnostic(
      id: id,
      primary: primary,
      state: state,
      screenshotBlocked: screenshotBlocked,
      axBlocked: axBlocked,
      indicatorConfirmed: indicatorConfirmed,
      reasons: reasons,
      generation: generation,
      updatedAt: updatedAt
    )
  }

  func categoryOnly() -> ProtectionDisplayDiagnostic {
    ProtectionDisplayDiagnostic(
      id: id,
      primary: primary,
      state: state,
      screenshotBlocked: screenshotBlocked,
      axBlocked: axBlocked,
      indicatorConfirmed: indicatorConfirmed,
      reasons: reasons.map { $0.categoryOnly() },
      generation: generation,
      updatedAt: updatedAt
    )
  }

  func sanitizedForPublication() -> ProtectionDisplayDiagnostic {
    ProtectionDisplayDiagnostic(
      id: id,
      primary: primary,
      state: state,
      screenshotBlocked: screenshotBlocked,
      axBlocked: axBlocked,
      indicatorConfirmed: indicatorConfirmed,
      reasons: reasons.map { $0.sanitizedForPublication() },
      generation: generation,
      updatedAt: updatedAt
    )
  }
}

struct ProtectionDiagnosticsSnapshot: Codable, Equatable {
  let generation: Int
  let state: ProtectionDiagnosticState
  let indicatorConfirmed: Bool
  let diagnosticsGuardActive: Bool
  let createdAt: Date
  let reasons: [ProtectionReasonDiagnostic]
  let displays: [ProtectionDisplayDiagnostic]

  init(
    generation: Int,
    state: ProtectionDiagnosticState,
    indicatorConfirmed: Bool,
    diagnosticsGuardActive: Bool,
    createdAt: Date,
    reasons: [ProtectionReasonDiagnostic],
    displays: [ProtectionDisplayDiagnostic]
  ) {
    self.generation = generation
    self.state = state
    self.indicatorConfirmed = indicatorConfirmed
    self.diagnosticsGuardActive = diagnosticsGuardActive
    self.createdAt = createdAt
    self.reasons = reasons
    self.displays = displays.map {
      $0.withSnapshotContext(generation: generation, updatedAt: createdAt)
    }
  }

  enum CodingKeys: String, CodingKey {
    case generation
    case state
    case indicatorConfirmed = "indicator_confirmed"
    case diagnosticsGuardActive = "diagnostics_guard_active"
    case createdAt = "created_at"
    case reasons
    case displays
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    let decodedGeneration = try container.decode(Int.self, forKey: .generation)
    guard decodedGeneration > 0 else {
      throw DecodingError.dataCorruptedError(
        forKey: .generation,
        in: container,
        debugDescription: "invalid_generation"
      )
    }
    let decodedState = try container.decode(ProtectionDiagnosticState.self, forKey: .state)
    let decodedIndicatorConfirmed = try container.decode(Bool.self, forKey: .indicatorConfirmed)
    let decodedDiagnosticsGuardActive = try container.decode(
      Bool.self,
      forKey: .diagnosticsGuardActive
    )
    let createdAtValue = try container.decode(String.self, forKey: .createdAt)
    let decodedCreatedAt = try ProtectionDiagnosticsDateCodec.decode(
      createdAtValue,
      codingPath: decoder.codingPath + [CodingKeys.createdAt]
    )
    let decodedReasons = try container.decode(
      [ProtectionReasonDiagnostic].self,
      forKey: .reasons
    )
    let wireDisplays = try container.decode([ProtectionDisplayDiagnostic].self, forKey: .displays)
    generation = decodedGeneration
    state = decodedState
    indicatorConfirmed = decodedIndicatorConfirmed
    diagnosticsGuardActive = decodedDiagnosticsGuardActive
    createdAt = decodedCreatedAt
    reasons = decodedReasons
    displays = wireDisplays.map {
      $0.withSnapshotContext(generation: decodedGeneration, updatedAt: decodedCreatedAt)
    }
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(generation, forKey: .generation)
    try container.encode(state, forKey: .state)
    try container.encode(indicatorConfirmed, forKey: .indicatorConfirmed)
    try container.encode(diagnosticsGuardActive, forKey: .diagnosticsGuardActive)
    try container.encode(ProtectionDiagnosticsDateCodec.encode(createdAt), forKey: .createdAt)
    try container.encode(reasons, forKey: .reasons)
    try container.encode(displays, forKey: .displays)
  }

  func categoryOnly() -> ProtectionDiagnosticsSnapshot {
    ProtectionDiagnosticsSnapshot(
      generation: generation,
      state: state,
      indicatorConfirmed: indicatorConfirmed,
      diagnosticsGuardActive: diagnosticsGuardActive,
      createdAt: createdAt,
      reasons: reasons.map { $0.categoryOnly() },
      displays: displays.map { $0.categoryOnly() }
    )
  }

  func sanitizedForPublication() -> ProtectionDiagnosticsSnapshot {
    ProtectionDiagnosticsSnapshot(
      generation: generation,
      state: state,
      indicatorConfirmed: indicatorConfirmed,
      diagnosticsGuardActive: diagnosticsGuardActive,
      createdAt: createdAt,
      reasons: reasons.map { $0.sanitizedForPublication() },
      displays: displays.map { $0.sanitizedForPublication() }
    )
  }
}

enum ProtectionDiagnosticsWireMessage: Codable, Equatable {
  case snapshot(ProtectionDiagnosticsSnapshot)
  case lease(
    leaseID: String,
    displayID: Int?,
    protectedGeneration: Int?,
    released: Bool
  )
  case error(code: String)

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case type
    case leaseID = "lease_id"
    case displayID = "display_id"
    case protectedGeneration = "protected_generation"
    case released
    case code
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    let schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
    guard schemaVersion == 1 else {
      throw DecodingError.dataCorruptedError(
        forKey: .schemaVersion,
        in: container,
        debugDescription: "unsupported_schema"
      )
    }
    switch try container.decode(String.self, forKey: .type) {
    case "snapshot":
      self = .snapshot(try ProtectionDiagnosticsSnapshot(from: decoder))
    case "lease":
      let leaseID = try container.decode(String.self, forKey: .leaseID)
      guard !leaseID.isEmpty else {
        throw DecodingError.dataCorruptedError(
          forKey: .leaseID,
          in: container,
          debugDescription: "invalid_lease"
        )
      }
      let released = try container.decodeIfPresent(Bool.self, forKey: .released) ?? false
      let displayID = try container.decodeIfPresent(Int.self, forKey: .displayID)
      let protectedGeneration = try container.decodeIfPresent(
        Int.self,
        forKey: .protectedGeneration
      )
      if released {
        guard displayID == nil, protectedGeneration == nil else {
          throw DecodingError.dataCorruptedError(
            forKey: .released,
            in: container,
            debugDescription: "invalid_release"
          )
        }
      } else if displayID == nil || protectedGeneration == nil {
        throw DecodingError.dataCorruptedError(
          forKey: .protectedGeneration,
          in: container,
          debugDescription: "invalid_lease"
        )
      }
      self = .lease(
        leaseID: leaseID,
        displayID: displayID,
        protectedGeneration: protectedGeneration,
        released: released
      )
    case "error":
      let code = try container.decode(String.self, forKey: .code)
      guard !code.isEmpty else {
        throw DecodingError.dataCorruptedError(
          forKey: .code,
          in: container,
          debugDescription: "invalid_error"
        )
      }
      self = .error(code: code)
    default:
      throw DecodingError.dataCorruptedError(
        forKey: .type,
        in: container,
        debugDescription: "unknown_type"
      )
    }
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.container(keyedBy: CodingKeys.self)
    try container.encode(1, forKey: .schemaVersion)
    switch self {
    case .snapshot(let snapshot):
      try container.encode("snapshot", forKey: .type)
      try snapshot.encode(to: encoder)
    case .lease(let leaseID, let displayID, let protectedGeneration, let released):
      try container.encode("lease", forKey: .type)
      try container.encode(leaseID, forKey: .leaseID)
      if released {
        try container.encode(true, forKey: .released)
      } else {
        try container.encodeIfPresent(displayID, forKey: .displayID)
        try container.encodeIfPresent(protectedGeneration, forKey: .protectedGeneration)
      }
    case .error(let code):
      try container.encode("error", forKey: .type)
      try container.encode(code, forKey: .code)
    }
  }
}

private enum ProtectionDiagnosticsDateCodec {
  static func decode(_ value: String, codingPath: [CodingKey]) throws -> Date {
    let formats: [ISO8601DateFormatter.Options] = [
      [.withInternetDateTime, .withFractionalSeconds],
      [.withInternetDateTime],
    ]
    for options in formats {
      let formatter = ISO8601DateFormatter()
      formatter.formatOptions = options
      if let date = formatter.date(from: value) {
        return date
      }
    }
    throw DecodingError.dataCorrupted(
      DecodingError.Context(codingPath: codingPath, debugDescription: "invalid_rfc3339")
    )
  }

  static func encode(_ value: Date) -> String {
    let formatter = ISO8601DateFormatter()
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: value)
  }
}

private func sanitizeProtectionDiagnosticValue(_ value: String) -> String {
  let cleanedScalars = value.unicodeScalars.map { scalar -> String in
    switch scalar.properties.generalCategory {
    case .control, .format, .surrogate, .privateUse, .unassigned:
      return " "
    default:
      return String(scalar)
    }
  }
  let cleaned = cleanedScalars.joined()
  guard cleaned.count > 160 else { return cleaned }
  return String(cleaned.prefix(159)) + "…"
}
