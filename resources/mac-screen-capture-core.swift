import Foundation

enum CaptureErrorCode: String, Error, Equatable {
    case unsupportedOS = "unsupported_os"
    case invalidCommand = "invalid_command"
    case contentUnavailable = "content_unavailable"
    case displayNotFound = "display_not_found"
    case windowNotFound = "window_not_found"
    case ambiguousDisplay = "ambiguous_display"
    case ambiguousWindow = "ambiguous_window"
    case windowOwnerUnavailable = "window_owner_unavailable"
    case contentChanged = "content_changed"
    case captureFailed = "capture_failed"
    case encodeFailed = "encode_failed"
}

enum CaptureResourceLimits {
    static let maxCommandBytes = 65_536
    static let maxDisplayCount = 16
    static let maxDimension = 16_384
    static let maxAggregatePixels = 128_000_000
    static let maxPNGBytes = 67_108_864
    static let maxAggregatePNGBytes = 134_217_728
    static let maxResponseBytes = 188_743_680
    static let maxStderrBytes = 65_536
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

struct CaptureApplicationSource: Equatable, Hashable {
    let processID: Int32
    let bundleIdentifier: String
    let applicationName: String
}

struct CaptureWindowSource: Equatable {
    let id: UInt32
    let owner: CaptureApplicationSource?
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let title: String?
}

struct ResolvedCaptureTargets: Equatable {
    let displayIndices: [Int]
    let excludedApplicationIndices: [Int]
    let fingerprintScope: CapturePrivacyFingerprintScope
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

struct PreparedCapture<Resource> {
    let resource: Resource
    let source: CaptureDisplaySource
    let pixelSize: CapturePixelSize
    let excludedApplicationIndices: [Int]
    let fingerprintScope: CapturePrivacyFingerprintScope
}

struct CapturedFrame<Payload> {
    let pixelWidth: Int
    let pixelHeight: Int
    let payload: Payload
}

struct CapturePrivacyFingerprint: Equatable {
    fileprivate let displays: [CaptureDisplayFingerprint]
    fileprivate let windows: [CaptureWindowFingerprint]
}

struct CapturePrivacyFingerprintScope: Equatable {
    let requestedDisplayIDs: [UInt32]
    let excludedApplicationProcessIDs: [Int32]
}

private struct CaptureDisplayFingerprint: Equatable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double
}

private struct CaptureWindowFingerprint: Equatable {
    let id: UInt32
    let ownerPID: Int32?
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let title: String?
}

private struct PendingCapturedDisplay<Payload> {
    let source: CaptureDisplaySource
    let pixelSize: CapturePixelSize
    let payload: Payload
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
    guard data.count <= CaptureResourceLimits.maxCommandBytes else {
        return .failure(.invalidCommand)
    }
    var parser = StrictJSONParser(data: data)
    guard
        let root = try? parser.parse(),
        case let .object(rootMembers) = root,
        let payload = objectDictionary(rootMembers),
        Set(payload.keys) == captureCommandKeys,
        positiveInt(payload["version"]) == 1,
        case let .array(rawDisplays)? = payload["displays"],
        !rawDisplays.isEmpty,
        rawDisplays.count <= CaptureResourceLimits.maxDisplayCount,
        case let .array(rawProtectedWindowIDs)? = payload["protected_window_ids"],
        case let .array(rawOverlayWindowIDs)? = payload["overlay_window_ids"]
    else {
        return .failure(.invalidCommand)
    }

