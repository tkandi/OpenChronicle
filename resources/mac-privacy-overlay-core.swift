import AppKit
import Foundation

enum IndicatorState: String, Codable {
    case inactive
    case protected
    case paused
    case failed
}

enum IndicatorStyle: String, Codable {
    case off
    case border
    case shield
    case pill
    case quietShield = "quiet-shield"
    case banner
}

struct OverlayDisplay: Codable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let reasons: [OverlayReason]?

    init(
        id: UInt32,
        left: Double,
        top: Double,
        width: Double,
        height: Double,
        reasons: [OverlayReason]? = nil
    ) {
        self.id = id
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.reasons = reasons
    }

    var frame: NSRect {
        NSRect(x: left, y: top, width: width, height: height)
    }

    enum CodingKeys: String, CodingKey {
        case id, left, top, width, height, reasons
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UInt32.self, forKey: .id)
        left = try container.decode(Double.self, forKey: .left)
        top = try container.decode(Double.self, forKey: .top)
        width = try container.decode(Double.self, forKey: .width)
        height = try container.decode(Double.self, forKey: .height)
        reasons = try container.decodeIfPresent([OverlayReason].self, forKey: .reasons) ?? []
    }
}

struct OverlayCommand: Codable {
    let generation: Int
    let state: IndicatorState
    let style: IndicatorStyle
    let displays: [OverlayDisplay]
    let allDisplays: Bool
    let reasonDisplay: String
    let reasonDetail: String
    let reasonTrigger: OverlayReasonTrigger
    let reasons: [OverlayReason]

    init(
        generation: Int,
        state: IndicatorState,
        style: IndicatorStyle,
        displays: [OverlayDisplay],
        allDisplays: Bool,
        reasonDisplay: String = "hybrid",
        reasonDetail: String = "exact",
        reasonTrigger: OverlayReasonTrigger = .hover,
        reasons: [OverlayReason] = []
    ) {
        self.generation = generation
        self.state = state
        self.style = style
        self.displays = displays
        self.allDisplays = allDisplays
        self.reasonDisplay = reasonDisplay
        self.reasonDetail = reasonDetail
        self.reasonTrigger = reasonTrigger
        self.reasons = reasons
    }

    enum CodingKeys: String, CodingKey {
        case generation, state, style, displays
        case allDisplays = "all_displays"
        case reasonDisplay = "reason_display"
        case reasonDetail = "reason_detail"
        case reasonTrigger = "reason_trigger"
        case reasons
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        generation = try container.decode(Int.self, forKey: .generation)
        state = try container.decode(IndicatorState.self, forKey: .state)
        style = try container.decode(IndicatorStyle.self, forKey: .style)
        displays = try container.decode([OverlayDisplay].self, forKey: .displays)
        allDisplays = try container.decode(Bool.self, forKey: .allDisplays)
        reasonDisplay = try container.decodeIfPresent(String.self, forKey: .reasonDisplay) ?? "hybrid"
        reasonDetail = try container.decodeIfPresent(String.self, forKey: .reasonDetail) ?? "exact"
        reasonTrigger = try container.decodeIfPresent(
            OverlayReasonTrigger.self,
            forKey: .reasonTrigger
        ) ?? .hover
        reasons = try container.decodeIfPresent([OverlayReason].self, forKey: .reasons) ?? []
    }
}

struct OverlayAcknowledgement: Codable {
    let generation: Int
    let rendered: Bool
    let error: String?
    let windowIDs: [UInt32]

    enum CodingKeys: String, CodingKey {
        case generation, rendered, error
        case windowIDs = "window_ids"
    }
}

struct OverlayScreenGeometry {
    let id: UInt32
    let frame: NSRect
    let visibleFrame: NSRect
}

struct IndicatorPresentation {
    let text: String?
    let symbolName: String
    let color: NSColor

    static func make(state: IndicatorState, style: IndicatorStyle) -> IndicatorPresentation {
        let includesText = style == .border || style == .pill || style == .banner

        switch state {
        case .protected:
            return IndicatorPresentation(
                text: includesText ? "已保护" : nil,
                symbolName: "checkmark.shield.fill",
                color: .systemGreen
            )
        case .paused:
            return IndicatorPresentation(
                text: includesText ? "已暂停" : nil,
                symbolName: "pause.fill",
                color: .systemGray
            )
        case .failed:
            return IndicatorPresentation(
                text: includesText ? "截图已停用" : nil,
                symbolName: "exclamationmark.triangle.fill",
                color: .systemYellow
            )
        case .inactive:
            return IndicatorPresentation(text: nil, symbolName: "", color: .clear)
        }
    }
}

class PrivacyOverlayPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    override init(
        contentRect: NSRect,
        styleMask: NSWindow.StyleMask = [.borderless, .nonactivatingPanel],
        backing: NSWindow.BackingStoreType = .buffered,
        defer flag: Bool = false
    ) {
        super.init(contentRect: contentRect, styleMask: styleMask, backing: backing, defer: flag)
        self.styleMask.insert(.nonactivatingPanel)
        ignoresMouseEvents = true
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        level = .statusBar
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    }
}

private final class IndicatorView: NSView {
    private static let margin: CGFloat = 12
    private static let iconSize: CGFloat = 18
    private static let compactSize = NSSize(width: 30, height: 30)
    private static let pillHeight: CGFloat = 30
    private static let bannerHeight: CGFloat = 30
    private static let expandedWidth: CGFloat = 340
    private static let reasonRowHeight: CGFloat = 18
    private static let reasonPadding: CGFloat = 10
    private static let reasonGap: CGFloat = 8
    private static let maximumReasonLines = 3

    private var presentation: IndicatorPresentation
    private var style: IndicatorStyle
    private var reasons: [OverlayReason]
    private var includeExactValues: Bool
    private var revealState: ReasonRevealState
    var onClick: (() -> Void)?

    init(
        presentation: IndicatorPresentation,
        style: IndicatorStyle,
        reasons: [OverlayReason],
        includeExactValues: Bool,
        trigger: OverlayReasonTrigger
    ) {
        self.presentation = presentation
        self.style = style
        self.reasons = reasons
        self.includeExactValues = includeExactValues
        revealState = ReasonRevealState(trigger: trigger)
        super.init(frame: .zero)
    }

    required init?(coder: NSCoder) {
        nil
    }

    var trigger: OverlayReasonTrigger { revealState.trigger }
    var hasReasons: Bool { !reasons.isEmpty }
    var isReasonExpanded: Bool { revealState.isExpanded && hasReasons }
    var styleForLayout: IndicatorStyle { style }
    var usesSeparateInputPanel: Bool { style == .border || style == .banner }

    func update(
        presentation: IndicatorPresentation,
        style: IndicatorStyle,
        reasons: [OverlayReason],
        includeExactValues: Bool,
        trigger: OverlayReasonTrigger
    ) {
        self.presentation = presentation
        self.style = style
        self.reasons = reasons
        self.includeExactValues = includeExactValues
        if revealState.trigger != trigger {
            revealState = ReasonRevealState(trigger: trigger)
        }
        needsDisplay = true
    }

    func update(pointerInside: Bool) -> Bool {
        let wasExpanded = isReasonExpanded
        revealState.update(pointerInside: pointerInside)
        let changed = wasExpanded != isReasonExpanded
        if changed { needsDisplay = true }
        return changed
    }

    func click() -> Bool {
        guard hasReasons else { return false }
        let wasExpanded = isReasonExpanded
        revealState.click()
        let changed = wasExpanded != isReasonExpanded
        if changed { needsDisplay = true }
        return changed
    }

    func desiredPanelSize() -> NSSize {
        let compact = compactPanelSize()
        guard isReasonExpanded else { return compact }
        return NSSize(
            width: Self.expandedWidth,
            height: compact.height + Self.reasonGap + reasonBoxHeight
        )
    }

