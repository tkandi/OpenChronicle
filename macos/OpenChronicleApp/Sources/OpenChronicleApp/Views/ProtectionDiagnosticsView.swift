import AppKit
import SwiftUI

extension ProtectionDisplayDiagnostic: Identifiable {}

enum ProtectionReasonDetailTruncation: Equatable {
  case middle
}

struct ProtectionReasonPresentationDescriptor: Equatable {
  static let hiddenExactValuePlaceholder = "具体值已遮盖"

  let title: String
  let systemImage: String
  let detail: String?
  let detailLineLimit = 2
  let truncation: ProtectionReasonDetailTruncation = .middle

  init(
    reason: ProtectionReasonDiagnostic,
    detail: PrivacyReasonDetailOption,
    showsExactValues: Bool
  ) {
    title = Self.title(for: reason.code)
    systemImage = Self.systemImage(for: reason.code)

    guard detail != .category else {
      self.detail = nil
      return
    }
    guard showsExactValues else {
      self.detail = Self.hiddenExactValuePlaceholder
      return
    }

    let values = [
      reason.sourceDisplayID.map { "Source display: \($0)" },
      reason.appName.map { "App: \($0)" },
      reason.bundleID.map { "Bundle: \($0)" },
      reason.windowTitle.map { "Window: \($0)" },
      reason.rule.map { "Rule: \($0)" },
      reason.effectiveResumeAt.map {
        "Resume: \($0.formatted(date: .omitted, time: .standard))"
      },
    ].compactMap { $0 }
    self.detail = values.isEmpty ? "No additional value" : values.joined(separator: " | ")
  }

  private static func title(for code: ProtectionReasonDiagnosticCode) -> String {
    switch code {
    case .appRule: return "Application rule"
    case .bundleRule: return "Bundle rule"
    case .windowTitleRule: return "Window title rule"
    case .windowTitleUnknown: return "Window title unavailable"
    case .modeAllInherited: return "All-monitor protection"
    case .diagnosticsReveal: return "Diagnostics guard"
    case .diagnosticsGuardInvalid: return "Diagnostics guard invalid"
    case .manualPause: return "Manual pause"
    case .timedPause: return "Timed pause"
    case .timedPauseWaiting: return "Timed pause awaiting recovery"
    case .pauseStateUnavailable: return "Pause state unavailable"
    case .inventoryUnavailable: return "Display inventory unavailable"
    case .helperExit: return "Window helper failed"
    case .helperParse: return "Window helper output invalid"
    case .emptyDisplays: return "No displays reported"
    case .invalidDisplayInventory: return "Display inventory invalid"
    case .multipleActiveWindows: return "Multiple active windows"
    case .activeWindowUnmapped: return "Active window unmapped"
    case .sensitiveWindowUnmapped: return "Sensitive window unmapped"
    case .indicatorUnconfirmed: return "Indicator unconfirmed"
    case .unknown: return "Unknown reason"
    }
  }

  private static func systemImage(for code: ProtectionReasonDiagnosticCode) -> String {
    switch code {
    case .appRule, .bundleRule, .windowTitleRule:
      return "checkmark.shield"
    case .windowTitleUnknown, .modeAllInherited:
      return "shield.lefthalf.filled"
    case .diagnosticsReveal:
      return "eye"
    case .manualPause, .timedPause, .timedPauseWaiting:
      return "pause.circle"
    case .diagnosticsGuardInvalid, .pauseStateUnavailable, .inventoryUnavailable,
      .helperExit, .helperParse, .emptyDisplays, .invalidDisplayInventory,
      .multipleActiveWindows, .activeWindowUnmapped, .sensitiveWindowUnmapped,
      .indicatorUnconfirmed:
      return "exclamationmark.triangle"
    case .unknown:
      return "questionmark.circle"
    }
  }
}

struct ProtectionDiagnosticsView: View {
  @ObservedObject var controller: PrivacyDiagnosticsController
  let detailOption: PrivacyReasonDetailOption

  @State private var selectedDisplayID: Int?

