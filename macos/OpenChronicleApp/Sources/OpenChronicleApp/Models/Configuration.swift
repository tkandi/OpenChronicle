import Foundation

struct ConfigurationSnapshot: Decodable, Equatable {
  let schemaVersion: Int
  let path: String
  let sha256: String
  let valid: Bool
  let error: String?
  let containsDirectAPIKeys: Bool
  let values: ConfigurationValues?

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case path
    case sha256
    case valid
    case error
    case containsDirectAPIKeys = "contains_direct_api_keys"
    case values
  }
}

struct ConfigurationValues: Decodable, Equatable {
  let models: [String: ModelConfigurationValue]
  let capture: CaptureConfigurationValue
  let timeline: TimelineConfigurationValue
  let session: SessionConfigurationValue
  let reducer: ReducerConfigurationValue
  let classifier: ClassifierConfigurationValue
  let memory: MemoryConfigurationValue
  let search: SearchConfigurationValue
  let mcp: MCPConfigurationValue
}

struct ModelConfigurationValue: Decodable, Equatable {
  let model: String
  let baseURL: String
  let apiKeyEnvironment: String
  let maxTokens: Int?
  let modelExplicit: Bool
  let usesDirectAPIKey: Bool

  enum CodingKeys: String, CodingKey {
    case model
    case baseURL = "base_url"
    case apiKeyEnvironment = "api_key_env"
    case maxTokens = "max_tokens"
    case modelExplicit = "model_explicit"
    case usesDirectAPIKey = "uses_direct_api_key"
  }
}

struct CaptureConfigurationValue: Decodable, Equatable {
  let eventDriven: Bool
  let heartbeatMinutes: Int
  let bufferRetentionHours: Int
  let screenshotRetentionHours: Int
  let bufferMaxMB: Int
  let includeScreenshot: Bool
  let screenshotMonitor: String
  let screenshotPrivacyMode: String
  let screenshotPrivacyFailClosed: Bool
  let screenshotJPEGQuality: Int
  let privacyIndicatorStyle: String?
  let privacyReasonDisplay: String?
  let privacyReasonDetail: String?
  let privacyReasonTrigger: String?
  let privacyCounts: [String: Int]?

  enum CodingKeys: String, CodingKey {
    case eventDriven = "event_driven"
    case heartbeatMinutes = "heartbeat_minutes"
    case bufferRetentionHours = "buffer_retention_hours"
    case screenshotRetentionHours = "screenshot_retention_hours"
    case bufferMaxMB = "buffer_max_mb"
    case includeScreenshot = "include_screenshot"
    case screenshotMonitor = "screenshot_monitor"
    case screenshotPrivacyMode = "screenshot_privacy_mode"
    case screenshotPrivacyFailClosed = "screenshot_privacy_fail_closed"
    case screenshotJPEGQuality = "screenshot_jpeg_quality"
    case privacyIndicatorStyle = "privacy_indicator_style"
    case privacyReasonDisplay = "privacy_reason_display"
    case privacyReasonDetail = "privacy_reason_detail"
    case privacyReasonTrigger = "privacy_reason_trigger"
    case privacyCounts = "privacy_counts"
  }

  func privacyCount(_ field: String) -> Int {
    privacyCounts?[field] ?? 0
  }
}

struct TimelineConfigurationValue: Decodable, Equatable {
  let windowMinutes: Int

  enum CodingKeys: String, CodingKey {
    case windowMinutes = "window_minutes"
  }
}

struct SessionConfigurationValue: Decodable, Equatable {
  let gapMinutes: Int
  let flushMinutes: Int

  enum CodingKeys: String, CodingKey {
    case gapMinutes = "gap_minutes"
    case flushMinutes = "flush_minutes"
  }
}

struct ReducerConfigurationValue: Decodable, Equatable {
  let enabled: Bool
}

struct ClassifierConfigurationValue: Decodable, Equatable {
  let intervalMinutes: Int

  enum CodingKeys: String, CodingKey {
    case intervalMinutes = "interval_minutes"
  }
}

struct MemoryConfigurationValue: Decodable, Equatable {
  let autoDormantDays: Int

  enum CodingKeys: String, CodingKey {
    case autoDormantDays = "auto_dormant_days"
  }
}