    var hitTargetRect: NSRect {
        let status = statusRect(in: bounds)
        guard isReasonExpanded else { return status }
        return status.union(reasonBoxRect(in: bounds))
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func mouseDown(with event: NSEvent) {
        guard trigger == .click, hasReasons else { return }
        let point = convert(event.locationInWindow, from: nil)
        guard hitTargetRect.contains(point) else { return }
        onClick?()
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        if isReasonExpanded {
            drawReasonBox(in: reasonBoxRect(in: bounds))
        }
        let status = statusRect(in: bounds)
        switch style {
        case .border:
            drawBorder()
            drawBadge(in: status, fill: presentation.color)
        case .banner:
            let banner = NSRect(
                x: bounds.minX,
                y: bounds.maxY - Self.bannerHeight,
                width: bounds.width,
                height: Self.bannerHeight
            )
            drawBadge(in: banner, fill: presentation.color, cornerRadius: 0)
        case .shield, .pill:
            drawBadge(in: status, fill: presentation.color)
        case .quietShield:
            drawQuietSymbol(in: status)
        case .off:
            break
        }
    }

    private var reasonLines: [String] {
        overlayReasonLines(
            reasons,
            includeExactValues: includeExactValues,
            maximumLines: Self.maximumReasonLines
        )
    }

    private var reasonBoxHeight: CGFloat {
        CGFloat(reasonLines.count) * Self.reasonRowHeight + Self.reasonPadding * 2
    }

    private func compactPanelSize() -> NSSize {
        switch style {
        case .shield, .quietShield:
            return Self.compactSize
        case .pill:
            return Self.compactSizeForText(presentation.text)
        case .border, .banner, .off:
            return .zero
        }
    }

    private static func compactSizeForText(_ text: String?) -> NSSize {
        let textWidth = (text as NSString? ?? "").size(
            withAttributes: [.font: NSFont.systemFont(ofSize: 12, weight: .medium)]
        ).width
        return NSSize(width: max(68, textWidth + 50), height: pillHeight)
    }

    private func statusRect(in container: NSRect) -> NSRect {
        let size: NSSize
        switch style {
        case .shield, .quietShield:
            size = Self.compactSize
        case .pill, .border, .banner:
            size = Self.compactSizeForText(presentation.text)
        case .off:
            return .zero
        }

        switch style {
        case .shield, .pill, .quietShield:
            return NSRect(
                x: container.maxX - size.width,
                y: container.minY,
                width: size.width,
                height: size.height
            )
        case .border:
            return NSRect(
                x: container.maxX - size.width - Self.margin,
                y: container.minY + Self.margin,
                width: size.width,
                height: size.height
            )
        case .banner:
            return NSRect(
                x: container.maxX - size.width - Self.margin,
                y: container.maxY - Self.bannerHeight,
                width: size.width,
                height: Self.bannerHeight
            )
        case .off:
            return .zero
        }
    }

    private func reasonBoxRect(in container: NSRect) -> NSRect {
        guard isReasonExpanded else { return .zero }
        let status = statusRect(in: container)
        switch style {
        case .shield, .pill, .quietShield:
            return NSRect(
                x: container.minX,
                y: status.maxY + Self.reasonGap,
                width: container.width,
                height: reasonBoxHeight
            )
        case .border:
            let width = min(Self.expandedWidth, max(0, container.width - Self.margin * 2))
            return NSRect(
                x: container.maxX - width - Self.margin,
                y: status.maxY + Self.reasonGap,
                width: width,
                height: reasonBoxHeight
            )
        case .banner:
            let width = min(Self.expandedWidth, max(0, container.width - Self.margin * 2))
            return NSRect(
                x: container.maxX - width - Self.margin,
                y: status.minY - Self.reasonGap - reasonBoxHeight,
                width: width,
                height: reasonBoxHeight
            )
        case .off:
            return .zero
        }
    }

    private func drawBorder() {
        presentation.color.withAlphaComponent(0.9).setStroke()
        let path = NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1))
        path.lineWidth = 2
        path.stroke()
    }

    private func drawReasonBox(in rect: NSRect) {
        guard !rect.isEmpty else { return }
        NSColor.black.withAlphaComponent(0.86).setFill()
        NSBezierPath(roundedRect: rect, xRadius: 7, yRadius: 7).fill()

        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingTail
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .regular),
            .foregroundColor: NSColor.white,
            .paragraphStyle: paragraph,
        ]
        for (index, line) in reasonLines.enumerated() {
            let lineRect = NSRect(
                x: rect.minX + Self.reasonPadding,
                y: rect.maxY - Self.reasonPadding - CGFloat(index + 1) * Self.reasonRowHeight,
                width: rect.width - Self.reasonPadding * 2,
                height: Self.reasonRowHeight
            )
            (line as NSString).draw(in: lineRect, withAttributes: attributes)
        }
    }

    private func drawBadge(in rect: NSRect, fill: NSColor, cornerRadius: CGFloat = 7) {
        fill.withAlphaComponent(0.92).setFill()
        NSBezierPath(roundedRect: rect, xRadius: cornerRadius, yRadius: cornerRadius).fill()

        let iconRect = NSRect(
            x: rect.minX + 7,
            y: rect.midY - Self.iconSize / 2,
            width: Self.iconSize,
            height: Self.iconSize
        )
        drawSymbol(in: iconRect, color: .white)

        guard let text = presentation.text else { return }
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 12, weight: .medium),
            .foregroundColor: NSColor.white,
        ]
        let textSize = (text as NSString).size(withAttributes: attributes)
        let textRect = NSRect(
            x: iconRect.maxX + 5,
            y: rect.midY - textSize.height / 2,
            width: rect.maxX - iconRect.maxX - 10,
            height: textSize.height
        )
        (text as NSString).draw(in: textRect, withAttributes: attributes)
    }

    private func drawQuietSymbol(in rect: NSRect) {
        presentation.color.withAlphaComponent(0.18).setFill()
        NSBezierPath(roundedRect: rect, xRadius: 7, yRadius: 7).fill()
        presentation.color.withAlphaComponent(0.8).setStroke()
        let path = NSBezierPath(
            roundedRect: rect.insetBy(dx: 0.5, dy: 0.5),
            xRadius: 7,
            yRadius: 7
        )
        path.lineWidth = 1
        path.stroke()
        drawSymbol(in: rect.insetBy(dx: 6, dy: 6), color: presentation.color)
    }

    private func drawSymbol(in rect: NSRect, color: NSColor) {
        guard let image = NSImage(
            systemSymbolName: presentation.symbolName,
            accessibilityDescription: nil
        )?.withSymbolConfiguration(
            NSImage.SymbolConfiguration(paletteColors: [color])
        ) else { return }
        image.isTemplate = false
        image.draw(in: rect)
    }
}

