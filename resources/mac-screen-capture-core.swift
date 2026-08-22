import CoreFoundation
import Foundation

enum CaptureErrorCode: String, Error, Equatable {
    case unsupportedOS = "unsupported_os"
    case invalidCommand = "invalid_command"
    case contentUnavailable = "content_unavailable"
    case displayNotFound = "display_not_found"
    case windowNotFound = "window_not_found"
    case ambiguousDisplay = "ambiguous_display"
    case ambiguousWindow = "ambiguous_window"
    case captureFailed = "capture_failed"
    case encodeFailed = "encode_failed"
}

struct CaptureDisplayRequest: Equatable {
    let id: UInt32
    let width: Int?
    let height: Int?
}

struct CaptureCommand: Equatable {
    let version: Int
    let displays: [CaptureDisplayRequest]
    let protectedWindowIDs: [UInt32]
    let overlayWindowIDs: [UInt32]
}

struct CaptureDisplaySource: Equatable {
    let id: UInt32
    let left: Double
    let top: Double
    let pointWidth: Double
    let pointHeight: Double
}

struct CaptureWindowSource: Equatable {
    let id: UInt32
}

struct ResolvedCaptureTargets: Equatable {
    let displayIndices: [Int]
    let excludedWindowIndices: [Int]
}

struct CapturePixelSize: Equatable {
    let width: Int
    let height: Int
}

struct CapturedDisplay: Equatable {
    let id: UInt32
    let left: Double
    let top: Double
    let pointWidth: Double
    let pointHeight: Double
    let pixelWidth: Int
    let pixelHeight: Int
    let pngData: Data
}

private let captureCommandKeys: Set<String> = [
    "version", "displays", "protected_window_ids", "overlay_window_ids",
]
private let nativeDisplayRequestKeys: Set<String> = ["id"]
private let sizedDisplayRequestKeys: Set<String> = ["id", "width", "height"]

func prepareCaptureCommand(
    _ data: Data,
    supportedOS: Bool
) -> Result<CaptureCommand, CaptureErrorCode> {
    guard supportedOS else { return .failure(.unsupportedOS) }
    guard
        let object = try? JSONSerialization.jsonObject(with: data),
        let payload = object as? [String: Any],
        Set(payload.keys) == captureCommandKeys,
        positiveInt(payload["version"]) == 1,
        let rawDisplays = payload["displays"] as? [Any],
        !rawDisplays.isEmpty,
        let rawProtectedWindowIDs = payload["protected_window_ids"] as? [Any],
        let rawOverlayWindowIDs = payload["overlay_window_ids"] as? [Any]
    else {
        return .failure(.invalidCommand)
    }

    var displays: [CaptureDisplayRequest] = []
    displays.reserveCapacity(rawDisplays.count)
    for rawDisplay in rawDisplays {
        guard
            let display = rawDisplay as? [String: Any],
            Set(display.keys) == nativeDisplayRequestKeys
                || Set(display.keys) == sizedDisplayRequestKeys,
            let id = positiveUInt32(display["id"])
        else {
            return .failure(.invalidCommand)
        }

        if Set(display.keys) == nativeDisplayRequestKeys {
            displays.append(CaptureDisplayRequest(id: id, width: nil, height: nil))
        } else {
            guard
                let width = positiveInt(display["width"]),
                let height = positiveInt(display["height"])
            else {
                return .failure(.invalidCommand)
            }
            displays.append(CaptureDisplayRequest(id: id, width: width, height: height))
        }
    }

    guard
        let protectedWindowIDs = positiveUInt32Array(rawProtectedWindowIDs),
        let overlayWindowIDs = positiveUInt32Array(rawOverlayWindowIDs)
    else {
        return .failure(.invalidCommand)
    }

    let command = CaptureCommand(
        version: 1,
        displays: displays,
        protectedWindowIDs: protectedWindowIDs,
        overlayWindowIDs: overlayWindowIDs
    )
    guard isValidCaptureCommand(command) else { return .failure(.invalidCommand) }
    return .success(command)
}

