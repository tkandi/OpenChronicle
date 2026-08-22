import SwiftUI

enum SettingsPage: Hashable {
  case models
  case capture
  case processing
  case mcp
  case advanced
}

enum ScreenshotPrivacyModeOption: String, CaseIterable, Identifiable {
  case off
  case skipMonitor = "skip-monitor"
  case maskWindow = "mask-window"
  case excludeWindow = "exclude-window"

  var id: String { rawValue }

  var title: String {
    switch self {
    case .off:
      "Off"
    case .skipMonitor:
      "Skip matching monitors"
    case .maskWindow:
      "Mask matching windows"
    case .excludeWindow:
      "Exclude matching windows"
    }
  }
}

struct PrivacyReasonPickerAvailability: Equatable {
  let isDisplayEnabled = true
  let isDetailEnabled = true
  let isTriggerEnabled: Bool

  init(
    display: PrivacyReasonDisplayOption,
    indicatorStyle: PrivacyIndicatorStyleOption
  ) {
    isTriggerEnabled = display != .diagnostics && indicatorStyle != .off
  }
}

struct SettingsView: View {
  @ObservedObject var backend: BackendController
  @ObservedObject var configuration: ConfigurationController
  let page: SettingsPage

  @State private var showAdvancedEditor = false
  @State private var showPrivacyEditor = false
  @State private var confirmReload = false

