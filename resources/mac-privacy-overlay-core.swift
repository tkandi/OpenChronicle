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

struct OverlayDisplay: Codable, Hashable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double

    var frame: NSRect {
        NSRect(x: left, y: top, width: width, height: height)
    }
}

struct OverlayCommand: Codable {
    let generation: Int
    let state: IndicatorState
    let style: IndicatorStyle
    let displays: [OverlayDisplay]
    let allDisplays: Bool

    enum CodingKeys: String, CodingKey {
        case generation, state, style, displays
        case allDisplays = "all_displays"
    }
}

struct OverlayAcknowledgement: Codable {
    let generation: Int
    let rendered: Bool
    let error: String?
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

final class PrivacyOverlayPanel: NSPanel {
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

    private var presentation: IndicatorPresentation
    private var style: IndicatorStyle

    init(presentation: IndicatorPresentation, style: IndicatorStyle) {
        self.presentation = presentation
        self.style = style
        super.init(frame: .zero)
    }

    required init?(coder: NSCoder) {
        nil
    }

    func update(presentation: IndicatorPresentation, style: IndicatorStyle) {
        self.presentation = presentation
        self.style = style
        needsDisplay = true
    }

    static func panelSize(for presentation: IndicatorPresentation, style: IndicatorStyle) -> NSSize {
        switch style {
        case .shield, .quietShield:
            return compactSize
        case .pill:
            return compactSizeForText(presentation.text)
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

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)

        switch style {
        case .border:
            drawBorder()
            drawBadge(in: badgeRect(in: bounds), fill: presentation.color)
        case .banner:
            let banner = NSRect(x: bounds.minX, y: bounds.maxY - Self.bannerHeight, width: bounds.width, height: Self.bannerHeight)
            drawBadge(in: banner, fill: presentation.color, cornerRadius: 0)
        case .shield:
            drawBadge(in: bounds, fill: presentation.color)
        case .pill:
            drawBadge(in: bounds, fill: presentation.color)
        case .quietShield:
            drawQuietSymbol(in: bounds)
        case .off:
            break
        }
    }

    private func drawBorder() {
        presentation.color.withAlphaComponent(0.9).setStroke()
        let path = NSBezierPath(rect: bounds.insetBy(dx: 1, dy: 1))
        path.lineWidth = 2
        path.stroke()
    }

    private func badgeRect(in container: NSRect) -> NSRect {
        let size = Self.compactSizeForText(presentation.text)
        return NSRect(
            x: container.maxX - size.width - Self.margin,
            y: container.minY + Self.margin,
            width: size.width,
            height: size.height
        )
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
        let path = NSBezierPath(roundedRect: rect.insetBy(dx: 0.5, dy: 0.5), xRadius: 7, yRadius: 7)
        path.lineWidth = 1
        path.stroke()
        drawSymbol(in: rect.insetBy(dx: 6, dy: 6), color: presentation.color)
    }

    private func drawSymbol(in rect: NSRect, color: NSColor) {
        guard let image = NSImage(
            systemSymbolName: presentation.symbolName,
            accessibilityDescription: nil
        ) else { return }
        image.isTemplate = true
        color.set()
        image.draw(in: rect)
    }
}

final class PrivacyOverlayController {
    private var panels: [UInt32: PrivacyOverlayPanel] = [:]

    func apply(_ command: OverlayCommand, completion: @escaping () -> Void) {
        dispatchPrecondition(condition: .onQueue(.main))

        guard command.state != .inactive, command.style != .off else {
            removeAllPanels()
            completion()
            return
        }

        let displays = targetDisplays(for: command)
        let displayIDs = Set(displays.map(\.id))
        let obsoleteIDs = panels.keys.filter { !displayIDs.contains($0) }
        for id in obsoleteIDs {
            guard let panel = panels.removeValue(forKey: id) else { continue }
            panel.orderOut(nil)
            panel.close()
        }

        let presentation = IndicatorPresentation.make(state: command.state, style: command.style)
        for display in displays {
            let panel = panels[display.id] ?? PrivacyOverlayPanel(contentRect: .zero)
            let frame = panelFrame(for: display, style: command.style, presentation: presentation)
            panel.setFrame(frame, display: true)
            let view: IndicatorView
            if let existing = panel.contentView as? IndicatorView {
                view = existing
                view.update(presentation: presentation, style: command.style)
            } else {
                view = IndicatorView(presentation: presentation, style: command.style)
                panel.contentView = view
            }
            view.frame = panel.contentView?.bounds ?? .zero
            panel.orderFrontRegardless()
            panels[display.id] = panel
        }

        completion()
    }

    private func targetDisplays(for command: OverlayCommand) -> [OverlayDisplay] {
        if !command.displays.isEmpty {
            return command.displays
        }
        guard command.allDisplays else { return [] }
        return NSScreen.screens.compactMap { screen in
            guard let id = Self.displayID(for: screen) else { return nil }
            let frame = screen.frame
            return OverlayDisplay(
                id: id,
                left: frame.minX,
                top: frame.minY,
                width: frame.width,
                height: frame.height
            )
        }
    }

    private func panelFrame(
        for display: OverlayDisplay,
        style: IndicatorStyle,
        presentation: IndicatorPresentation
    ) -> NSRect {
        switch style {
        case .shield, .pill, .quietShield:
            let visibleFrame = screen(for: display)?.visibleFrame ?? display.frame
            let size = IndicatorView.panelSize(for: presentation, style: style)
            return NSRect(
                x: visibleFrame.maxX - size.width - 12,
                y: visibleFrame.minY + 12,
                width: size.width,
                height: size.height
            )
        case .border, .banner, .off:
            return screen(for: display)?.frame ?? display.frame
        }
    }

    private func screen(for display: OverlayDisplay) -> NSScreen? {
        NSScreen.screens.first { Self.displayID(for: $0) == display.id }
    }

    private static func displayID(for screen: NSScreen) -> UInt32? {
        guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
            return nil
        }
        return number.uint32Value
    }

    private func removeAllPanels() {
        for panel in panels.values {
            panel.orderOut(nil)
            panel.close()
        }
        panels.removeAll()
    }
}