    var displays: [CaptureDisplayRequest] = []
    displays.reserveCapacity(rawDisplays.count)
    for rawDisplay in rawDisplays {
        guard
            case let .object(displayMembers) = rawDisplay,
            let display = objectDictionary(displayMembers),
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
                let height = positiveInt(display["height"]),
                width <= CaptureResourceLimits.maxDimension,
                height <= CaptureResourceLimits.maxDimension
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
    windows: [CaptureWindowSource],
    applications: [CaptureApplicationSource]
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
    let applicationIndicesByIdentity = indicesByIdentity(applications)
    let applicationIdentitiesByPID = Dictionary(grouping: applications, by: \.processID)
    let requestedWindowIDs = command.protectedWindowIDs + command.overlayWindowIDs
    var excludedApplicationIndices: [Int] = []
    var seenApplicationIndices: Set<Int> = []
    for id in requestedWindowIDs {
        let indices = windowIndicesByID[id] ?? []
        guard !indices.isEmpty else { return .failure(.windowNotFound) }
        guard indices.count == 1 else { return .failure(.ambiguousWindow) }
        guard
            let owner = windows[indices[0]].owner,
            isValidApplicationSource(owner),
            applicationIdentitiesByPID[owner.processID]?.allSatisfy({ $0 == owner }) == true,
            let ownerIndices = applicationIndicesByIdentity[owner],
            ownerIndices.count == 1
        else {
            return .failure(.windowOwnerUnavailable)
        }
        let ownerIndex = ownerIndices[0]
        if seenApplicationIndices.insert(ownerIndex).inserted {
            excludedApplicationIndices.append(ownerIndex)
        }
    }

    return .success(ResolvedCaptureTargets(
        displayIndices: displayIndices,
        excludedApplicationIndices: excludedApplicationIndices,
        fingerprintScope: CapturePrivacyFingerprintScope(
            requestedDisplayIDs: command.displays.map(\.id),
            excludedApplicationProcessIDs: excludedApplicationIndices.map {
                applications[$0].processID
            }
        )
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
        let size = CapturePixelSize(width: width, height: height)
        return capturePixelSizesAreWithinLimits([size])
            ? .success(size)
            : .failure(.invalidCommand)
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
    let size = CapturePixelSize(width: width, height: height)
    return capturePixelSizesAreWithinLimits([size])
        ? .success(size)
        : .failure(.contentUnavailable)
}

func prepareCaptureSequence<Resource>(
    command: CaptureCommand,
    displays: [CaptureDisplaySource],
    windows: [CaptureWindowSource],
    applications: [CaptureApplicationSource],
    prepareResource: (
        _ displayIndex: Int,
        _ excludedApplicationIndices: [Int]
    ) -> Result<(Resource, Double), CaptureErrorCode>
) -> Result<[PreparedCapture<Resource>], CaptureErrorCode> {
    let targets: ResolvedCaptureTargets
    switch resolveCaptureTargets(
        command: command,
        displays: displays,
        windows: windows,
        applications: applications
    ) {
    case let .success(resolved):
        targets = resolved
    case let .failure(error):
        return .failure(error)
    }

    var prepared: [PreparedCapture<Resource>] = []
    prepared.reserveCapacity(targets.displayIndices.count)
    for (requestIndex, displayIndex) in targets.displayIndices.enumerated() {
        let resource: Resource
        let pointPixelScale: Double
        switch prepareResource(displayIndex, targets.excludedApplicationIndices) {
        case let .success(value):
            (resource, pointPixelScale) = value
        case let .failure(error):
            return .failure(error)
        }

        let source = displays[displayIndex]
        let pixelSize: CapturePixelSize
        switch resolveOutputDimensions(
            request: command.displays[requestIndex],
            pointWidth: source.pointWidth,
            pointHeight: source.pointHeight,
            pointPixelScale: pointPixelScale
        ) {
        case let .success(size):
            pixelSize = size
        case let .failure(error):
            return .failure(error)
        }

        prepared.append(PreparedCapture(
            resource: resource,
            source: source,
            pixelSize: pixelSize,
            excludedApplicationIndices: targets.excludedApplicationIndices,
            fingerprintScope: targets.fingerprintScope
        ))
    }
    guard capturePixelSizesAreWithinLimits(prepared.map(\.pixelSize)) else {
        return .failure(.contentUnavailable)
    }
    return .success(prepared)
}

func executePreparedCaptures<Resource, Payload>(
    _ prepared: [PreparedCapture<Resource>],
    initialFingerprint: CapturePrivacyFingerprint,
    capture: (
        _ resource: Resource,
        _ pixelSize: CapturePixelSize
    ) async -> Result<CapturedFrame<Payload>, CaptureErrorCode>,
    currentFingerprint: () async -> Result<CapturePrivacyFingerprint, CaptureErrorCode>,
    encodePNG: (_ payload: Payload) -> Data?
) async -> Result<[CapturedDisplay], CaptureErrorCode> {
    guard
        !prepared.isEmpty,
        prepared.count <= CaptureResourceLimits.maxDisplayCount,
        capturePixelSizesAreWithinLimits(prepared.map(\.pixelSize))
    else {
        return .failure(.captureFailed)
    }
    var pending: [PendingCapturedDisplay<Payload>] = []
    pending.reserveCapacity(prepared.count)

    for item in prepared {
        let frame: CapturedFrame<Payload>
        switch await capture(item.resource, item.pixelSize) {
        case let .success(captured):
            frame = captured
        case let .failure(error):
            return .failure(error)
        }
        guard
            frame.pixelWidth == item.pixelSize.width,
            frame.pixelHeight == item.pixelSize.height
        else {
            return .failure(.captureFailed)
        }
        pending.append(PendingCapturedDisplay(
            source: item.source,
            pixelSize: item.pixelSize,
            payload: frame.payload
        ))
    }

    let postCaptureFingerprint: CapturePrivacyFingerprint
    switch await currentFingerprint() {
    case let .success(value):
        postCaptureFingerprint = value
    case let .failure(error):
        return .failure(error)
    }
    guard postCaptureFingerprint == initialFingerprint else {
        return .failure(.contentChanged)
    }

    var displays: [CapturedDisplay] = []
    var pngByteCounts: [Int] = []
    displays.reserveCapacity(pending.count)
    pngByteCounts.reserveCapacity(pending.count)
    for item in pending {
        guard let pngData = encodePNG(item.payload), !pngData.isEmpty else {
            return .failure(.encodeFailed)
        }
        pngByteCounts.append(pngData.count)
        guard capturePNGByteCountsAreWithinLimits(pngByteCounts) else {
            return .failure(.encodeFailed)
        }
        displays.append(CapturedDisplay(
            id: item.source.id,
            left: item.source.left,
            top: item.source.top,
            pointWidth: item.source.pointWidth,
            pointHeight: item.source.pointHeight,
            pixelWidth: item.pixelSize.width,
            pixelHeight: item.pixelSize.height,
            pngData: pngData
        ))
    }
    return .success(displays)
}

func capturePrivacyFingerprint(
    displays: [CaptureDisplaySource],
    windows: [CaptureWindowSource],
    scope: CapturePrivacyFingerprintScope
) -> Result<CapturePrivacyFingerprint, CaptureErrorCode> {
    guard
        !scope.requestedDisplayIDs.isEmpty,
        scope.requestedDisplayIDs.allSatisfy({ $0 > 0 }),
        Set(scope.requestedDisplayIDs).count == scope.requestedDisplayIDs.count,
        !scope.excludedApplicationProcessIDs.isEmpty,
        scope.excludedApplicationProcessIDs.allSatisfy({ $0 > 0 }),
        Set(scope.excludedApplicationProcessIDs).count == scope.excludedApplicationProcessIDs.count
    else {
        return .failure(.contentUnavailable)
    }

    var displayFingerprint: [CaptureDisplayFingerprint] = []
    displayFingerprint.reserveCapacity(scope.requestedDisplayIDs.count)
    for id in scope.requestedDisplayIDs {
        let matchingDisplays = displays.filter { $0.id == id }
        guard matchingDisplays.count == 1, let display = matchingDisplays.first,
              isRepresentableGeometry(display)
        else {
            return .failure(.contentUnavailable)
        }
        displayFingerprint.append(CaptureDisplayFingerprint(
            id: display.id,
            left: display.left,
            top: display.top,
            width: display.pointWidth,
            height: display.pointHeight
        ))
    }
    displayFingerprint.sort(by: displayFingerprintLessThan)

    var windowFingerprint: [CaptureWindowFingerprint] = []
    windowFingerprint.reserveCapacity(windows.count)
    let excludedApplicationProcessIDs = Set(scope.excludedApplicationProcessIDs)
    for window in windows {
        guard
            let owner = window.owner,
            excludedApplicationProcessIDs.contains(owner.processID)
        else {
            continue
        }
        guard
            window.id > 0,
            isValidApplicationSource(owner),
            isRepresentableCoordinate(window.left),
            isRepresentableCoordinate(window.top),
            window.width.isFinite,
            window.height.isFinite,
            window.width >= 0,
            window.height >= 0
        else {
            return .failure(.contentUnavailable)
        }
        windowFingerprint.append(CaptureWindowFingerprint(
            id: window.id,
            ownerPID: owner.processID,
            left: window.left,
            top: window.top,
            width: window.width,
            height: window.height,
            title: window.title
        ))
    }
    windowFingerprint.sort(by: windowFingerprintLessThan)
    return .success(CapturePrivacyFingerprint(
        displays: displayFingerprint,
        windows: windowFingerprint
    ))
}

func capturePixelSizesAreWithinLimits(_ sizes: [CapturePixelSize]) -> Bool {
    guard sizes.count <= CaptureResourceLimits.maxDisplayCount else { return false }
    var aggregatePixels = 0
    for size in sizes {
        guard
            size.width > 0,
            size.height > 0,
            size.width <= CaptureResourceLimits.maxDimension,
            size.height <= CaptureResourceLimits.maxDimension
        else {
            return false
        }
        let (pixels, multiplicationOverflow) = size.width.multipliedReportingOverflow(
            by: size.height
        )
        guard !multiplicationOverflow else { return false }
        let (newAggregate, additionOverflow) = aggregatePixels.addingReportingOverflow(pixels)
        guard
            !additionOverflow,
            newAggregate <= CaptureResourceLimits.maxAggregatePixels
        else {
            return false
        }
        aggregatePixels = newAggregate
    }
    return true
}

func capturePNGByteCountsAreWithinLimits(_ counts: [Int]) -> Bool {
    guard counts.count <= CaptureResourceLimits.maxDisplayCount else { return false }
    var aggregateBytes = 0
    var aggregateBase64Bytes = 0
    for count in counts {
        guard count > 0, count <= CaptureResourceLimits.maxPNGBytes else { return false }
        let (newAggregate, additionOverflow) = aggregateBytes.addingReportingOverflow(count)
        guard
            !additionOverflow,
            newAggregate <= CaptureResourceLimits.maxAggregatePNGBytes,
            let base64Bytes = estimatedBase64Length(count)
        else {
            return false
        }
        let (newBase64Aggregate, base64Overflow) = aggregateBase64Bytes.addingReportingOverflow(
            base64Bytes
        )
        guard
            !base64Overflow,
            newBase64Aggregate < CaptureResourceLimits.maxResponseBytes
        else {
            return false
        }
        aggregateBytes = newAggregate
        aggregateBase64Bytes = newBase64Aggregate
    }
    return true
}

func estimatedBase64Length(_ byteCount: Int) -> Int? {
    guard byteCount >= 0 else { return nil }
    let (adjusted, additionOverflow) = byteCount.addingReportingOverflow(2)
    guard !additionOverflow else { return nil }
    let groups = adjusted / 3
    let (encoded, multiplicationOverflow) = groups.multipliedReportingOverflow(by: 4)
    return multiplicationOverflow ? nil : encoded
}

func errorResponseLine(_ code: CaptureErrorCode) -> Data {
    Data("{\"version\":1,\"status\":\"error\",\"error\":\"\(code.rawValue)\"}\n".utf8)
}

func encodeSuccessResponseLine(
    displays: [CapturedDisplay]
) -> Result<Data, CaptureErrorCode> {
    guard
        !displays.isEmpty,
        displays.count <= CaptureResourceLimits.maxDisplayCount,
        capturePixelSizesAreWithinLimits(displays.map {
            CapturePixelSize(width: $0.pixelWidth, height: $0.pixelHeight)
        }),
        capturePNGByteCountsAreWithinLimits(displays.map { $0.pngData.count })
    else {
        return .failure(.encodeFailed)
    }
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
        guard data.count <= CaptureResourceLimits.maxResponseBytes else {
            return .failure(.encodeFailed)
        }
        return .success(data)
    } catch {
        return .failure(.encodeFailed)
    }
}

private func isValidCaptureCommand(_ command: CaptureCommand) -> Bool {
    guard
        command.version == 1,
        !command.displays.isEmpty,
        command.displays.count <= CaptureResourceLimits.maxDisplayCount
    else {
        return false
    }
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
        return width > 0
            && height > 0
            && width <= CaptureResourceLimits.maxDimension
            && height <= CaptureResourceLimits.maxDimension
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

private func indicesByIdentity(
    _ applications: [CaptureApplicationSource]
) -> [CaptureApplicationSource: [Int]] {
    var result: [CaptureApplicationSource: [Int]] = [:]
    for (index, application) in applications.enumerated() {
        result[application, default: []].append(index)
    }
    return result
}

private func isValidApplicationSource(_ application: CaptureApplicationSource) -> Bool {
    application.processID > 0
        && !application.bundleIdentifier.isEmpty
        && !application.applicationName.isEmpty
}

private func displayFingerprintLessThan(
    _ left: CaptureDisplayFingerprint,
    _ right: CaptureDisplayFingerprint
) -> Bool {
    if left.id != right.id { return left.id < right.id }
    if left.left != right.left { return left.left < right.left }
    if left.top != right.top { return left.top < right.top }
    if left.width != right.width { return left.width < right.width }
    return left.height < right.height
}

private func windowFingerprintLessThan(
    _ left: CaptureWindowFingerprint,
    _ right: CaptureWindowFingerprint
) -> Bool {
    if left.id != right.id { return left.id < right.id }
    if left.ownerPID != right.ownerPID {
        return (left.ownerPID ?? Int32.min) < (right.ownerPID ?? Int32.min)
    }
    if left.left != right.left { return left.left < right.left }
    if left.top != right.top { return left.top < right.top }
    if left.width != right.width { return left.width < right.width }
    if left.height != right.height { return left.height < right.height }
    return (left.title ?? "") < (right.title ?? "")
}

private func positiveUInt32Array(_ values: [StrictJSONValue]) -> [UInt32]? {
    var result: [UInt32] = []
    result.reserveCapacity(values.count)
    for value in values {
        guard let parsed = positiveUInt32(value) else { return nil }
        result.append(parsed)
    }
    return result
}

private func positiveUInt32(_ value: StrictJSONValue?) -> UInt32? {
    guard case let .number(token)? = value, isPlainIntegerToken(token) else { return nil }
    guard let parsed = UInt32(token), parsed > 0 else { return nil }
    return parsed
}

private func positiveInt(_ value: StrictJSONValue?) -> Int? {
    guard case let .number(token)? = value, isPlainIntegerToken(token) else { return nil }
    guard let parsed = Int(token), parsed > 0 else { return nil }
    return parsed
}

private func isPlainIntegerToken(_ token: String) -> Bool {
    !token.contains(".") && !token.contains("e") && !token.contains("E")
}

private func objectDictionary(
    _ members: [(String, StrictJSONValue)]
) -> [String: StrictJSONValue]? {
    var object: [String: StrictJSONValue] = [:]
    for (key, value) in members {
        object[key] = value
    }
    return object
}

private indirect enum StrictJSONValue {
    case object([(String, StrictJSONValue)])
    case array([StrictJSONValue])
    case string(String)
    case number(String)
    case bool(Bool)
    case null
}

private enum StrictJSONError: Error {
    case invalid
}

private struct StrictJSONParser {
    private let bytes: [UInt8]
    private var index = 0

    init(data: Data) {
        bytes = Array(data)
    }

    mutating func parse() throws -> StrictJSONValue {
        skipWhitespace()
        let value = try parseValue(depth: 0)
        skipWhitespace()
        guard index == bytes.count else { throw StrictJSONError.invalid }
        return value
    }

    private mutating func parseValue(depth: Int) throws -> StrictJSONValue {
        guard depth <= 16, let byte = currentByte else { throw StrictJSONError.invalid }
        switch byte {
        case 0x7b:
            return try parseObject(depth: depth + 1)
        case 0x5b:
            return try parseArray(depth: depth + 1)
        case 0x22:
            return .string(try parseString())
        case 0x74:
            try consumeLiteral("true")
            return .bool(true)
        case 0x66:
            try consumeLiteral("false")
            return .bool(false)
        case 0x6e:
            try consumeLiteral("null")
            return .null
        case 0x2d, 0x30...0x39:
            return .number(try parseNumber())
        default:
            throw StrictJSONError.invalid
        }
    }

    private mutating func parseObject(depth: Int) throws -> StrictJSONValue {
        try consume(0x7b)
        skipWhitespace()
        if consumeIfPresent(0x7d) { return .object([]) }

        var members: [(String, StrictJSONValue)] = []
        var decodedKeys: Set<String> = []
        while true {
            guard currentByte == 0x22 else { throw StrictJSONError.invalid }
            let key = try parseString()
            guard decodedKeys.insert(key).inserted else { throw StrictJSONError.invalid }
            skipWhitespace()
            try consume(0x3a)
            skipWhitespace()
            let value = try parseValue(depth: depth)
            members.append((key, value))
            skipWhitespace()
            if consumeIfPresent(0x7d) { return .object(members) }
            try consume(0x2c)
            skipWhitespace()
        }
    }

    private mutating func parseArray(depth: Int) throws -> StrictJSONValue {
        try consume(0x5b)
        skipWhitespace()
        if consumeIfPresent(0x5d) { return .array([]) }

        var values: [StrictJSONValue] = []
        while true {
            values.append(try parseValue(depth: depth))
            skipWhitespace()
            if consumeIfPresent(0x5d) { return .array(values) }
            try consume(0x2c)
            skipWhitespace()
        }
    }

    private mutating func parseString() throws -> String {
        let start = index
        try consume(0x22)
        var escaped = false
        while let byte = currentByte {
            if byte < 0x20 { throw StrictJSONError.invalid }
            index += 1
            if escaped {
                escaped = false
                continue
            }
            if byte == 0x5c {
                escaped = true
                continue
            }
            if byte == 0x22 {
                let token = Data(bytes[start..<index])
                guard
                    let decoded = try? JSONSerialization.jsonObject(
                        with: token,
                        options: .fragmentsAllowed
                    ),
                    let string = decoded as? String
                else {
                    throw StrictJSONError.invalid
                }
                return string
            }
        }
        throw StrictJSONError.invalid
    }

    private mutating func parseNumber() throws -> String {
        let start = index
        if consumeIfPresent(0x2d), currentByte == nil { throw StrictJSONError.invalid }

        if consumeIfPresent(0x30) {
            if let byte = currentByte, isDigit(byte) { throw StrictJSONError.invalid }
        } else {
            guard let byte = currentByte, byte >= 0x31, byte <= 0x39 else {
                throw StrictJSONError.invalid
            }
            consumeDigits()
        }

        if consumeIfPresent(0x2e) {
            guard let byte = currentByte, isDigit(byte) else { throw StrictJSONError.invalid }
            consumeDigits()
        }
        if consumeIfPresent(0x65) || consumeIfPresent(0x45) {
            _ = consumeIfPresent(0x2b) || consumeIfPresent(0x2d)
            guard let byte = currentByte, isDigit(byte) else { throw StrictJSONError.invalid }
            consumeDigits()
        }
        return String(decoding: bytes[start..<index], as: UTF8.self)
    }

    private mutating func consumeLiteral(_ literal: StaticString) throws {
        let expected = Array(String(describing: literal).utf8)
        guard index + expected.count <= bytes.count else { throw StrictJSONError.invalid }
        guard Array(bytes[index..<(index + expected.count)]) == expected else {
            throw StrictJSONError.invalid
        }
        index += expected.count
    }

    private mutating func consume(_ byte: UInt8) throws {
        guard consumeIfPresent(byte) else { throw StrictJSONError.invalid }
    }

    private mutating func consumeIfPresent(_ byte: UInt8) -> Bool {
        guard currentByte == byte else { return false }
        index += 1
        return true
    }

    private mutating func consumeDigits() {
        while let byte = currentByte, isDigit(byte) {
            index += 1
        }
    }

    private mutating func skipWhitespace() {
        while let byte = currentByte, byte == 0x20 || byte == 0x09 || byte == 0x0a || byte == 0x0d {
            index += 1
        }
    }

    private var currentByte: UInt8? {
        index < bytes.count ? bytes[index] : nil
    }

    private func isDigit(_ byte: UInt8) -> Bool {
        byte >= 0x30 && byte <= 0x39
    }
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