  var body: some View {
    VStack(spacing: 0) {
      pageContent
      Divider()
      statusArea
      Divider()
      actionBar
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .task {
      if configuration.snapshot == nil && !configuration.isLoading {
        await configuration.load()
      }
    }
    .alert("Discard unsaved changes?", isPresented: $confirmReload) {
      Button("Cancel", role: .cancel) {}
      Button("Discard and Reload", role: .destructive) {
        Task { await configuration.reloadDiscardingChanges() }
      }
    } message: {
      Text("OpenChronicle will reload config.toml from disk.")
    }
  }

  @ViewBuilder
  private var pageContent: some View {
    switch page {
    case .models:
      modelsTab
    case .capture:
      captureTab
    case .processing:
      processingTab
    case .mcp:
      mcpTab
    case .advanced:
      advancedTab
    }
  }

  private var modelsTab: some View {
    Group {
      if configuration.draft != nil {
        Form {
          Section("Default Provider") {
            TextField(
              "Default model",
              text: binding(\.defaultModel, fallback: "")
            )
            TextField(
              "Base URL (optional)",
              text: binding(\.defaultBaseURL, fallback: "")
            )
            TextField(
              "API key environment variable",
              text: binding(\.defaultAPIKeyEnvironment, fallback: "OPENAI_API_KEY")
            )
            Text(
              "This common form stores only the environment variable name. It never displays or writes a direct API key."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
          }

          Section("Stage Model Overrides") {
            StageModelRow(
              title: "Timeline",
              inheritedModel: configuration.draft?.defaultModel ?? "",
              override: binding(\.timelineModelOverride, fallback: nil)
            )
            StageModelRow(
              title: "Reducer",
              inheritedModel: configuration.draft?.defaultModel ?? "",
              override: binding(\.reducerModelOverride, fallback: nil)
            )
            StageModelRow(
              title: "Classifier",
              inheritedModel: configuration.draft?.defaultModel ?? "",
              override: binding(\.classifierModelOverride, fallback: nil)
            )
            StageModelRow(
              title: "Compact",
              inheritedModel: configuration.draft?.defaultModel ?? "",
              override: binding(\.compactModelOverride, fallback: nil)
            )
            Text("Per-stage base URLs and API-key environments remain available in Advanced TOML.")
              .font(.caption)
              .foregroundStyle(.secondary)
          }

          if configuration.snapshot?.containsDirectAPIKeys == true {
            Section("Security Notice") {
              Label(
                "A direct api_key exists in config.toml. The common form will not display or modify it; direct keys override api_key_env.",
                systemImage: "exclamationmark.shield"
              )
              .foregroundStyle(.orange)
            }
          }
        }
        .formStyle(.grouped)
      } else {
        invalidConfigurationPlaceholder
      }
    }
  }

  private var captureTab: some View {
    Group {
      if configuration.draft != nil {
        Form {
          Section("Capture Timing") {
            Toggle("Event-driven capture", isOn: binding(\.eventDriven, fallback: true))
            integerField(
              "Heartbeat minutes (0 disables)",
              \.heartbeatMinutes,
              fallback: 10
            )
          }

          Section("Screenshots") {
            Toggle("Include screenshots", isOn: binding(\.includeScreenshot, fallback: true))
            Picker("Monitor mode", selection: binding(\.screenshotMonitor, fallback: "primary")) {
              Text("Primary monitor").tag("primary")
              Text("All monitors combined").tag("all")
              Text("Separate monitors").tag("separate")
            }
            Picker(
              "Privacy mode",
              selection: binding(\.screenshotPrivacyMode, fallback: "skip-monitor")
            ) {
              ForEach(ScreenshotPrivacyModeOption.allCases) { option in
                Text(option.title).tag(option.rawValue)
              }
            }
            Toggle(
              "Fail closed when visible windows cannot be checked",
              isOn: binding(\.screenshotPrivacyFailClosed, fallback: true)
            )
            PrivacyIndicatorStylePicker(
              selection: binding(\.privacyIndicatorStyle, fallback: "pill")
            )
            Picker(
              "Reason location",
              selection: binding(\.privacyReasonDisplay, fallback: "hybrid")
            ) {
              ForEach(PrivacyReasonDisplayOption.allCases) { option in
                Label(option.title, systemImage: option.systemImage)
                  .tag(option.rawValue)
              }
            }
            .pickerStyle(.menu)
            .disabled(!privacyReasonPickerAvailability.isDisplayEnabled)
            Picker(
              "Detail",
              selection: binding(\.privacyReasonDetail, fallback: "exact")
            ) {
              ForEach(PrivacyReasonDetailOption.allCases) { option in
                Label(option.title, systemImage: option.systemImage)
                  .tag(option.rawValue)
              }
            }
            .pickerStyle(.menu)
            .disabled(!privacyReasonPickerAvailability.isDetailEnabled)
            Picker(
              "Overlay reveal",
              selection: binding(\.privacyReasonTrigger, fallback: "hover")
            ) {
              ForEach(PrivacyReasonTriggerOption.allCases) { option in
                Label(option.title, systemImage: option.systemImage)
                  .tag(option.rawValue)
              }
            }
            .pickerStyle(.menu)
            .disabled(!privacyReasonPickerAvailability.isTriggerEnabled)
            integerField("JPEG quality (1–100)", \.screenshotJPEGQuality, fallback: 80)
          }

          Section("Retention") {
            integerField("Buffer retention hours", \.bufferRetentionHours, fallback: 168)
            integerField(
              "Screenshot retention hours",
              \.screenshotRetentionHours,
              fallback: 24
            )
            integerField("Buffer size limit (MB; 0 disables)", \.bufferMaxMB, fallback: 2000)
          }

          privacyDenylistsSection
        }
        .formStyle(.grouped)
      } else {
        invalidConfigurationPlaceholder
      }
    }
  }

  private var privacyReasonPickerAvailability: PrivacyReasonPickerAvailability {
    PrivacyReasonPickerAvailability(
      display: PrivacyReasonDisplayOption(
        rawValue: configuration.draft?.privacyReasonDisplay ?? ""
      ) ?? .defaultValue,
      indicatorStyle: PrivacyIndicatorStyleOption(
        rawValue: configuration.draft?.privacyIndicatorStyle ?? ""
      ) ?? .defaultStyle
    )
  }

  private var processingTab: some View {
    Group {
      if configuration.draft != nil {
        Form {
          Section("Timeline and Sessions") {
            integerField("Timeline window minutes", \.timelineWindowMinutes, fallback: 1)
            integerField("Session idle gap minutes", \.sessionGapMinutes, fallback: 5)
            integerField("Incremental flush minutes", \.sessionFlushMinutes, fallback: 5)
            Text(
              "Reducer and classifier intervals below five minutes are clamped to five at runtime."
            )
            .font(.caption)
            .foregroundStyle(.secondary)
          }
          Section("Long-term Processing") {
            Toggle("Enable session reducer", isOn: binding(\.reducerEnabled, fallback: true))
            integerField(
              "Classifier interval minutes",
              \.classifierIntervalMinutes,
              fallback: 30
            )
            integerField("Auto-dormant after days", \.autoDormantDays, fallback: 30)
            integerField("Default search result count", \.defaultTopK, fallback: 5)
          }
        }
        .formStyle(.grouped)
      } else {
        invalidConfigurationPlaceholder
      }
    }
  }

  private var mcpTab: some View {
    Group {
      if configuration.draft != nil {
        Form {
          Section("Embedded MCP Server") {
            Toggle("Start MCP server with backend", isOn: binding(\.mcpAutoStart, fallback: true))
            Picker("Transport", selection: binding(\.mcpTransport, fallback: "streamable-http")) {
              Text("Streamable HTTP").tag("streamable-http")
              Text("SSE (deprecated)").tag("sse")
              Text("stdio").tag("stdio")
            }
            TextField("Host", text: binding(\.mcpHost, fallback: "127.0.0.1"))
            integerField("Port", \.mcpPort, fallback: 8742)
          }
          Section("Network Safety") {
            Label(
              mcpSafetyText,
              systemImage: configuration.draft?.mcpHost == "127.0.0.1"
                ? "lock.shield" : "exclamationmark.triangle"
            )
            .foregroundColor(
              configuration.draft?.mcpHost == "127.0.0.1"
                ? Color.secondary : Color.orange
            )
          }
        }
        .formStyle(.grouped)
      } else {
        invalidConfigurationPlaceholder
      }
    }
  }

  private var advancedTab: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        VStack(alignment: .leading, spacing: 3) {
          Text("Advanced TOML")
            .font(.headline)
          Text(configuration.snapshot?.path ?? "~/.openchronicle/config.toml")
            .font(.caption.monospaced())
            .foregroundStyle(.secondary)
            .textSelection(.enabled)
        }
        Spacer()
        Button("Show in Finder") {
          configuration.revealConfig()
        }
      }

      if showAdvancedEditor {
        TextEditor(text: $configuration.rawText)
          .font(.system(.body, design: .monospaced))
          .border(Color.secondary.opacity(0.35))
        HStack {
          Text(
            "The full file may contain direct API keys. Keep screenshots and screen sharing in mind."
          )
          .font(.caption)
          .foregroundStyle(.orange)
          Spacer()
          Button(configuration.isValidating ? "Validating…" : "Validate") {
            Task { await configuration.validateRaw() }
          }
          .disabled(configuration.isBusy)
          Button("Hide Editor") {
            showAdvancedEditor = false
          }
        }
      } else {
        SettingsPlaceholder(
          title: "Advanced Editor Hidden",
          message:
            "This editor exposes the entire config.toml and may reveal a direct API key if one is stored there.",
          systemImage: "eye.slash",
          actionTitle: "Show Advanced TOML",
          action: { showAdvancedEditor = true }
        )
      }
    }
    .padding(16)
  }

  private var invalidConfigurationPlaceholder: some View {
    SettingsPlaceholder(
      title: "Common Settings Unavailable",
      message: "Repair and validate config.toml in the Advanced tab, then reload Settings.",
      systemImage: "exclamationmark.triangle"
    )
  }

  private var privacyDenylistsSection: some View {
    Section("Privacy Denylists") {
      if showPrivacyEditor {
        Label(
          "Rule contents may reveal private apps, sites, or text. Keep screenshots and screen sharing in mind.",
          systemImage: "eye.trianglebadge.exclamationmark"
        )
        .font(.caption)
        .foregroundStyle(.orange)

        if configuration.isLoadingPrivacy {
          HStack(spacing: 8) {
            ProgressView()
              .controlSize(.small)
            Text("Loading privacy rules…")
              .foregroundStyle(.secondary)
          }
        } else if configuration.privacyDraft != nil {
          PrivacyRuleList(
            title: "App Names",
            detail: "Exact, case-insensitive application names.",
            placeholder: "Password Manager",
            values: privacyBinding(\.denyAppNames, fallback: [])
          )
          Divider()
          PrivacyRuleList(
            title: "Bundle IDs",
            detail: "Exact, case-insensitive macOS bundle identifiers.",
            placeholder: "com.example.private",
            values: privacyBinding(\.denyBundleIDs, fallback: [])
          )
          Divider()
          PrivacyRuleList(
            title: "Window-title Patterns",
            detail: "Case-insensitive Python regular expressions matched against window titles.",
            placeholder: "(?i)private|incognito",
            values: privacyBinding(\.denyWindowTitlePatterns, fallback: [])
          )
          Divider()
          PrivacyRuleList(
            title: "URL Patterns",
            detail: "Case-insensitive Python regular expressions matched against captured URLs.",
            placeholder: "accounts\\.example\\.com",
            values: privacyBinding(\.denyURLPatterns, fallback: [])
          )
          Divider()
          PrivacyRuleList(
            title: "Text Patterns",
            detail:
              "Case-insensitive Python regular expressions matched against focused and visible text.",
            placeholder: "confidential|secret",
            values: privacyBinding(\.denyTextPatterns, fallback: [])
          )

          if let validationError = configuration.privacyValidationError {
            Label(validationError, systemImage: "exclamationmark.triangle.fill")
              .font(.caption)
              .foregroundStyle(.red)
          }

          Text(
            "Saving rewrites only a changed denylist field. Comments outside that TOML array are preserved; comments inside the array are replaced with it. Python validates regular expressions before the file is written."
          )
          .font(.caption)
          .foregroundStyle(.secondary)

          Button("Hide Rule Contents") {
            showPrivacyEditor = false
          }
        } else {
          Text("Privacy rules could not be loaded. The error appears below.")
            .font(.caption)
            .foregroundStyle(.secondary)
          Button("Retry") {
            Task { await configuration.loadPrivacy() }
          }
          .disabled(configuration.isBusy)
        }
      } else {
        privacyCountRow("App names", field: "deny_app_names")
        privacyCountRow("Bundle IDs", field: "deny_bundle_ids")
        privacyCountRow("Window-title patterns", field: "deny_window_title_patterns")
        privacyCountRow("URL patterns", field: "deny_url_patterns")
        privacyCountRow("Text patterns", field: "deny_text_patterns")
        Text(
          "Only saved rule counts are shown by default. Open the editor explicitly to load and display rule contents."
        )
        .font(.caption)
        .foregroundStyle(.secondary)
        Button("Manage Privacy Denylists…") {
          showPrivacyEditor = true
          if configuration.privacyDraft == nil {
            Task { await configuration.loadPrivacy() }
          }
        }
        .disabled(configuration.isBusy)
      }
    }
  }

  private var statusArea: some View {
    VStack(alignment: .leading, spacing: 4) {
      if let error = configuration.lastError {
        Label(error, systemImage: "xmark.circle.fill")
          .font(.caption)
          .foregroundStyle(.red)
          .textSelection(.enabled)
      } else if let validationError = configuration.privacyValidationError {
        Label(validationError, systemImage: "exclamationmark.triangle.fill")
          .font(.caption)
          .foregroundStyle(.red)
      } else if let message = configuration.statusMessage {
        Label(message, systemImage: "checkmark.circle.fill")
          .font(.caption)
          .foregroundStyle(.green)
          .textSelection(.enabled)
      } else if configuration.hasFormChanges || configuration.hasRawChanges {
        Label(unsavedChangeText, systemImage: "pencil.circle")
          .font(.caption)
          .foregroundStyle(.orange)
      } else {
        Text("Changes take effect after the backend restarts.")
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.horizontal, 16)
    .padding(.vertical, 8)
  }

  private var actionBar: some View {
    HStack {
      if configuration.isLoading {
        ProgressView()
          .controlSize(.small)
        Text("Loading configuration…")
          .foregroundStyle(.secondary)
      }
      Spacer()
      Button("Reload") {
        if configuration.hasFormChanges || configuration.hasRawChanges {
          confirmReload = true
        } else {
          Task { await configuration.reloadDiscardingChanges() }
        }
      }
      .disabled(configuration.isBusy)

      Button(configuration.isSaving ? "Saving…" : "Save") {
        save(restart: false)
      }
      .disabled(!canSave || configuration.isBusy)

      Button("Apply & Restart") {
        save(restart: true)
      }
      .keyboardShortcut(.defaultAction)
      .disabled(
        !canSave || configuration.isBusy || backend.snapshot.owner == .external
      )
    }
    .padding(12)
  }

  private var canSave: Bool {
    if page == .advanced {
      return configuration.hasRawChanges && !configuration.hasFormChanges
    }
    return configuration.hasFormChanges && !configuration.hasRawChanges
      && configuration.privacyValidationError == nil
  }

  private var unsavedChangeText: String {
    if configuration.hasFormChanges && configuration.hasRawChanges {
      return "Both form and Advanced drafts changed. Save one path after reloading the other."
    }
    if configuration.hasRawChanges {
      return "Advanced TOML has unsaved changes."
    }
    if configuration.hasPrivacyChanges && configuration.hasCommonChanges {
      return "Common settings and Privacy Denylists have unsaved changes."
    }
    return configuration.hasPrivacyChanges
      ? "Privacy Denylists have unsaved changes."
      : "Common settings have unsaved changes."
  }

  private var mcpSafetyText: String {
    if configuration.draft?.mcpHost == "127.0.0.1" {
      return "The MCP endpoint is restricted to this Mac."
    }
    return "A non-loopback host may expose OpenChronicle data to other devices."
  }

  private func save(restart: Bool) {
    Task {
      let changed =
        page == .advanced
        ? await configuration.saveRaw()
        : await configuration.saveCommon()
      if changed && restart {
        backend.restartBackend()
      }
    }
  }

  private func binding<Value>(
    _ keyPath: WritableKeyPath<ConfigurationDraft, Value>,
    fallback: Value
  ) -> Binding<Value> {
    Binding(
      get: { configuration.draft?[keyPath: keyPath] ?? fallback },
      set: { configuration.updateDraft(keyPath, value: $0) }
    )
  }

  private func privacyBinding<Value>(
    _ keyPath: WritableKeyPath<PrivacyConfigurationDraft, Value>,
    fallback: Value
  ) -> Binding<Value> {
    Binding(
      get: { configuration.privacyDraft?[keyPath: keyPath] ?? fallback },
      set: { configuration.updatePrivacyDraft(keyPath, value: $0) }
    )
  }

  private func privacyCountRow(_ title: String, field: String) -> some View {
    LabeledContent(title) {
      Text("\(configuration.snapshot?.values?.capture.privacyCount(field) ?? 0)")
        .monospacedDigit()
        .foregroundStyle(.secondary)
    }
  }

  private func integerField(
    _ title: String,
    _ keyPath: WritableKeyPath<ConfigurationDraft, Int>,
    fallback: Int
  ) -> some View {
    LabeledContent(title) {
      TextField("", value: binding(keyPath, fallback: fallback), format: .number)
        .multilineTextAlignment(.trailing)
        .frame(width: 100)
    }
  }
}