private final class IndicatorInputView: NSView {
    var onClick: (() -> Void)?

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        guard bounds.contains(point) else { return }
        onClick?()
    }
}

typealias OverlayPointerTimerFactory = (
    _ interval: TimeInterval,
    _ tick: @escaping () -> Void
) -> () -> Void

private let pointerPollInterval: TimeInterval = 0.08

final class PrivacyOverlayController {
    private var panels: [UInt32: PrivacyOverlayPanel] = [:]
    private var inputPanels: [UInt32: PrivacyOverlayPanel] = [:]
    private var panelScreens: [UInt32: OverlayScreenGeometry] = [:]
    private var cancelPointerTimer: (() -> Void)?
    private let screenProvider: () -> [OverlayScreenGeometry]
    private let panelFactory: () -> PrivacyOverlayPanel
    private let inputPanelFactory: () -> PrivacyOverlayPanel
    private let pointerProvider: () -> NSPoint
    private let timerFactory: OverlayPointerTimerFactory
    private let windowNumberProvider: (PrivacyOverlayPanel) -> Int

    init(
        screenProvider: @escaping () -> [OverlayScreenGeometry] = PrivacyOverlayController.systemScreenGeometry,
        panelFactory: @escaping () -> PrivacyOverlayPanel = { PrivacyOverlayPanel(contentRect: .zero) },
        inputPanelFactory: @escaping () -> PrivacyOverlayPanel = {
            PrivacyOverlayPanel(contentRect: .zero)
        },
        pointerProvider: @escaping () -> NSPoint = { NSEvent.mouseLocation },
        timerFactory: @escaping OverlayPointerTimerFactory = PrivacyOverlayController.makePointerTimer,
        windowNumberProvider: @escaping (PrivacyOverlayPanel) -> Int = { $0.windowNumber }
    ) {
        self.screenProvider = screenProvider
        self.panelFactory = panelFactory
        self.inputPanelFactory = inputPanelFactory
        self.pointerProvider = pointerProvider
        self.timerFactory = timerFactory
        self.windowNumberProvider = windowNumberProvider
    }

    deinit {
        cancelPointerTimer?()
    }

    func apply(_ command: OverlayCommand, completion: @escaping (Bool) -> Void) {
        applyWithWindowIDs(command) { rendered, _windowIDs in
            completion(rendered || !self.panels.isEmpty)
        }
    }

