// mac-screen-capture - capture one filtered frame for each requested display.

import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

private func writeResponse(_ data: Data) {
    FileHandle.standardOutput.write(data)
}

@available(macOS 14.0, *)
private func captureDisplays(
    command: CaptureCommand
) async -> Result<[CapturedDisplay], CaptureErrorCode> {
    let content: SCShareableContent
    do {
        content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
    } catch {
        return .failure(.contentUnavailable)
    }

    let displaySources = content.displays.map { display in
        CaptureDisplaySource(
            id: display.displayID,
            left: display.frame.origin.x,
            top: display.frame.origin.y,
            pointWidth: display.frame.width,
            pointHeight: display.frame.height
        )
    }
    let windowSources = content.windows.map { CaptureWindowSource(id: $0.windowID) }

    let targets: ResolvedCaptureTargets
    switch resolveCaptureTargets(
        command: command,
        displays: displaySources,
        windows: windowSources
    ) {
    case let .success(resolved):
        targets = resolved
    case let .failure(error):
        return .failure(error)
    }

    let excludedWindows = targets.excludedWindowIndices.map { content.windows[$0] }
    var capturedDisplays: [CapturedDisplay] = []
    capturedDisplays.reserveCapacity(targets.displayIndices.count)

    for (requestIndex, displayIndex) in targets.displayIndices.enumerated() {
        let display = content.displays[displayIndex]
        let source = displaySources[displayIndex]

        // This is the only capture filter in the helper. Reaching it requires
        // unique resolution of every requested display and excluded window.
        let filter = SCContentFilter(display: display, excludingWindows: excludedWindows)
        let pixelSize: CapturePixelSize
        switch resolveOutputDimensions(
            request: command.displays[requestIndex],
            pointWidth: source.pointWidth,
            pointHeight: source.pointHeight,
            pointPixelScale: Double(filter.pointPixelScale)
        ) {
        case let .success(size):
            pixelSize = size
        case let .failure(error):
            return .failure(error)
        }

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
        guard let pngData = encodePNG(image) else { return .failure(.encodeFailed) }

        capturedDisplays.append(CapturedDisplay(
            id: source.id,
            left: source.left,
            top: source.top,
            pointWidth: source.pointWidth,
            pointHeight: source.pointHeight,
            pixelWidth: image.width,
            pixelHeight: image.height,
            pngData: pngData
        ))
    }

    return .success(capturedDisplays)
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

        let input = readLine().map { Data($0.utf8) } ?? Data()
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