struct SearchConfigurationValue: Decodable, Equatable {
  let defaultTopK: Int

  enum CodingKeys: String, CodingKey {
    case defaultTopK = "default_top_k"
  }
}

struct MCPConfigurationValue: Decodable, Equatable {
  let autoStart: Bool
  let transport: String
  let host: String
  let port: Int

  enum CodingKeys: String, CodingKey {
    case autoStart = "auto_start"
    case transport
    case host
    case port
  }
}

struct ConfigurationDraft: Equatable {
  var defaultModel: String
  var defaultBaseURL: String
  var defaultAPIKeyEnvironment: String
  var timelineModelOverride: String?
  var reducerModelOverride: String?
  var classifierModelOverride: String?
  var compactModelOverride: String?

  var eventDriven: Bool
  var heartbeatMinutes: Int
  var includeScreenshot: Bool
  var screenshotMonitor: String
  var screenshotPrivacyMode: String
  var screenshotPrivacyFailClosed: Bool
  var privacyIndicatorStyle: String
  var privacyReasonDisplay: String
  var privacyReasonDetail: String
  var privacyReasonTrigger: String
  var bufferRetentionHours: Int
  var screenshotRetentionHours: Int
  var bufferMaxMB: Int
  var screenshotJPEGQuality: Int

  var timelineWindowMinutes: Int
  var sessionGapMinutes: Int
  var sessionFlushMinutes: Int
  var reducerEnabled: Bool
  var classifierIntervalMinutes: Int
  var autoDormantDays: Int
  var defaultTopK: Int

  var mcpAutoStart: Bool
  var mcpTransport: String
  var mcpHost: String
  var mcpPort: Int

  init?(snapshot: ConfigurationSnapshot) {
    guard let values = snapshot.values,
      let defaultModelValue = values.models["default"]
    else {
      return nil
    }

    defaultModel = defaultModelValue.model
    defaultBaseURL = defaultModelValue.baseURL
    defaultAPIKeyEnvironment = defaultModelValue.apiKeyEnvironment
    timelineModelOverride = Self.explicitModel("timeline", values: values)
    reducerModelOverride = Self.explicitModel("reducer", values: values)
    classifierModelOverride = Self.explicitModel("classifier", values: values)
    compactModelOverride = Self.explicitModel("compact", values: values)

    eventDriven = values.capture.eventDriven
    heartbeatMinutes = values.capture.heartbeatMinutes
    includeScreenshot = values.capture.includeScreenshot
    screenshotMonitor = values.capture.screenshotMonitor
    screenshotPrivacyMode = values.capture.screenshotPrivacyMode
    screenshotPrivacyFailClosed = values.capture.screenshotPrivacyFailClosed
    privacyIndicatorStyle = PrivacyIndicatorStyleOption(
      rawValue: values.capture.privacyIndicatorStyle ?? ""
    )?.rawValue ?? PrivacyIndicatorStyleOption.defaultStyle.rawValue
    privacyReasonDisplay = PrivacyReasonDisplayOption(
      rawValue: values.capture.privacyReasonDisplay ?? ""
    )?.rawValue ?? PrivacyReasonDisplayOption.defaultValue.rawValue
    privacyReasonDetail = PrivacyReasonDetailOption(
      rawValue: values.capture.privacyReasonDetail ?? ""
    )?.rawValue ?? PrivacyReasonDetailOption.defaultValue.rawValue
    privacyReasonTrigger = PrivacyReasonTriggerOption(
      rawValue: values.capture.privacyReasonTrigger ?? ""
    )?.rawValue ?? PrivacyReasonTriggerOption.defaultValue.rawValue
    bufferRetentionHours = values.capture.bufferRetentionHours
    screenshotRetentionHours = values.capture.screenshotRetentionHours
    bufferMaxMB = values.capture.bufferMaxMB
    screenshotJPEGQuality = values.capture.screenshotJPEGQuality

    timelineWindowMinutes = values.timeline.windowMinutes
    sessionGapMinutes = values.session.gapMinutes
    sessionFlushMinutes = values.session.flushMinutes
    reducerEnabled = values.reducer.enabled
    classifierIntervalMinutes = values.classifier.intervalMinutes
    autoDormantDays = values.memory.autoDormantDays
    defaultTopK = values.search.defaultTopK

    mcpAutoStart = values.mcp.autoStart
    mcpTransport = values.mcp.transport
    mcpHost = values.mcp.host
    mcpPort = values.mcp.port
  }