    func applyWithWindowIDs(
        _ command: OverlayCommand,
        completion: @escaping (Bool, [UInt32]) -> Void
    ) {
        dispatchPrecondition(condition: .onQueue(.main))

        guard command.state != .inactive, command.style != .off else {
            removeAllPanels()
            completion(true, [])
            return
        }

        guard let displays = resolvedDisplays(for: command) else {
            removeAllPanels()
            completion(false, [])
            return
        }

        let displayIDs = Set(displays.map(\.id))
        let obsoleteIDs = panels.keys.filter { !displayIDs.contains($0) }
        for id in obsoleteIDs {
            panelScreens.removeValue(forKey: id)
            removeInputPanel(displayID: id)
            guard let panel = panels.removeValue(forKey: id) else { continue }
            panel.orderOut(nil)
            panel.close()
        }

        let presentation = IndicatorPresentation.make(state: command.state, style: command.style)
        for display in displays {
            let panel = panels[display.id] ?? panelFactory()
            let displayReasons: [OverlayReason]
            if command.reasonDisplay == "overlay" || command.reasonDisplay == "hybrid" {
                displayReasons = command.displays.first { $0.id == display.id }?.reasons
                    ?? command.reasons
            } else {
                displayReasons = []
            }
            let view: IndicatorView
            if let existing = panel.contentView as? IndicatorView {
                view = existing
                view.update(
                    presentation: presentation,
                    style: command.style,
                    reasons: displayReasons,
                    includeExactValues: command.reasonDetail == "exact",
                    trigger: command.reasonTrigger
                )
            } else {
                view = IndicatorView(
                    presentation: presentation,
                    style: command.style,
                    reasons: displayReasons,
                    includeExactValues: command.reasonDetail == "exact",
                    trigger: command.reasonTrigger
                )
                panel.contentView = view
            }
            view.onClick = { [weak self] in
                self?.toggleReason(for: display.id)
            }
            panels[display.id] = panel
            panelScreens[display.id] = display
            layoutPanel(displayID: display.id)
            updateInputState(panel: panel, view: view, pointer: pointerProvider())
            panel.orderFrontRegardless()
            view.displayIfNeeded()
            panel.displayIfNeeded()
            updateInputPanel(displayID: display.id)
        }

        startPointerTimerIfNeeded()
        pollPointer()
        guard let windowIDs = currentWindowIDs() else {
            completion(false, [])
            return
        }
        completion(true, windowIDs)
    }

    private func currentWindowIDs() -> [UInt32]? {
        let currentPanels = Array(panels.values) + Array(inputPanels.values)
        guard !currentPanels.isEmpty else { return nil }
        var windowIDs = Set<UInt32>()
        for panel in currentPanels {
            let windowNumber = windowNumberProvider(panel)
            guard windowNumber > 0, windowNumber <= Int(UInt32.max) else {
                return nil
            }
            guard windowIDs.insert(UInt32(windowNumber)).inserted else {
                return nil
            }
        }
        guard windowIDs.count == currentPanels.count else { return nil }
        return windowIDs.sorted()
    }

    private func resolvedDisplays(for command: OverlayCommand) -> [OverlayScreenGeometry]? {
        let screens = screenProvider()
        var screensByID: [UInt32: OverlayScreenGeometry] = [:]
        for screen in screens {
            guard screensByID[screen.id] == nil else { return nil }
            screensByID[screen.id] = screen
        }

        if !command.displays.isEmpty {
            var resolved: [OverlayScreenGeometry] = []
            var requestedIDs = Set<UInt32>()
            for display in command.displays {
                guard requestedIDs.insert(display.id).inserted,
                      let screen = screensByID[display.id] else {
                    return nil
                }
                resolved.append(screen)
            }
            return resolved
        }

        guard command.allDisplays, !screens.isEmpty else { return nil }
        return screens
    }

    private func layoutPanel(displayID: UInt32) {
        guard let panel = panels[displayID],
              let display = panelScreens[displayID],
              let view = panel.contentView as? IndicatorView else { return }
        panel.setFrame(panelFrame(for: display, view: view), display: true)
        view.frame = panel.contentView?.bounds ?? .zero
        view.needsDisplay = true
    }

    private func panelFrame(
        for display: OverlayScreenGeometry,
        view: IndicatorView
    ) -> NSRect {
        switch view.styleForLayout {
        case .shield, .pill, .quietShield:
            let size = view.desiredPanelSize()
            return NSRect(
                x: display.visibleFrame.maxX - size.width - 12,
                y: display.visibleFrame.minY + 12,
                width: size.width,
                height: size.height
            )
        case .border, .banner, .off:
            return display.frame
        }
    }

    private func toggleReason(for displayID: UInt32) {
        guard let panel = panels[displayID],
              let view = panel.contentView as? IndicatorView,
              view.click() else { return }
        layoutPanel(displayID: displayID)
        updateInputState(panel: panel, view: view, pointer: pointerProvider())
        view.displayIfNeeded()
        panel.displayIfNeeded()
        updateInputPanel(displayID: displayID)
    }

