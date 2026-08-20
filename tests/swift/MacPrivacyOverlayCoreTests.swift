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
        print("MacPrivacyOverlayCoreTests passed")
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
            precondition(panels.allSatisfy { $0.displayIfNeededCount == 1 })
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
}
