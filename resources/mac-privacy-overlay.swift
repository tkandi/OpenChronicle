import AppKit
import Foundation

private final class AcknowledgementWriter {
    private let lock = NSLock()
    private let encoder = JSONEncoder()

    func write(_ acknowledgement: OverlayAcknowledgement) {
        guard let data = try? encoder.encode(acknowledgement) else { return }
        lock.lock()
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        lock.unlock()
    }
}

private let acknowledgementWriter = AcknowledgementWriter()

func writeAcknowledgement(_ acknowledgement: OverlayAcknowledgement) {
    acknowledgementWriter.write(acknowledgement)
}

@main
enum MacPrivacyOverlay {
    static func main() {
        NSApplication.shared.setActivationPolicy(.accessory)
        let controller = PrivacyOverlayController()

        DispatchQueue.global(qos: .userInitiated).async {
            while let line = readLine() {
                do {
                    let command = try JSONDecoder().decode(OverlayCommand.self, from: Data(line.utf8))
                    DispatchQueue.main.async {
                        controller.applyWithWindowIDs(command) { rendered, windowIDs in
                            writeAcknowledgement(
                                OverlayAcknowledgement(
                                    generation: command.generation,
                                    rendered: rendered,
                                    error: rendered ? nil : "unresolved-window-id",
                                    windowIDs: rendered ? windowIDs : []
                                )
                            )
                        }
                    }
                } catch {
                    writeAcknowledgement(
                        OverlayAcknowledgement(
                            generation: -1,
                            rendered: false,
                            error: "invalid-command",
                            windowIDs: []
                        )
                    )
                }
            }
            DispatchQueue.main.async {
                NSApp.terminate(nil)
            }
        }

        NSApp.run()
    }
}