  var body: some View {
    VStack(spacing: 0) {
      statusBar
      Divider()
      diagnosticsTable
      Divider()
      detailPane
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .overlay(alignment: .topLeading) {
      WindowScreenObserver { displayID in
        controller.setDisplay(displayID.map(Int.init))
      }
      .frame(width: 1, height: 1)
      .allowsHitTesting(false)
      .accessibilityHidden(true)
    }
    .onAppear {
      selectAvailableDisplay(from: controller.displayDiagnostics.map(\.id))
      controller.setPageVisible(true)
    }
    .onDisappear {
      controller.setPageVisible(false)
      controller.setDisplay(nil)
    }
    .onChange(of: controller.displayDiagnostics.map(\.id)) { displayIDs in
      selectAvailableDisplay(from: displayIDs)
    }
  }

  private var statusBar: some View {
    HStack(spacing: 12) {
      Label(
        "\(controller.displayDiagnostics.count) displays",
        systemImage: "display.2"
      )
      .foregroundStyle(.secondary)

      if controller.lastErrorCode != nil {
        Label("Unavailable", systemImage: "exclamationmark.triangle.fill")
          .foregroundStyle(.orange)
      }

      Spacer()
      exactValueControls
    }
    .font(.callout)
    .padding(.horizontal, 16)
    .frame(height: 42)
  }

  @ViewBuilder
  private var exactValueControls: some View {
    if detailOption != .category {
      Label(
        controller.showsExactValues ? "具体值已显示" : "具体值已遮盖",
        systemImage: controller.showsExactValues ? "eye" : "eye.slash"
      )
      .foregroundStyle(controller.showsExactValues ? .primary : .secondary)

      if detailOption == .tiered {
        Button {
          if controller.showsExactValues {
            controller.hideExact()
          } else {
            controller.revealExact()
          }
        } label: {
          Label(
            controller.showsExactValues ? "隐藏具体值" : "显示具体值",
            systemImage: controller.showsExactValues ? "eye.slash" : "eye"
          )
        }
      }
    }
  }

  private var diagnosticsTable: some View {
    Table(controller.displayDiagnostics, selection: $selectedDisplayID) {
      TableColumn("Display") { display in
        HStack(spacing: 5) {
          Text("\(display.id)")
            .monospacedDigit()
          if display.primary {
            Image(systemName: "star.fill")
              .font(.caption2)
              .foregroundStyle(.yellow)
              .help("Primary display")
          }
        }
      }
      .width(min: 56, ideal: 72, max: 90)

      TableColumn("State") { display in
        DiagnosticStateLabel(state: display.state)
      }
      .width(min: 70, ideal: 82, max: 96)

      TableColumn("Shot") { display in
        BlockedStateIcon(
          blocked: display.screenshotBlocked,
          blockedLabel: "Screenshot blocked",
          openLabel: "Screenshot allowed"
        )
      }
      .width(min: 38, ideal: 46, max: 54)

      TableColumn("AX") { display in
        BlockedStateIcon(
          blocked: display.axBlocked,
          blockedLabel: "Accessibility capture blocked",
          openLabel: "Accessibility capture allowed"
        )
      }
      .width(min: 34, ideal: 42, max: 50)

      TableColumn("Primary reason") { display in
        let title = display.reasons.first.map {
          ProtectionReasonPresentationDescriptor(
            reason: $0,
            detail: .category,
            showsExactValues: false
          ).title
        } ?? "None"
        Text(title)
          .lineLimit(1)
          .truncationMode(.middle)
          .help(title)
      }
      .width(min: 120, ideal: 180)

      TableColumn("More") { display in
        Text(display.reasons.count > 1 ? "+\(display.reasons.count - 1)" : "-")
          .monospacedDigit()
          .foregroundStyle(display.reasons.count > 1 ? .primary : .tertiary)
      }
      .width(min: 38, ideal: 42, max: 48)

      TableColumn("Update") { display in
        VStack(alignment: .leading, spacing: 1) {
          Text("#\(display.generation)")
            .monospacedDigit()
          Text(display.updatedAt, style: .relative)
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .lineLimit(1)
      }
      .width(min: 62, ideal: 72, max: 84)

      TableColumn("Confirm") { display in
        Image(
          systemName: display.indicatorConfirmed
            ? "checkmark.seal.fill"
            : "exclamationmark.triangle.fill"
        )
        .foregroundStyle(display.indicatorConfirmed ? .green : .orange)
        .help(display.indicatorConfirmed ? "Indicator confirmed" : "Indicator unconfirmed")
        .accessibilityLabel(
          display.indicatorConfirmed ? "Indicator confirmed" : "Indicator unconfirmed"
        )
      }
      .width(min: 48, ideal: 56, max: 66)
    }
    .frame(minHeight: 220, maxHeight: .infinity)
    .overlay {
      if controller.displayDiagnostics.isEmpty {
        Text(controller.lastErrorCode == nil ? "No display diagnostics" : "Diagnostics unavailable")
          .foregroundStyle(.secondary)
      }
    }
  }

  @ViewBuilder
  private var detailPane: some View {
    if let selectedDisplay {
      VStack(alignment: .leading, spacing: 0) {
        HStack {
          Text("Display \(selectedDisplay.id) reasons")
            .font(.headline)
          Spacer()
          Text("\(selectedDisplay.reasons.count)")
            .monospacedDigit()
            .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 16)
        .frame(height: 38)

        Divider()
        if selectedDisplay.reasons.isEmpty {
          Text("No reasons reported")
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
          ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
              ForEach(Array(selectedDisplay.reasons.enumerated()), id: \.offset) {
                index,
                reason in
                if index > 0 {
                  Divider()
                }
                ProtectionReasonDetailRow(
                  descriptor: ProtectionReasonPresentationDescriptor(
                    reason: reason,
                    detail: detailOption,
                    showsExactValues: controller.showsExactValues
                  )
                )
              }
            }
            .padding(.horizontal, 16)
          }
        }
      }
      .frame(minHeight: 190, idealHeight: 230, maxHeight: 270)
    } else {
      Text("Select a display")
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, minHeight: 190, maxHeight: 270)
    }
  }

  private var selectedDisplay: ProtectionDisplayDiagnostic? {
    controller.displayDiagnostics.first { $0.id == selectedDisplayID }
  }

  private func selectAvailableDisplay(from displayIDs: [Int]) {
    if let selectedDisplayID, displayIDs.contains(selectedDisplayID) {
      return
    }
    selectedDisplayID = displayIDs.first
  }
}