private struct PrivacyIndicatorStylePicker: View {
  @Binding var selection: String

  private let columns = [
    GridItem(.flexible(minimum: 180), spacing: 12),
    GridItem(.flexible(minimum: 180), spacing: 12),
  ]

  var body: some View {
    LabeledContent("Privacy indicator") {
      LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
        ForEach(PrivacyIndicatorStyleOption.allCases) { option in
          PrivacyIndicatorStyleButton(
            option: option,
            isSelected: selection == option.rawValue
          ) {
            selection = option.rawValue
          }
        }
      }
    }
  }
}

private struct PrivacyIndicatorStyleButton: View {
  let option: PrivacyIndicatorStyleOption
  let isSelected: Bool
  let action: () -> Void

  var body: some View {
    Button(action: action) {
      HStack(spacing: 8) {
        PrivacyIndicatorPreview(option: option)
        Text(option.title)
          .font(.subheadline)
          .multilineTextAlignment(.leading)
          .lineLimit(2)
          .frame(maxWidth: .infinity, alignment: .leading)
        Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
          .foregroundStyle(isSelected ? Color.green : Color.secondary)
          .accessibilityHidden(true)
      }
      .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
      .padding(6)
      .background(isSelected ? Color.green.opacity(0.12) : Color.clear)
      .overlay(
        RoundedRectangle(cornerRadius: 6)
          .stroke(isSelected ? Color.green : Color.secondary.opacity(0.35), lineWidth: isSelected ? 2 : 1)
      )
      .contentShape(RoundedRectangle(cornerRadius: 6))
    }
    .buttonStyle(.plain)
    .help(option.title)
    .accessibilityLabel("Privacy indicator style: \(option.title)")
    .accessibilityValue(isSelected ? "Selected" : "Not selected")
    .accessibilityAddTraits(isSelected ? .isSelected : [])
  }
}