func resolveCaptureTargets(
    command: CaptureCommand,
    displays: [CaptureDisplaySource],
    windows: [CaptureWindowSource]
) -> Result<ResolvedCaptureTargets, CaptureErrorCode> {
    guard isValidCaptureCommand(command) else { return .failure(.invalidCommand) }

    let displayIndicesByID = indicesByID(displays.map(\.id))
    var displayIndices: [Int] = []
    displayIndices.reserveCapacity(command.displays.count)
    for request in command.displays {
        let indices = displayIndicesByID[request.id] ?? []
        guard !indices.isEmpty else { return .failure(.displayNotFound) }
        guard indices.count == 1 else { return .failure(.ambiguousDisplay) }
        let index = indices[0]
        guard isRepresentableGeometry(displays[index]) else {
            return .failure(.contentUnavailable)
        }
        displayIndices.append(index)
    }

    let windowIndicesByID = indicesByID(windows.map(\.id))
    let requestedWindowIDs = command.protectedWindowIDs + command.overlayWindowIDs
    var excludedWindowIndices: [Int] = []
    excludedWindowIndices.reserveCapacity(requestedWindowIDs.count)
    for id in requestedWindowIDs {
        let indices = windowIndicesByID[id] ?? []
        guard !indices.isEmpty else { return .failure(.windowNotFound) }
        guard indices.count == 1 else { return .failure(.ambiguousWindow) }
        excludedWindowIndices.append(indices[0])
    }

    return .success(ResolvedCaptureTargets(
        displayIndices: displayIndices,
        excludedWindowIndices: excludedWindowIndices
    ))
}

func resolveOutputDimensions(
    request: CaptureDisplayRequest,
    pointWidth: Double,
    pointHeight: Double,
    pointPixelScale: Double
) -> Result<CapturePixelSize, CaptureErrorCode> {
    guard isValidDisplayRequest(request) else { return .failure(.invalidCommand) }
    if let width = request.width, let height = request.height {
        return .success(CapturePixelSize(width: width, height: height))
    }

    guard
        isPositiveRepresentable(pointWidth),
        isPositiveRepresentable(pointHeight),
        pointPixelScale.isFinite,
        pointPixelScale > 0,
        let width = roundedPositiveInt(pointWidth * pointPixelScale),
        let height = roundedPositiveInt(pointHeight * pointPixelScale)
    else {
        return .failure(.contentUnavailable)
    }
    return .success(CapturePixelSize(width: width, height: height))
}

func errorResponseLine(_ code: CaptureErrorCode) -> Data {
    Data("{\"version\":1,\"status\":\"error\",\"error\":\"\(code.rawValue)\"}\n".utf8)
}

func encodeSuccessResponseLine(
    displays: [CapturedDisplay]
) -> Result<Data, CaptureErrorCode> {
    guard !displays.isEmpty else { return .failure(.encodeFailed) }
    let ids = displays.map(\.id)
    guard Set(ids).count == ids.count else { return .failure(.encodeFailed) }
    guard displays.allSatisfy(isValidCapturedDisplay) else { return .failure(.encodeFailed) }

    let payload = CaptureSuccessResponse(
        version: 1,
        status: "ok",
        displays: displays.map { display in
            CaptureDisplayResponse(
                id: display.id,
                left: display.left,
                top: display.top,
                pointWidth: display.pointWidth,
                pointHeight: display.pointHeight,
                pixelWidth: display.pixelWidth,
                pixelHeight: display.pixelHeight,
                pngBase64: display.pngData.base64EncodedString()
            )
        }
    )

    do {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var data = try encoder.encode(payload)
        data.append(0x0a)
        return .success(data)
    } catch {
        return .failure(.encodeFailed)
    }
}

private func isValidCaptureCommand(_ command: CaptureCommand) -> Bool {
    guard command.version == 1, !command.displays.isEmpty else { return false }
    guard command.displays.allSatisfy(isValidDisplayRequest) else { return false }
    let displayIDs = command.displays.map(\.id)
    guard Set(displayIDs).count == displayIDs.count else { return false }

    guard !command.protectedWindowIDs.isEmpty else { return false }
    guard command.protectedWindowIDs.allSatisfy({ $0 > 0 }) else { return false }
    guard command.overlayWindowIDs.allSatisfy({ $0 > 0 }) else { return false }
    guard Set(command.protectedWindowIDs).count == command.protectedWindowIDs.count else {
        return false
    }
    guard Set(command.overlayWindowIDs).count == command.overlayWindowIDs.count else {
        return false
    }
    return Set(command.protectedWindowIDs).isDisjoint(with: command.overlayWindowIDs)
}