  func effectiveModel(for stage: String) -> String {
    switch stage {
    case "timeline": return timelineModelOverride ?? defaultModel
    case "reducer": return reducerModelOverride ?? defaultModel
    case "classifier": return classifierModelOverride ?? defaultModel
    case "compact": return compactModelOverride ?? defaultModel
    default: return defaultModel
    }
  }

  func updates(comparedTo original: ConfigurationDraft) -> [String: Any] {
    var updates: [String: Any] = [:]
    add(&updates, "models.default.model", defaultModel, original.defaultModel)
    add(&updates, "models.default.base_url", defaultBaseURL, original.defaultBaseURL)
    add(
      &updates,
      "models.default.api_key_env",
      defaultAPIKeyEnvironment,
      original.defaultAPIKeyEnvironment
    )
    addOptional(
      &updates,
      "models.timeline.model",
      timelineModelOverride,
      original.timelineModelOverride
    )
    addOptional(
      &updates,
      "models.reducer.model",
      reducerModelOverride,
      original.reducerModelOverride
    )
    addOptional(
      &updates,
      "models.classifier.model",
      classifierModelOverride,
      original.classifierModelOverride
    )
    addOptional(
      &updates,
      "models.compact.model",
      compactModelOverride,
      original.compactModelOverride
    )

    add(&updates, "capture.event_driven", eventDriven, original.eventDriven)
    add(
      &updates,
      "capture.heartbeat_minutes",
      heartbeatMinutes,
      original.heartbeatMinutes
    )
    add(
      &updates,
      "capture.include_screenshot",
      includeScreenshot,
      original.includeScreenshot
    )
    add(
      &updates,
      "capture.screenshot_monitor",
      screenshotMonitor,
      original.screenshotMonitor
    )
    add(
      &updates,
      "capture.screenshot_privacy_mode",
      screenshotPrivacyMode,
      original.screenshotPrivacyMode
    )
    add(
      &updates,
      "capture.screenshot_privacy_fail_closed",
      screenshotPrivacyFailClosed,
      original.screenshotPrivacyFailClosed
    )
    add(
      &updates,
      "capture.privacy_indicator_style",
      privacyIndicatorStyle,
      original.privacyIndicatorStyle
    )
    add(
      &updates,
      "capture.privacy_reason_display",
      privacyReasonDisplay,
      original.privacyReasonDisplay
    )
    add(
      &updates,
      "capture.privacy_reason_detail",
      privacyReasonDetail,
      original.privacyReasonDetail
    )
    add(
      &updates,
      "capture.privacy_reason_trigger",
      privacyReasonTrigger,
      original.privacyReasonTrigger
    )
    add(
      &updates,
      "capture.buffer_retention_hours",
      bufferRetentionHours,
      original.bufferRetentionHours
    )
    add(
      &updates,
      "capture.screenshot_retention_hours",
      screenshotRetentionHours,
      original.screenshotRetentionHours
    )
    add(&updates, "capture.buffer_max_mb", bufferMaxMB, original.bufferMaxMB)
    add(
      &updates,
      "capture.screenshot_jpeg_quality",
      screenshotJPEGQuality,
      original.screenshotJPEGQuality
    )

    add(
      &updates,
      "timeline.window_minutes",
      timelineWindowMinutes,
      original.timelineWindowMinutes
    )
    add(&updates, "session.gap_minutes", sessionGapMinutes, original.sessionGapMinutes)
    add(
      &updates,
      "session.flush_minutes",
      sessionFlushMinutes,
      original.sessionFlushMinutes
    )
    add(&updates, "reducer.enabled", reducerEnabled, original.reducerEnabled)
    add(
      &updates,
      "classifier.interval_minutes",
      classifierIntervalMinutes,
      original.classifierIntervalMinutes
    )
    add(
      &updates,
      "memory.auto_dormant_days",
      autoDormantDays,
      original.autoDormantDays
    )
    add(&updates, "search.default_top_k", defaultTopK, original.defaultTopK)

    add(&updates, "mcp.auto_start", mcpAutoStart, original.mcpAutoStart)
    add(&updates, "mcp.transport", mcpTransport, original.mcpTransport)
    add(&updates, "mcp.host", mcpHost, original.mcpHost)
    add(&updates, "mcp.port", mcpPort, original.mcpPort)
    return updates
  }