private struct DiagnosticStateLabel: View {
  let state: ProtectionDiagnosticState

  var body: some View {
    Label(title, systemImage: systemImage)
      .font(.caption)
      .foregroundStyle(color)
      .lineLimit(1)
  }

  private var title: String {
    switch state {
    case .inactive: return "Inactive"
    case .protected: return "Protected"
    case .paused: return "Paused"
    case .failed: return "Failed"
    }
  }

  private var systemImage: String {
    switch state {
    case .inactive: return "circle"
    case .protected: return "checkmark.shield.fill"
    case .paused: return "pause.circle.fill"
    case .failed: return "exclamationmark.triangle.fill"
    }
  }

  private var color: Color {
    switch state {
    case .inactive: return .secondary
    case .protected: return .green
    case .paused: return .orange
    case .failed: return .red
    }
  }
}

private struct BlockedStateIcon: View {
  let blocked: Bool
  let blockedLabel: String
  let openLabel: String

  var body: some View {
    Image(systemName: blocked ? "hand.raised.fill" : "circle")
      .foregroundStyle(blocked ? .green : .secondary)
      .help(blocked ? blockedLabel : openLabel)
      .accessibilityLabel(blocked ? blockedLabel : openLabel)
  }
}

private struct ProtectionReasonDetailRow: View {
  let descriptor: ProtectionReasonPresentationDescriptor

  var body: some View {
    HStack(alignment: .top, spacing: 10) {
      Image(systemName: descriptor.systemImage)
        .foregroundStyle(.secondary)
        .frame(width: 18)
      VStack(alignment: .leading, spacing: 3) {
        Text(descriptor.title)
          .font(.callout.weight(.medium))
        if let detail = descriptor.detail {
          Text(detail)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(descriptor.detailLineLimit)
            .truncationMode(.middle)
            .help(detail)
        }
      }
      Spacer(minLength: 8)
    }
    .padding(.vertical, 7)
  }
}

private struct WindowScreenObserver: NSViewRepresentable {
  let onDisplayChange: (UInt32?) -> Void

  func makeCoordinator() -> Coordinator {
    Coordinator(onDisplayChange: onDisplayChange)
  }

  func makeNSView(context: Context) -> ScreenObserverView {
    let view = ScreenObserverView()
    view.onWindowChange = { window in
      context.coordinator.attach(to: window)
    }
    return view
  }

  func updateNSView(_ nsView: ScreenObserverView, context: Context) {
    context.coordinator.onDisplayChange = onDisplayChange
    context.coordinator.attach(to: nsView.window)
  }

  static func dismantleNSView(_ nsView: ScreenObserverView, coordinator: Coordinator) {
    nsView.onWindowChange = nil
    coordinator.detach()
  }

  final class Coordinator: NSObject {
    var onDisplayChange: (UInt32?) -> Void
    private weak var window: NSWindow?

    init(onDisplayChange: @escaping (UInt32?) -> Void) {
      self.onDisplayChange = onDisplayChange
    }

    func attach(to window: NSWindow?) {
      guard self.window !== window else { return }
      detach()
      self.window = window
      if let window {
        NotificationCenter.default.addObserver(
          self,
          selector: #selector(windowDidChangeScreen(_:)),
          name: NSWindow.didChangeScreenNotification,
          object: window
        )
      }
      publishDisplayID()
    }

    func detach() {
      if let window {
        NotificationCenter.default.removeObserver(
          self,
          name: NSWindow.didChangeScreenNotification,
          object: window
        )
      }
      window = nil
    }

    @objc private func windowDidChangeScreen(_ notification: Notification) {
      publishDisplayID()
    }

    private func publishDisplayID() {
      guard
        let number = window?.screen?.deviceDescription[
          NSDeviceDescriptionKey("NSScreenNumber")
        ] as? NSNumber,
        number.int64Value > 0,
        number.uint64Value <= UInt64(UInt32.max)
      else {
        onDisplayChange(nil)
        return
      }
      onDisplayChange(UInt32(number.uint64Value))
    }
  }
}

private final class ScreenObserverView: NSView {
  var onWindowChange: ((NSWindow?) -> Void)?

  override func viewDidMoveToWindow() {
    super.viewDidMoveToWindow()
    onWindowChange?(window)
  }
}
