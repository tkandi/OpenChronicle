// mac-screen-capture - capture one filtered frame for each requested display.

import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

private func writeResponse(_ data: Data) {
    FileHandle.standardOutput.write(data)
}

private func readBoundedCommand() -> Data? {
    var command = Data()
    while command.count <= CaptureResourceLimits.maxCommandBytes {
        guard let chunk = try? FileHandle.standardInput.read(upToCount: 4096) else {
            return nil
        }
        guard !chunk.isEmpty else { return command }
        if let newline = chunk.firstIndex(of: 0x0a) {
            let prefix = chunk[..<newline]
            guard command.count + prefix.count <= CaptureResourceLimits.maxCommandBytes else {
                return nil
            }
            command.append(prefix)
            return command
        }
        guard command.count + chunk.count <= CaptureResourceLimits.maxCommandBytes else {
            return nil
        }
        command.append(chunk)
    }
    return nil
}

@available(macOS 14.0, *)
private func loadShareableContent() async -> SCShareableContent? {
    try? await SCShareableContent.excludingDesktopWindows(
        false,
        onScreenWindowsOnly: true
    )
}

@available(macOS 14.0, *)
private func applicationSource(_ application: SCRunningApplication) -> CaptureApplicationSource {
    CaptureApplicationSource(
        processID: application.processID,
        bundleIdentifier: application.bundleIdentifier,
        applicationName: application.applicationName
    )
}

@available(macOS 14.0, *)
private func displaySources(_ content: SCShareableContent) -> [CaptureDisplaySource] {
    content.displays.map { display in
        CaptureDisplaySource(
            id: display.displayID,
            left: display.frame.origin.x,
            top: display.frame.origin.y,
            pointWidth: display.frame.width,
            pointHeight: display.frame.height
        )
    }
}

@available(macOS 14.0, *)
private func windowSources(_ content: SCShareableContent) -> [CaptureWindowSource] {
    content.windows.map { window in
        CaptureWindowSource(
            id: window.windowID,
            owner: window.owningApplication.map(applicationSource),
            left: window.frame.origin.x,
            top: window.frame.origin.y,
            width: window.frame.width,
            height: window.frame.height,
            title: window.title
        )
    }
}

@available(macOS 14.0, *)
private func privacyFingerprint(
    _ content: SCShareableContent
) -> Result<CapturePrivacyFingerprint, CaptureErrorCode> {
    capturePrivacyFingerprint(
        displays: displaySources(content),
        windows: windowSources(content)
    )
}

@available(macOS 14.0, *)
private func captureDisplays(
    command: CaptureCommand
) async -> Result<[CapturedDisplay], CaptureErrorCode> {
    guard let content = await loadShareableContent() else {
        return .failure(.contentUnavailable)
    }
    let initialFingerprint: CapturePrivacyFingerprint
    switch privacyFingerprint(content) {
    case let .success(value):
        initialFingerprint = value
    case let .failure(error):
        return .failure(error)
    }
    let displays = displaySources(content)
    let windows = windowSources(content)
    let applications = content.applications.map(applicationSource)

    let prepared: [PreparedCapture<SCContentFilter>]
    switch prepareCaptureSequence(
        command: command,
        displays: displays,
        windows: windows,
        applications: applications,
        prepareResource: { displayIndex, excludedApplicationIndices in
            let excludedApplications = excludedApplicationIndices.map {
                content.applications[$0]
            }
            let filter = SCContentFilter(
                display: content.displays[displayIndex],
                excludingApplications: excludedApplications,
                exceptingWindows: []
            )
            return .success((filter, Double(filter.pointPixelScale)))
        }
    ) {
    case let .success(sequence):
        prepared = sequence
    case let .failure(error):
        return .failure(error)
    }

    return await executePreparedCaptures(
        prepared,
        initialFingerprint: initialFingerprint,
        capture: { filter, pixelSize in
            let configuration = SCStreamConfiguration()
            configuration.width = pixelSize.width
            configuration.height = pixelSize.height
            configuration.showsCursor = false
            configuration.scalesToFit = true

            let image: CGImage
            do {
                image = try await SCScreenshotManager.captureImage(
                    contentFilter: filter,
                    configuration: configuration
                )
            } catch {
                return .failure(.captureFailed)
            }
            guard image.width == pixelSize.width, image.height == pixelSize.height else {
                return .failure(.captureFailed)
            }
            return .success(CapturedFrame(
                pixelWidth: image.width,
                pixelHeight: image.height,
                payload: image
            ))
        },
        currentFingerprint: {
            guard let current = await loadShareableContent() else {
                return .failure(.contentUnavailable)
            }
            return privacyFingerprint(current)
        },
        encodePNG: encodePNG
    )
}

@available(macOS 14.0, *)
private func encodePNG(_ image: CGImage) -> Data? {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(
        data,
        UTType.png.identifier as CFString,
        1,
        nil
    ) else {
        return nil
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { return nil }
    return data as Data
}

@main
enum MacScreenCapture {
    static func main() async {
        let supportedOS: Bool
        if #available(macOS 14.0, *) {
            supportedOS = true
        } else {
            supportedOS = false
        }

        let input = readBoundedCommand() ?? Data()
        let command: CaptureCommand
        switch prepareCaptureCommand(input, supportedOS: supportedOS) {
        case let .success(parsed):
            command = parsed
        case let .failure(error):
            writeResponse(errorResponseLine(error))
            return
        }

        guard #available(macOS 14.0, *) else {
            writeResponse(errorResponseLine(.unsupportedOS))
            return
        }

        switch await captureDisplays(command: command) {
        case let .failure(error):
            writeResponse(errorResponseLine(error))
        case let .success(displays):
            switch encodeSuccessResponseLine(displays: displays) {
            case let .success(response):
                writeResponse(response)
            case let .failure(error):
                writeResponse(errorResponseLine(error))
            }
        }
    }
}