private func isValidDisplayRequest(_ request: CaptureDisplayRequest) -> Bool {
    guard request.id > 0 else { return false }
    switch (request.width, request.height) {
    case (nil, nil):
        return true
    case let (width?, height?):
        return width > 0 && height > 0
    default:
        return false
    }
}

private func isRepresentableGeometry(_ display: CaptureDisplaySource) -> Bool {
    display.id > 0
        && isRepresentableCoordinate(display.left)
        && isRepresentableCoordinate(display.top)
        && isPositiveRepresentable(display.pointWidth)
        && isPositiveRepresentable(display.pointHeight)
}

private func isValidCapturedDisplay(_ display: CapturedDisplay) -> Bool {
    display.id > 0
        && isRepresentableCoordinate(display.left)
        && isRepresentableCoordinate(display.top)
        && isPositiveRepresentable(display.pointWidth)
        && isPositiveRepresentable(display.pointHeight)
        && display.pixelWidth > 0
        && display.pixelHeight > 0
        && !display.pngData.isEmpty
}

private func isRepresentableCoordinate(_ value: Double) -> Bool {
    value.isFinite && abs(value) < Double(Int.max)
}

private func isPositiveRepresentable(_ value: Double) -> Bool {
    value.isFinite && value > 0 && value < Double(Int.max)
}

private func roundedPositiveInt(_ value: Double) -> Int? {
    guard value.isFinite, value > 0, value < Double(Int.max) else { return nil }
    let rounded = value.rounded(.toNearestOrAwayFromZero)
    guard rounded >= 1, rounded < Double(Int.max) else { return nil }
    return Int(rounded)
}

private func indicesByID(_ ids: [UInt32]) -> [UInt32: [Int]] {
    var result: [UInt32: [Int]] = [:]
    for (index, id) in ids.enumerated() {
        result[id, default: []].append(index)
    }
    return result
}

private func positiveUInt32Array(_ values: [Any]) -> [UInt32]? {
    var result: [UInt32] = []
    result.reserveCapacity(values.count)
    for value in values {
        guard let parsed = positiveUInt32(value) else { return nil }
        result.append(parsed)
    }
    return result
}

private func positiveUInt32(_ value: Any?) -> UInt32? {
    guard let number = jsonNumber(value), isIntegral(number) else { return nil }
    guard
        number.compare(NSNumber(value: 1)) != .orderedAscending,
        number.compare(NSNumber(value: UInt32.max)) != .orderedDescending
    else {
        return nil
    }
    return UInt32(number.uint64Value)
}

private func positiveInt(_ value: Any?) -> Int? {
    guard let number = jsonNumber(value), isIntegral(number) else { return nil }
    guard
        number.compare(NSNumber(value: 1)) != .orderedAscending,
        number.compare(NSNumber(value: Int.max)) != .orderedDescending
    else {
        return nil
    }
    return Int(number.int64Value)
}

private func jsonNumber(_ value: Any?) -> NSNumber? {
    guard let number = value as? NSNumber else { return nil }
    guard CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
    return number
}

private func isIntegral(_ number: NSNumber) -> Bool {
    var value = number.decimalValue
    guard !value.isNaN else { return false }
    var integral = Decimal()
    NSDecimalRound(&integral, &value, 0, .down)
    return integral == value
}

private struct CaptureSuccessResponse: Encodable {
    let version: Int
    let status: String
    let displays: [CaptureDisplayResponse]
}

private struct CaptureDisplayResponse: Encodable {
    let id: UInt32
    let left: Double
    let top: Double
    let pointWidth: Double
    let pointHeight: Double
    let pixelWidth: Int
    let pixelHeight: Int
    let pngBase64: String

    enum CodingKeys: String, CodingKey {
        case id
        case left
        case top
        case pointWidth = "point_width"
        case pointHeight = "point_height"
        case pixelWidth = "pixel_width"
        case pixelHeight = "pixel_height"
        case pngBase64 = "png_base64"
    }
}