    private func pollPointer() {
        let pointer = pointerProvider()
        for displayID in panels.keys.sorted() {
            guard let panel = panels[displayID],
                  let view = panel.contentView as? IndicatorView else { continue }
            let inside = pointerIsInside(pointer, panel: panel, view: view)
            let revealChanged = view.update(pointerInside: inside)
            if revealChanged {
                layoutPanel(displayID: displayID)
            }
            updateInputState(panel: panel, view: view, pointer: pointer)
            if revealChanged {
                view.displayIfNeeded()
                panel.displayIfNeeded()
            }
        }
    }

    private func updateInputState(
        panel: PrivacyOverlayPanel,
        view: IndicatorView,
        pointer: NSPoint
    ) {
        if view.usesSeparateInputPanel {
            panel.ignoresMouseEvents = true
            return
        }
        let inside = pointerIsInside(pointer, panel: panel, view: view)
        panel.ignoresMouseEvents = !(view.trigger == .click && view.hasReasons && inside)
    }

    private func updateInputPanel(displayID: UInt32) {
        guard let visualPanel = panels[displayID],
              let indicatorView = visualPanel.contentView as? IndicatorView,
              indicatorView.usesSeparateInputPanel,
              indicatorView.trigger == .click,
              indicatorView.hasReasons else {
            removeInputPanel(displayID: displayID)
            return
        }

        let target = indicatorView.hitTargetRect
        guard !target.isEmpty else {
            removeInputPanel(displayID: displayID)
            return
        }
        let inputPanel = inputPanels[displayID] ?? inputPanelFactory()
        let inputView: IndicatorInputView
        if let existing = inputPanel.contentView as? IndicatorInputView {
            inputView = existing
        } else {
            inputView = IndicatorInputView(frame: .zero)
            inputPanel.contentView = inputView
        }
        inputView.onClick = { [weak self] in
            self?.toggleReason(for: displayID)
        }
        inputPanel.ignoresMouseEvents = false
        inputPanel.setFrame(
            NSRect(
                x: visualPanel.frame.minX + target.minX,
                y: visualPanel.frame.minY + target.minY,
                width: target.width,
                height: target.height
            ),
            display: true
        )
        inputView.frame = inputPanel.contentView?.bounds ?? .zero
        inputPanels[displayID] = inputPanel
        inputPanel.orderFrontRegardless()
    }

    private func removeInputPanel(displayID: UInt32) {
        guard let inputPanel = inputPanels.removeValue(forKey: displayID) else { return }
        inputPanel.orderOut(nil)
        inputPanel.close()
    }

    private func pointerIsInside(
        _ pointer: NSPoint,
        panel: PrivacyOverlayPanel,
        view: IndicatorView
    ) -> Bool {
        let pointInPanel = NSPoint(
            x: pointer.x - panel.frame.minX,
            y: pointer.y - panel.frame.minY
        )
        return view.hitTargetRect.contains(pointInPanel)
    }

    private func startPointerTimerIfNeeded() {
        guard cancelPointerTimer == nil, !panels.isEmpty else { return }
        cancelPointerTimer = timerFactory(pointerPollInterval) { [weak self] in
            self?.pollPointer()
        }
    }

    private func stopPointerTimer() {
        cancelPointerTimer?()
        cancelPointerTimer = nil
    }

    static func makePointerTimer(
        interval: TimeInterval,
        tick: @escaping () -> Void
    ) -> () -> Void {
        let timer = Timer(timeInterval: interval, repeats: true) { _ in tick() }
        RunLoop.main.add(timer, forMode: .common)
        return { timer.invalidate() }
    }

    private static func systemScreenGeometry() -> [OverlayScreenGeometry] {
        NSScreen.screens.compactMap { screen in
            guard let number = screen.deviceDescription[
                NSDeviceDescriptionKey("NSScreenNumber")
            ] as? NSNumber else {
                return nil
            }
            return OverlayScreenGeometry(
                id: number.uint32Value,
                frame: screen.frame,
                visibleFrame: screen.visibleFrame
            )
        }
    }

    private func removeAllPanels() {
        stopPointerTimer()
        for inputPanel in inputPanels.values {
            inputPanel.orderOut(nil)
            inputPanel.close()
        }
        for panel in panels.values {
            panel.orderOut(nil)
            panel.close()
        }
        inputPanels.removeAll()
        panels.removeAll()
        panelScreens.removeAll()
    }
}
