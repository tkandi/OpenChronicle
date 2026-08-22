import AppKit
import SwiftUI

struct WindowScreenGeometry: Equatable {
  let displayID: UInt32?
  let frame: CGRect
}

enum WindowDisplaySelection {
  static func singleIntersectingDisplayID(
    windowFrame: CGRect,
    screens: [WindowScreenGeometry]
  ) -> UInt32? {
    guard isValid(frame: windowFrame), screens.allSatisfy({ isValid(frame: $0.frame) }) else {
      return nil
    }
    let intersecting = screens.filter { screen in
      let overlap = windowFrame.intersection(screen.frame)
      return !overlap.isNull && overlap.width > 0 && overlap.height > 0
    }
    guard intersecting.count == 1, let displayID = intersecting[0].displayID, displayID > 0 else {
      return nil
    }
    return displayID
  }

  private static func isValid(frame: CGRect) -> Bool {
    frame.origin.x.isFinite
      && frame.origin.y.isFinite
      && frame.width.isFinite
      && frame.height.isFinite
      && frame.width > 0
      && frame.height > 0
  }
}

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

struct ProtectionReasonPresentationSection: Equatable {
  let title: String
  let descriptors: [ProtectionReasonPresentationDescriptor]
}

struct ProtectionDiagnosticsDetailPresentation: Equatable {
  let sections: [ProtectionReasonPresentationSection]
  let emptyMessage: String?

  static func make(
    selectedDisplayID: Int?,
    displays: [ProtectionDisplayDiagnostic],
    globalReasons: [ProtectionReasonDiagnostic],
    detail: PrivacyReasonDetailOption,
    showsExactValues: Bool
  ) -> ProtectionDiagnosticsDetailPresentation {
    var sections: [ProtectionReasonPresentationSection] = []
    if !globalReasons.isEmpty {
      sections.append(
        section(
          title: "Global reasons",
          reasons: globalReasons,
          detail: detail,
          showsExactValues: showsExactValues
        )
      )
    }
    if let selectedDisplayID,
      let display = displays.first(where: { $0.id == selectedDisplayID })
    {
      sections.append(
        section(
          title: "Display \(display.id) reasons",
          reasons: display.reasons,
          detail: detail,
          showsExactValues: showsExactValues
        )
      )
    }
    return ProtectionDiagnosticsDetailPresentation(
      sections: sections,
      emptyMessage: sections.isEmpty
        ? (displays.isEmpty ? "No reasons reported" : "Select a display")
        : nil
    )
  }

  private static func section(
    title: String,
    reasons: [ProtectionReasonDiagnostic],
    detail: PrivacyReasonDetailOption,
    showsExactValues: Bool
  ) -> ProtectionReasonPresentationSection {
    ProtectionReasonPresentationSection(
      title: title,
      descriptors: reasons.map {
        ProtectionReasonPresentationDescriptor(
          reason: $0,
          detail: detail,
          showsExactValues: showsExactValues
        )
      }
    )
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
    let presentation = ProtectionDiagnosticsDetailPresentation.make(
      selectedDisplayID: selectedDisplayID,
      displays: controller.displayDiagnostics,
      globalReasons: controller.globalReasons,
      detail: detailOption,
      showsExactValues: controller.showsExactValues
    )
    if presentation.sections.isEmpty {
      Text(presentation.emptyMessage ?? "No reasons reported")
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, minHeight: 190, maxHeight: 270)
    } else {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: 0) {
          ForEach(Array(presentation.sections.enumerated()), id: \.offset) {
            sectionIndex,
            section in
            if sectionIndex > 0 {
              Divider()
            }
            HStack {
              Text(section.title)
                .font(.headline)
              Spacer()
              Text("\(section.descriptors.count)")
                .monospacedDigit()
                .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16)
            .frame(height: 38)

            Divider()
            if section.descriptors.isEmpty {
              Text("No reasons reported")
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 64)
            } else {
              ForEach(Array(section.descriptors.enumerated()), id: \.offset) {
                reasonIndex,
                descriptor in
                if reasonIndex > 0 {
                  Divider()
                }
                ProtectionReasonDetailRow(descriptor: descriptor)
              }
              .padding(.horizontal, 16)
            }
          }
        }
      }
      .frame(minHeight: 190, idealHeight: 230, maxHeight: 270)
    }
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

typealias WindowScreenSettleScheduler = (
  _ delay: TimeInterval,
  _ action: @escaping () -> Void
) -> () -> Void
typealias WindowFrameProvider = @MainActor (NSWindow) -> CGRect

struct WindowScreenObserver: NSViewRepresentable {
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

  @MainActor
  final class Coordinator: NSObject {
    private static let settleDelay: TimeInterval = 0.15

    var onDisplayChange: (UInt32?) -> Void
    private let screenGeometryProvider: () -> [WindowScreenGeometry]
    private let windowFrameProvider: WindowFrameProvider
    private let settleScheduler: WindowScreenSettleScheduler
    private weak var window: NSWindow?
    private var pendingSettleCancellation: (() -> Void)?
    private var settleGeneration = 0
    private var isMoving = false
    private var isLiveResizing = false