  private static func explicitModel(
    _ stage: String,
    values: ConfigurationValues
  ) -> String? {
    guard let model = values.models[stage], model.modelExplicit else { return nil }
    return model.model
  }

  private func add<T: Equatable>(
    _ updates: inout [String: Any],
    _ path: String,
    _ value: T,
    _ original: T
  ) {
    if value != original {
      updates[path] = value
    }
  }

  private func addOptional(
    _ updates: inout [String: Any],
    _ path: String,
    _ value: String?,
    _ original: String?
  ) {
    guard value != original else { return }
    updates[path] = value ?? NSNull()
  }
}

struct ConfigurationMutationResult: Decodable, Equatable {
  let ok: Bool
  let changed: Bool?
  let path: String?
  let backup: String?
  let sha256: String?
  let valid: Bool?
  let error: String?
}

struct PrivacyConfigurationSnapshot: Decodable, Equatable {
  let schemaVersion: Int
  let path: String
  let sha256: String
  let valid: Bool
  let error: String?
  let values: PrivacyConfigurationValues?

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case path
    case sha256
    case valid
    case error
    case values
  }
}

struct PrivacyConfigurationValues: Decodable, Equatable {
  let denyAppNames: [String]
  let denyBundleIDs: [String]
  let denyWindowTitlePatterns: [String]
  let denyURLPatterns: [String]
  let denyTextPatterns: [String]

  enum CodingKeys: String, CodingKey {
    case denyAppNames = "deny_app_names"
    case denyBundleIDs = "deny_bundle_ids"
    case denyWindowTitlePatterns = "deny_window_title_patterns"
    case denyURLPatterns = "deny_url_patterns"
    case denyTextPatterns = "deny_text_patterns"
  }
}

struct PrivacyConfigurationDraft: Equatable {
  var denyAppNames: [String]
  var denyBundleIDs: [String]
  var denyWindowTitlePatterns: [String]
  var denyURLPatterns: [String]
  var denyTextPatterns: [String]

  init?(snapshot: PrivacyConfigurationSnapshot) {
    guard let values = snapshot.values else { return nil }
    denyAppNames = values.denyAppNames
    denyBundleIDs = values.denyBundleIDs
    denyWindowTitlePatterns = values.denyWindowTitlePatterns
    denyURLPatterns = values.denyURLPatterns
    denyTextPatterns = values.denyTextPatterns
  }

  var validationError: String? {
    let groups: [(String, [String])] = [
      ("App names", denyAppNames),
      ("Bundle IDs", denyBundleIDs),
      ("Window-title patterns", denyWindowTitlePatterns),
      ("URL patterns", denyURLPatterns),
      ("Text patterns", denyTextPatterns),
    ]
    for (label, values) in groups where values.contains(where: Self.isBlank) {
      return "\(label) cannot contain an empty rule."
    }
    return nil
  }

  func updates(comparedTo original: PrivacyConfigurationDraft) -> [String: Any] {
    var updates: [String: Any] = [:]
    add(&updates, "capture.deny_app_names", denyAppNames, original.denyAppNames)
    add(&updates, "capture.deny_bundle_ids", denyBundleIDs, original.denyBundleIDs)
    add(
      &updates,
      "capture.deny_window_title_patterns",
      denyWindowTitlePatterns,
      original.denyWindowTitlePatterns
    )
    add(&updates, "capture.deny_url_patterns", denyURLPatterns, original.denyURLPatterns)
    add(&updates, "capture.deny_text_patterns", denyTextPatterns, original.denyTextPatterns)
    return updates
  }

  private static func isBlank(_ value: String) -> Bool {
    value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
  }

  private func add(
    _ updates: inout [String: Any],
    _ path: String,
    _ value: [String],
    _ original: [String]
  ) {
    if value != original {
      updates[path] = value
    }
  }
}
