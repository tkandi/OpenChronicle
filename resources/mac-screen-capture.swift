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

    let prepared: [PreparedCapture<SCContentFilter>]
    switch prepareCaptureSequence(
        command: command,
        displays: displaySources,
        windows: windowSources,
        prepareResource: { displayIndex, excludedWindowIndices in
            let excludedWindows = excludedWindowIndices.map { content.windows[$0] }
            let filter = SCContentFilter(
                display: content.displays[displayIndex],
                excludingWindows: excludedWindows
            )
            return .success((filter, Double(filter.pointPixelScale)))
        }
    ) {
    case let .success(sequence):
        prepared = sequence
    case let .failure(error):
        return .failure(error)
    }

    return await executePreparedCaptures(prepared) { filter, pixelSize in
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

        return .success(CapturedFrame(
            pixelWidth: image.width,
            pixelHeight: image.height,
            pngData: pngData
        ))
    }
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