    init(
      onDisplayChange: @escaping (UInt32?) -> Void,
      screenGeometryProvider: @escaping () -> [WindowScreenGeometry] = {
        NSScreen.screens.map { screen in
          let number = screen.deviceDescription[
            NSDeviceDescriptionKey("NSScreenNumber")
          ] as? NSNumber
          let displayID: UInt32?
          if let number,
            number.int64Value > 0,
            number.uint64Value <= UInt64(UInt32.max)
          {
            displayID = UInt32(number.uint64Value)
          } else {
            displayID = nil
          }
          return WindowScreenGeometry(displayID: displayID, frame: screen.frame)
        }
      },
      windowFrameProvider: @escaping WindowFrameProvider = { $0.frame },
      settleScheduler: @escaping WindowScreenSettleScheduler = { delay, action in
        let workItem = DispatchWorkItem(block: action)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: workItem)
        return { workItem.cancel() }
      }
    ) {
      self.onDisplayChange = onDisplayChange
      self.screenGeometryProvider = screenGeometryProvider
      self.windowFrameProvider = windowFrameProvider
      self.settleScheduler = settleScheduler
    }

    func attach(to window: NSWindow?) {
      guard self.window !== window else { return }
      detach()
      self.window = window
      if let window {
        observe(NSWindow.willMoveNotification, #selector(windowWillMove(_:)), window: window)
        observe(NSWindow.didMoveNotification, #selector(windowDidMove(_:)), window: window)
        observe(
          NSWindow.willStartLiveResizeNotification,
          #selector(windowWillStartLiveResize(_:)),
          window: window
        )
        observe(NSWindow.didResizeNotification, #selector(windowDidResize(_:)), window: window)
        observe(
          NSWindow.didEndLiveResizeNotification,
          #selector(windowDidEndLiveResize(_:)),
          window: window
        )
        observe(
          NSWindow.didChangeScreenNotification,
          #selector(windowDidChangeScreen(_:)),
          window: window
        )
        publishDisplayID(for: window)
      } else {
        onDisplayChange(nil)
      }
    }

    func detach() {
      let hadWindow = window != nil
      cancelPendingSettle()
      if let window {
        NotificationCenter.default.removeObserver(self, name: nil, object: window)
      }
      window = nil
      isMoving = false
      isLiveResizing = false
      if hadWindow {
        onDisplayChange(nil)
      }
    }

    private func observe(_ name: Notification.Name, _ selector: Selector, window: NSWindow) {
      NotificationCenter.default.addObserver(
        self,
        selector: selector,
        name: name,
        object: window
      )
    }

    @objc private func windowWillMove(_ notification: Notification) {
      guard currentWindow(from: notification) != nil else { return }
      isMoving = true
      concealAndCancelSettle()
    }

    @objc private func windowDidMove(_ notification: Notification) {
      guard let window = currentWindow(from: notification) else { return }
      isMoving = false
      concealAndScheduleSettle(for: window)
    }

    @objc private func windowWillStartLiveResize(_ notification: Notification) {
      guard currentWindow(from: notification) != nil else { return }
      isLiveResizing = true
      concealAndCancelSettle()
    }

    @objc private func windowDidResize(_ notification: Notification) {
      guard let window = currentWindow(from: notification) else { return }
      if isLiveResizing {
        concealAndCancelSettle()
      } else {
        concealAndScheduleSettle(for: window)
      }
    }

    @objc private func windowDidEndLiveResize(_ notification: Notification) {
      guard let window = currentWindow(from: notification) else { return }
      isLiveResizing = false
      concealAndScheduleSettle(for: window)
    }

    @objc private func windowDidChangeScreen(_ notification: Notification) {
      guard let window = currentWindow(from: notification) else { return }
      if isMoving || isLiveResizing {
        concealAndCancelSettle()
      } else {
        concealAndScheduleSettle(for: window)
      }
    }

    private func currentWindow(from notification: Notification) -> NSWindow? {
      guard let notifiedWindow = notification.object as? NSWindow,
        notifiedWindow === window
      else {
        return nil
      }
      return notifiedWindow
    }

    private func concealAndCancelSettle() {
      cancelPendingSettle()
      onDisplayChange(nil)
    }

    private func concealAndScheduleSettle(for window: NSWindow) {
      concealAndCancelSettle()
      let generation = settleGeneration
      pendingSettleCancellation = settleScheduler(Self.settleDelay) { [weak self, weak window] in
        guard let self, let window,
          self.settleGeneration == generation,
          self.window === window
        else {
          return
        }
        self.pendingSettleCancellation = nil
        self.settleGeneration += 1
        self.publishDisplayID(for: window)
      }
    }

    private func cancelPendingSettle() {
      settleGeneration += 1
      pendingSettleCancellation?()
      pendingSettleCancellation = nil
    }

    private func publishDisplayID(for window: NSWindow) {
      onDisplayChange(
        WindowDisplaySelection.singleIntersectingDisplayID(
          windowFrame: windowFrameProvider(window),
          screens: screenGeometryProvider()
        )
      )
    }
  }
}

final class ScreenObserverView: NSView {
  var onWindowChange: ((NSWindow?) -> Void)?

  override func viewDidMoveToWindow() {
    super.viewDidMoveToWindow()
    onWindowChange?(window)
  }
}