private struct PrivacyIndicatorPreview: View {
  let option: PrivacyIndicatorStyleOption

  private var descriptor: PrivacyIndicatorPreviewDescriptor {
    option.previewDescriptor
  }

  private var overlayAlignment: Alignment {
    switch descriptor.placement {
    case .none: return .center
    case .lowerTrailing: return .bottomTrailing
    case .top: return .top
    }
  }

  var body: some View {
    ZStack(alignment: overlayAlignment) {
      RoundedRectangle(cornerRadius: 4)
        .fill(Color.black.opacity(0.78))

      switch descriptor.composition {
      case .none:
        EmptyView()
      case .borderAndBadge:
        RoundedRectangle(cornerRadius: 3)
          .stroke(Color.green.opacity(0.9), lineWidth: 1)
          .padding(1)
        RuntimePreviewBadge(text: descriptor.text)
          .padding(4)
      case .solidShield:
        RuntimePreviewBadge(text: nil)
          .padding(4)
      case .pill:
        RuntimePreviewBadge(text: descriptor.text)
          .padding(4)
      case .quietShield:
        RuntimeQuietShield()
          .padding(4)
      case .banner:
        VStack(spacing: 0) {
          RuntimePreviewBanner(text: descriptor.text ?? "")
          Spacer()
        }
        .clipShape(RoundedRectangle(cornerRadius: 4))
      }
    }
    .frame(width: 96, height: 44)
    .accessibilityHidden(true)
  }
}

