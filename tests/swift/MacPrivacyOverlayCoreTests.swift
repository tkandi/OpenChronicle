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

        let acknowledgement = OverlayAcknowledgement(
            generation: 9,
            rendered: true,
            error: nil,
            windowIDs: [7, 41]
        )
        let acknowledgementJSON = String(
            data: try JSONEncoder().encode(acknowledgement),
            encoding: .utf8
        )!
        precondition(acknowledgementJSON.contains(#""window_ids":[7,41]"#))

        testRevealState()
        testReasonPresentation()
        testTimedPauseResumePresentation()

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
        testControllerAcknowledgesEveryPanelWindowID()
        testControllerKeepsRenderingWhenAnyWindowIDIsUnavailable()
        testHoverStaysMouseThrough()
        testCompactClickKeepsTargetSizedVisualPanel()
        testClickUsesOnlyIndicatorHitTarget()
        testFullScreenVisualPanelNeverConsumesStaleOutsideClick()
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

    private static func testTimedPauseResumePresentation() {
        let present = try! JSONDecoder().decode(
            OverlayReason.self,
            from: Data(
                #"{"code":"timed_pause","effective_resume_at":"2026-08-22T18:30:00+08:00"}"#.utf8
            )
        )
        precondition(present.effectiveResumeAt == "2026-08-22T18:30:00+08:00")
        precondition(
            present.presentationText(includeExactValues: true)
                == "定时暂停 · 恢复: 2026-08-22T18:30:00+08:00"
        )
        precondition(present.presentationText(includeExactValues: false) == "定时暂停")

        let missing = try! JSONDecoder().decode(
            OverlayReason.self,
            from: Data(#"{"code":"timed_pause"}"#.utf8)
        )
        precondition(missing.effectiveResumeAt == nil)
        precondition(missing.presentationText(includeExactValues: true) == "定时暂停")

        let longResume = "prefix\n" + String(repeating: "x", count: 170)
            + "private-control-suffix"
        let bounded = OverlayReason(
            code: "timed_pause",
            displayID: nil,
            sourceDisplayID: nil,
            appName: nil,
            bundleID: nil,
            windowTitle: nil,
            rule: nil,
            effectiveResumeAt: longResume
        )
        let boundedValue = bounded.presentationText(includeExactValues: true)
            .components(separatedBy: "恢复: ").last!
        precondition(boundedValue.count == 160)
        precondition(boundedValue.hasSuffix("…"))
        precondition(!boundedValue.contains("\n"))
        precondition(!boundedValue.contains("private-control-suffix"))
        precondition(bounded.presentationText(includeExactValues: false) == "定时暂停")
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

    private static func testControllerAcknowledgesEveryPanelWindowID() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { NSPoint(x: 400, y: 300) },
            timerFactory: { _, _ in {} },
            windowNumberProvider: { panel in
                panel === visualPanel ? 41 : 7
            }
        )

        var acknowledgement: (Bool, [UInt32])?
        controller.applyWithWindowIDs(reasonCommand(trigger: .click, style: .border)) {
            acknowledgement = ($0, $1)
        }

        precondition(acknowledgement?.0 == true)
        precondition(acknowledgement?.1 == [7, 41])

        controller.applyWithWindowIDs(
            OverlayCommand(
                generation: 99,
                state: .inactive,
                style: .off,
                displays: [],
                allDisplays: false
            )
        ) { rendered, windowIDs in
            precondition(rendered)
            precondition(windowIDs.isEmpty)
        }
    }

    private static func testControllerKeepsRenderingWhenAnyWindowIDIsUnavailable() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { NSPoint(x: 400, y: 300) },
            timerFactory: { _, _ in {} },
            windowNumberProvider: { panel in
                panel === visualPanel ? 41 : 0
            }
        )

        controller.applyWithWindowIDs(reasonCommand(trigger: .click, style: .border)) {
            rendered, windowIDs in
            precondition(rendered)
            precondition(windowIDs.isEmpty)
        }
        precondition(visualPanel.isVisible)
        precondition(inputPanel.isVisible)
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
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { NSPoint(x: 400, y: 300) },
            timerFactory: { _, _ in {} }
        )

        controller.apply(reasonCommand(trigger: .click, style: .border)) { rendered in
            precondition(rendered)
        }
        precondition(visualPanel.frame == screen.frame)
        precondition(visualPanel.ignoresMouseEvents)
        precondition(!inputPanel.ignoresMouseEvents)
        let compactFrame = inputPanel.frame
        precondition(compactFrame.width < screen.frame.width)
        precondition(compactFrame.height < screen.frame.height)

        let event = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: compactFrame.width / 2, y: compactFrame.height / 2),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: inputPanel.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1
        )!
        inputPanel.contentView?.mouseDown(with: event)
        let expandedFrame = inputPanel.frame
        precondition(expandedFrame.width > compactFrame.width)
        precondition(visualPanel.ignoresMouseEvents)

        let collapseEvent = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: expandedFrame.width / 2, y: expandedFrame.height / 2),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: inputPanel.windowNumber,
            context: nil,
            eventNumber: 2,
            clickCount: 1,
            pressure: 1
        )!
        inputPanel.contentView?.mouseDown(with: collapseEvent)
        precondition(inputPanel.frame == compactFrame)
        precondition(visualPanel.ignoresMouseEvents)
        precondition(!visualPanel.canBecomeKey)
        precondition(!visualPanel.canBecomeMain)
        precondition(!inputPanel.canBecomeKey)
        precondition(!inputPanel.canBecomeMain)
    }

    private static func testCompactClickKeepsTargetSizedVisualPanel() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let panel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: -100, y: -100)
        var tick: (() -> Void)?
        var inputPanelStarts = 0
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { panel },
            inputPanelFactory: {
                inputPanelStarts += 1
                return RecordingPanel(contentRect: .zero)
            },
            pointerProvider: { pointer },
            timerFactory: { _, handler in
                tick = handler
                return {}
            }
        )

        controller.apply(reasonCommand(trigger: .click, style: .pill)) { rendered in
            precondition(rendered)
        }
        let compactFrame = panel.frame
        precondition(inputPanelStarts == 0)
        precondition(panel.ignoresMouseEvents)

        pointer = NSPoint(x: compactFrame.midX, y: compactFrame.midY)
        tick?()
        precondition(!panel.ignoresMouseEvents)
        let event = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: compactFrame.width / 2, y: compactFrame.height / 2),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: panel.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1
        )!
        panel.contentView?.mouseDown(with: event)
        precondition(panel.frame.width == 340)
        precondition(inputPanelStarts == 0)
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

    private static func testFullScreenVisualPanelNeverConsumesStaleOutsideClick() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        var pointer = NSPoint(x: 740, y: 27)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { pointer },
            timerFactory: { _, _ in {} }
        )

        controller.apply(reasonCommand(trigger: .click, style: .border)) { rendered in
            precondition(rendered)
        }
        precondition(visualPanel.frame == screen.frame)
        precondition(visualPanel.ignoresMouseEvents)
        precondition(!inputPanel.ignoresMouseEvents)
        precondition(!inputPanel.canBecomeKey)
        precondition(!inputPanel.canBecomeMain)
        let compactInputFrame = inputPanel.frame
        precondition(compactInputFrame.width < screen.frame.width)
        precondition(compactInputFrame.height < screen.frame.height)

        let insideEvent = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: compactInputFrame.width / 2, y: compactInputFrame.height / 2),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: inputPanel.windowNumber,
            context: nil,
            eventNumber: 1,
            clickCount: 1,
            pressure: 1
        )!
        inputPanel.contentView?.mouseDown(with: insideEvent)
        let expandedInputFrame = inputPanel.frame
        precondition(expandedInputFrame.height > compactInputFrame.height)
        precondition(visualPanel.ignoresMouseEvents)

        pointer = NSPoint(x: 400, y: 300)
        precondition(!expandedInputFrame.contains(pointer))
        let outsideEvent = NSEvent.mouseEvent(
            with: .leftMouseDown,
            location: NSPoint(x: -20, y: -20),
            modifierFlags: [],
            timestamp: 0,
            windowNumber: inputPanel.windowNumber,
            context: nil,
            eventNumber: 2,
            clickCount: 1,
            pressure: 1
        )!
        inputPanel.contentView?.mouseDown(with: outsideEvent)
        precondition(inputPanel.frame == expandedInputFrame)
        precondition(visualPanel.ignoresMouseEvents)
    }

    private static func testBannerUsesOnlyCornerHitTarget() {
        let screen = OverlayScreenGeometry(
            id: 1,
            frame: NSRect(x: 0, y: 0, width: 800, height: 600),
            visibleFrame: NSRect(x: 0, y: 0, width: 800, height: 600)
        )
        let visualPanel = RecordingPanel(contentRect: .zero)
        let inputPanel = RecordingPanel(contentRect: .zero)
        let controller = PrivacyOverlayController(
            screenProvider: { [screen] },
            panelFactory: { visualPanel },
            inputPanelFactory: { inputPanel },
            pointerProvider: { NSPoint(x: 100, y: 585) },
            timerFactory: { _, _ in {} }
        )

        controller.apply(reasonCommand(trigger: .click, style: .banner)) { rendered in
            precondition(rendered)
        }
        precondition(visualPanel.frame == screen.frame)
        precondition(visualPanel.ignoresMouseEvents)
        precondition(!inputPanel.ignoresMouseEvents)
        precondition(inputPanel.frame.width < screen.frame.width)
        precondition(inputPanel.frame.height == 30)
        precondition(!inputPanel.frame.contains(NSPoint(x: 100, y: 585)))
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
