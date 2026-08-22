import Foundation

struct WindowBounds: Equatable {
    let left: Double
    let top: Double
    let width: Double
    let height: Double
}

struct OnScreenCGWindow: Equatable {
    let windowID: UInt32?
    let ownerPID: Int32
    let layer: Int?
    let bounds: WindowBounds
    let title: String
}

struct AXWindowMetadata: Equatable {
    let windowID: UInt32?
    let ownerPID: Int32
    let isFocused: Bool

    init(windowID: UInt32?, ownerPID: Int32, isFocused: Bool = false) {
        self.windowID = windowID
        self.ownerPID = ownerPID
        self.isFocused = isFocused
    }
}

struct AXWindowMatchResolution: Equatable {
    let axIndexByCGIndex: [Int?]
}

struct ResolvedWindowMetadata: Equatable {
    let title: String
    let alternateTitle: String?
    let isActive: Bool
    let titleAvailable: Bool
    let isActiveCandidate: Bool

    init(
        title: String,
        alternateTitle: String? = nil,
        isActive: Bool,
        titleAvailable: Bool = true,
        isActiveCandidate: Bool = false
    ) {
        self.title = title
        self.alternateTitle = alternateTitle
        self.isActive = isActive
        self.titleAvailable = titleAvailable
        self.isActiveCandidate = isActiveCandidate
    }
}

private struct WindowIdentity: Hashable {
    let ownerPID: Int32
    let windowID: UInt32
}

private func windowIdentity(ownerPID: Int32, windowID: UInt32?) -> WindowIdentity? {
    guard ownerPID > 0, let windowID, windowID > 0 else { return nil }
    return WindowIdentity(ownerPID: ownerPID, windowID: windowID)
}

func resolveAXWindowMatches(
    cgWindows: [OnScreenCGWindow],
    axWindows: [AXWindowMetadata]
) -> AXWindowMatchResolution {
    var cgIndicesByIdentity: [WindowIdentity: [Int]] = [:]
    var cgWindowIDCounts: [UInt32: Int] = [:]
    for (index, window) in cgWindows.enumerated() {
        if let windowID = window.windowID, windowID > 0 {
            cgWindowIDCounts[windowID, default: 0] += 1
        }
        guard let identity = windowIdentity(ownerPID: window.ownerPID, windowID: window.windowID)
        else { continue }
        cgIndicesByIdentity[identity, default: []].append(index)
    }

    var axIndicesByIdentity: [WindowIdentity: [Int]] = [:]
    var axWindowIDCounts: [UInt32: Int] = [:]
    for (index, window) in axWindows.enumerated() {
        if let windowID = window.windowID, windowID > 0 {
            axWindowIDCounts[windowID, default: 0] += 1
        }
        guard let identity = windowIdentity(ownerPID: window.ownerPID, windowID: window.windowID)
        else { continue }
        axIndicesByIdentity[identity, default: []].append(index)
    }

    var axIndexByCGIndex = [Int?](repeating: nil, count: cgWindows.count)
    for (identity, cgIndices) in cgIndicesByIdentity where cgIndices.count == 1 {
        let cgIndex = cgIndices[0]
        guard
            cgWindows[cgIndex].layer == 0,
            cgWindowIDCounts[identity.windowID] == 1,
            let axIndices = axIndicesByIdentity[identity],
            axIndices.count == 1,
            axWindowIDCounts[identity.windowID] == 1
        else { continue }
        axIndexByCGIndex[cgIndex] = axIndices[0]
    }

    return AXWindowMatchResolution(
        axIndexByCGIndex: axIndexByCGIndex
    )
}

func resolvedWindowMetadata(
    cgWindows: [OnScreenCGWindow],
    axWindows: [AXWindowMetadata],
    frontmostPID: Int32? = nil,
    readAXTitle: (Int) -> String?
) -> [ResolvedWindowMetadata] {
    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)
    let focusedCGIndices = cgWindows.indices.filter { cgIndex in
        guard let axIndex = resolution.axIndexByCGIndex[cgIndex] else { return false }
        return axWindows[axIndex].isFocused
    }
    let activeCGIndex = focusedCGIndices.count == 1 ? focusedCGIndices[0] : nil

    var metadata: [ResolvedWindowMetadata] = []
    for cgIndex in cgWindows.indices {
        let cgWindow = cgWindows[cgIndex]
        let axIndex = resolution.axIndexByCGIndex[cgIndex]
        var title = cgWindow.title
        var alternateTitle: String?
        var titleAvailable = !title.isEmpty
        if let axIndex, let axTitle = readAXTitle(axIndex) {
            if title.isEmpty {
                title = axTitle
                titleAvailable = true
            } else if axTitle != title {
                alternateTitle = axTitle
            }
        }
        metadata.append(ResolvedWindowMetadata(
            title: title,
            alternateTitle: alternateTitle,
            isActive: cgIndex == activeCGIndex,
            titleAvailable: titleAvailable,
            isActiveCandidate: activeCGIndex == nil
                && cgWindow.layer == 0
                && cgWindow.ownerPID == frontmostPID
        ))
    }
    return metadata
}