private struct RuntimePreviewBadge: View {
  let text: String?

  var body: some View {
    HStack(spacing: 2) {
      Image(systemName: "checkmark.shield.fill")
        .font(.system(size: 10, weight: .medium))
      if let text {
        Text(text)
          .font(.system(size: 7, weight: .medium))
      }
    }
    .foregroundStyle(.white)
    .padding(.horizontal, text == nil ? 4 : 5)
    .frame(height: 20)
    .background(RoundedRectangle(cornerRadius: 4).fill(Color.green.opacity(0.92)))
  }
}

private struct RuntimeQuietShield: View {
  var body: some View {
    Image(systemName: "checkmark.shield.fill")
      .font(.system(size: 10, weight: .medium))
      .foregroundStyle(Color.green.opacity(0.8))
      .frame(width: 20, height: 20)
      .background(Color.green.opacity(0.18))
      .overlay(
        RoundedRectangle(cornerRadius: 4)
          .stroke(Color.green.opacity(0.8), lineWidth: 1)
      )
      .clipShape(RoundedRectangle(cornerRadius: 4))
  }
}

private struct RuntimePreviewBanner: View {
  let text: String

  var body: some View {
    HStack(spacing: 3) {
      Image(systemName: "checkmark.shield.fill")
        .font(.system(size: 8, weight: .medium))
      Text(text)
        .font(.system(size: 7, weight: .medium))
      Spacer(minLength: 0)
    }
    .foregroundStyle(.white)
    .padding(.horizontal, 5)
    .frame(maxWidth: .infinity, minHeight: 12, maxHeight: 12)
    .background(Color.green.opacity(0.92))
  }
}

