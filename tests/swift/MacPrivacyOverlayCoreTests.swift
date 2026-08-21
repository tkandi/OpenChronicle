import AppKit
import Foundation

private final class RecordingPanel: PrivacyOverlayPanel {
    var displayIfNeededCount = 0
    var orderOutCount = 0

    override func displayIfNeeded() {
        displayIfNeededCount += 1
        super.displayIfNeeded()
    }

    override func orderOut(_ sender: Any?) {
        orderOutCount += 1
        super.orderOut(sender)
    }
}

@main
enum MacPrivacyOverlayCoreTests {
    static func main() throws {
        let raw = Data(#"{"generation":9,"state":"protected","style":"pill","displays":[],"all_displays":false}"#.utf8)
        let command = try JSONDecoder().decode(OverlayCommand.self, from: raw)
        precondition(command.generation == 9)
        precondition(command.style == .pill)
        precondition(command.reasonTrigger == .hover)
        precondition(command.reasons.isEmpty)
        precondition(command.displays.allSatisfy { ($0.reasons ?? []).isEmpty })

        testRevealState()
        testReasonPresentation()

        let protectedPresentation = IndicatorPresentation.make(state: .protected, style: .pill)
        precondition(protectedPresentation.text == "已保护")
        precondition(protectedPresentation.symbolName == "checkmark.shield.fill")

        let paused = IndicatorPresentation.make(state: .paused, style: .shield)
        precondition(paused.text == nil)
        precondition(paused.symbolName == "pause.fill")

        let panel = PrivacyOverlayPanel(contentRect: .zero)
        precondition(panel.ignoresMouseEvents)
        precondition(!panel.canBecomeKey)
        precondition(!panel.canBecomeMain)

        testControllerRendering()
        testHoverStaysMouseThrough()
        testClickUsesOnlyIndicatorHitTarget()
        testBannerUsesOnlyCornerHitTarget()
        testAlwaysStaysExpandedAndMouseThrough()
        testDiagnosticsModeIgnoresWireReasons()
        testPointerTimerStopsWhenOverlayClears()
        print("MacPrivacyOverlayCoreTests passed")
    }

    private static func testRevealState() {
        var hover = ReasonRevealState(trigger: .hover)
        hover.update(pointerInside: true)
        precondition(hover.isExpanded)
        hover.update(pointerInside: false)
        precondition(!hover.isExpanded)

        var click = ReasonRevealState(trigger: .click)
        click.click()
        precondition(click.isExpanded)
        click.click()
        precondition(!click.isExpanded)

        var always = ReasonRevealState(trigger: .always)
        precondition(always.isExpanded)
        always.update(pointerInside: false)
        always.click()
        precondition(always.isExpanded)
    }

    private static func testReasonPresentation() {
        let reason = OverlayReason(
            code: "window_title_rule",
            displayID: 2,
            sourceDisplayID: nil,
            appName: "Edge",
            bundleID: "com.microsoft.edgemac",
            windowTitle: "InPrivate",
            rule: "InPrivate"
        )
        precondition(reason.presentationText(includeExactValues: false) == "窗口标题规则")
        let exact = reason.presentationText(includeExactValues: true)
        precondition(exact.contains("应用: Edge"))
        precondition(exact.contains("标识: com.microsoft.edgemac"))
        precondition(exact.contains("标题: InPrivate"))
        precondition(exact.contains("规则: InPrivate"))

        let unknown = OverlayReason(
            code: "private-error-payload",
            displayID: nil,
            sourceDisplayID: nil,
            appName: nil,
            bundleID: nil,
            windowTitle: nil,
            rule: nil
        )
        precondition(unknown.presentationText(includeExactValues: true) == "隐私保护")

        let overflow = overlayReasonLines(
            Array(repeating: reason, count: 5),
            includeExactValues: false,
            maximumLines: 3
        )
        precondition(overflow == ["窗口标题规则", "窗口标题规则", "+3"])
    }

    private static func testControllerRendering() {
        let screens = [
            OverlayScreenGeometry(
                id: 1,
                frame: NSRect(x: 0, y: 0, width: 100, height: 100),
                visibleFrame: NSRect(x: 10, y: 20, width: 80, height: 70)
            ),
            OverlayScreenGeometry(
                id: 2,
                frame: NSRect(x: 100, y: 0, width: 200, height: 120),
                visibleFrame: NSRect(x: 100, y: 0, width: 200, height: 100)
            ),
        ]
        var panels: [RecordingPanel] = []
        let controller = PrivacyOverlayController(
            screenProvider: { screens },
            panelFactory: {
                let panel = RecordingPanel(contentRect: .zero)
                panels.append(panel)
                return panel
            }
        )

        var firstRendered: Bool?
        controller.apply(
            OverlayCommand(
                generation: 10,
                state: .protected,
                style: .shield,
                displays: [],
                allDisplays: true
            )
        ) { rendered in
            precondition(panels.count == 2)
            precondition(
                panels.allSatisfy { $0.displayIfNeededCount == 1 },
                "display counts: \(panels.map(\.displayIfNeededCount))"
            )
            firstRendered = rendered
        }
        precondition(firstRendered == true)
        precondition(panels[0].frame == NSRect(x: 48, y: 32, width: 30, height: 30))
        precondition(panels[1].frame == NSRect(x: 258, y: 12, width: 30, height: 30))

        var transitioned: Bool?
        controller.apply(
            OverlayCommand(
                generation: 11,
                state: .paused,
                style: .banner,
                displays: [OverlayDisplay(id: 1, left: 900, top: 900, width: 10, height: 10)],
                allDisplays: false
            )
        ) { rendered in
            precondition(panels[0].displayIfNeededCount == 2)
            transitioned = rendered
        }
        precondition(transitioned == true)
        precondition(panels.count == 2)
        precondition(panels[0].frame == screens[0].frame)
        precondition(panels[1].orderOutCount >= 1)
        precondition(!panels[1].isVisible)

        var rejected: Bool?
        controller.apply(
            OverlayCommand(
                generation: 12,
                state: .protected,
                style: .pill,
                displays: [OverlayDisplay(id: 99, left: 0, top: 999, width: 10, height: 10)],
                allDisplays: false
            )
        ) { rendered in
            rejected = rendered
        }
        precondition(rejected == false)
        precondition(panels[0].orderOutCount >= 1)
        precondition(!panels[0].isVisible)
    }

    private static func testHoverStaysMouseThrough() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: -100, y: -100)
        var tick: (() -> Void)?
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { pointer },
            timerFactory: { interval, handler in
                precondition(interval == 0.08)
                tick = handler
                return {}
            }
        )

        controller.apply(reasonCommand(trigger: .hover, style: .pill)) { rendered in
            precondition(rendered)
        }
        let compactFrame = panel.frame
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: compactFrame.midX, y: compactFrame.midY)
        tick?()
        precondition(panel.frame.width > compactFrame.width)
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: -100, y: -100)
        tick?()
        precondition(panel.frame == compactFrame)
        precondition(panel.ignoresMouseEvents)
        precondition(!panel.canBecomeKey)
        precondition(!panel.canBecomeMain)
    }

    private static func testClickUsesOnlyIndicatorHitTarget() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: 400, y: 300)
        var tick: (() -> Void)?
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { pointer },
            timerFactory: { _, handler in
                tick = handler
                return {}
            }
        )

        controller.apply(reasonCommand(trigger: .click, style: .border)) { rendered in
            precondition(rendered)
        }
        precondition(panel.frame == screen.frame)
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: 740, y: 27)
        tick?()
        precondition(!panel.ignoresMouseEvents)

        let event = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: pointer,
            modifierFlags: [],
            timestamp: 0,
            windowNumber: panel.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1
        )!
        panel.contentView?.mouseDown(with: event)
        precondition(!panel.ignoresMouseEvents)

        pointer = NSPoint(x: 740, y: 70)
        tick?()
        precondition(!panel.ignoresMouseEvents)
        panel.contentView?.mouseDown(with: event)
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: 400, y: 300)
        tick?()
        precondition(panel.ignoresMouseEvents)
        precondition(!panel.canBecomeKey)
        precondition(!panel.canBecomeMain)
    }

    private static func testAlwaysStaysExpandedAndMouseThrough() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { NSPoint(x: -100, y: -100) },
            timerFactory: { _, _ in {} }
        )

        controller.apply(reasonCommand(trigger: .always, style: .pill)) { rendered in
            precondition(rendered)
        }

        precondition(panel.frame.width == 340)
        precondition(panel.ignoresMouseEvents)
        precondition(!panel.canBecomeKey)
        precondition(!panel.canBecomeMain)
    }

    private static func testBannerUsesOnlyCornerHitTarget() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: 100, y: 585)
        var tick: (() -> Void)?
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { pointer },
            timerFactory: { _, handler in
                tick = handler
                return {}
            }
        )

        controller.apply(reasonCommand(trigger: .click, style: .banner)) { rendered in
            precondition(rendered)
        }
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: 740, y: 585)
        tick?()
        precondition(!panel.ignoresMouseEvents)

        pointer = NSPoint(x: 100, y: 585)
        tick?()
        precondition(panel.ignoresMouseEvents)
    }

    private static func testPointerTimerStopsWhenOverlayClears() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var timerCancelled = false
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { NSPoint(x: -100, y: -100) },
            timerFactory: { _, _ in
                { timerCancelled = true }
            }
        )

        controller.apply(reasonCommand(trigger: .hover, style: .pill)) { rendered in
            precondition(rendered)
        }
        precondition(!timerCancelled)

        controller.apply(
            OverlayCommand(
                generation: 21,
                state: .inactive,
                style: .off,
                displays: [],
                allDisplays: false
            )
        ) { rendered in
            precondition(rendered)
        }
        precondition(timerCancelled)
    }

    private static func testDiagnosticsModeIgnoresWireReasons() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            pointerProvider: { NSPoint(x: -100, y: -100) },
            timerFactory: { _, _ in {} }
        )

        controller.apply(
            reasonCommand(trigger: .always, style: .pill, reasonDisplay: "diagnostics")
        ) { rendered in
            precondition(rendered)
        }

        precondition(panel.frame.width < 340)
        precondition(panel.ignoresMouseEvents)
    }

    private static func reasonCommand(
        trigger: OverlayReasonTrigger,
        style: IndicatorStyle,
        reasonDisplay: String = "hybrid"
    ) -> OverlayCommand {
        OverlayCommand(
            generation: 20,
            state: .protected,
            style: style,
            displays: [
                OverlayDisplay(
                    id: 1,
                    left: 0,
                    top: 0,
                    width: 800,
                    height: 600,
                    reasons: [
                        OverlayReason(
                            code: "window_title_rule",
                            displayID: 1,
                            sourceDisplayID: nil,
                            appName: "Edge",
                            bundleID: "com.microsoft.edgemac",
                            windowTitle: "InPrivate",
                            rule: "InPrivate"
                        )
                    ]
                )
            ],
            allDisplays: false,
            reasonDisplay: reasonDisplay,
            reasonDetail: "exact",
            reasonTrigger: trigger,
            reasons: []
        )
    }
}