private struct PrivacyRuleList: View {
  let title: String
  let detail: String
  let placeholder: String
  @Binding var values: [String]

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack {
        Text(title)
          .font(.subheadline.bold())
        Spacer()
        Text("\(values.count)")
          .font(.caption.monospacedDigit())
          .foregroundStyle(.secondary)
      }
      Text(detail)
        .font(.caption)
        .foregroundStyle(.secondary)

      if values.isEmpty {
        Text("No rules")
          .font(.caption)
          .foregroundStyle(.tertiary)
      } else {
        ForEach(values.indices, id: \.self) { index in
          HStack(spacing: 6) {
            TextField(placeholder, text: $values[index])
              .textFieldStyle(.roundedBorder)
            Button {
              values.remove(at: index)
            } label: {
              Image(systemName: "minus.circle.fill")
                .foregroundStyle(.secondary)
            }
            .buttonStyle(.borderless)
            .help("Remove rule")
            .accessibilityLabel("Remove \(title) rule \(index + 1)")
          }
        }
      }

      Button {
        values.append("")
      } label: {
        Label("Add Rule", systemImage: "plus")
      }
      .buttonStyle(.borderless)
    }
    .padding(.vertical, 3)
  }
}

private struct StageModelRow: View {
  let title: String
  let inheritedModel: String
  @Binding var override: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 6) {
      Toggle(
        "Override \(title) model",
        isOn: Binding(
          get: { override != nil },
          set: { enabled in
            override = enabled ? (override ?? inheritedModel) : nil
          }
        )
      )
      HStack {
        Text(title)
          .frame(width: 80, alignment: .leading)
        TextField("Model", text: modelBinding)
          .disabled(override == nil)
        if override == nil {
          Text("inherits \(inheritedModel)")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }
    }
  }

  private var modelBinding: Binding<String> {
    Binding(
      get: { override ?? inheritedModel },
      set: { override = $0 }
    )
  }
}

private struct SettingsPlaceholder: View {
  let title: String
  let message: String
  let systemImage: String
  var actionTitle: String?
  var action: (() -> Void)?

  init(
    title: String,
    message: String,
    systemImage: String,
    actionTitle: String? = nil,
    action: (() -> Void)? = nil
  ) {
    self.title = title
    self.message = message
    self.systemImage = systemImage
    self.actionTitle = actionTitle
    self.action = action
  }

  var body: some View {
    VStack(spacing: 12) {
      Image(systemName: systemImage)
        .font(.system(size: 34))
        .foregroundStyle(.secondary)
      Text(title)
        .font(.title3.bold())
      Text(message)
        .foregroundStyle(.secondary)
        .multilineTextAlignment(.center)
        .frame(maxWidth: 440)
      if let actionTitle, let action {
        Button(actionTitle, action: action)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .padding(24)
  }
}
