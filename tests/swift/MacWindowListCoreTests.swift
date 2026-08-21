import Foundation

private func expectMatches(
    _ resolution: AXWindowMatchResolution,
    _ expected: [Int?],
    file: StaticString = #file,
    line: UInt = #line
) {
    precondition(resolution.axIndexByCGIndex == expected, file: file, line: line)
}

private func testExactIdentityAuthorizesNormalLayerFallback() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 41,
            ownerPID: 500,
            layer: 0,
            bounds: WindowBounds(left: -80, top: 10, width: 300, height: 200),
            title: ""
        )
    ]
    let axWindows = [AXWindowMetadata(windowID: 41, ownerPID: 500, title: "InPrivate")]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [0])
    precondition(resolution.titleFallbackComplete)
}

private func testNonWindowLayersCannotAuthorizeFallback() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 42,
            ownerPID: 500,
            layer: 3,
            bounds: WindowBounds(left: 0, top: 0, width: 200, height: 100),
            title: ""
        )
    ]
    let axWindows = [AXWindowMetadata(windowID: 42, ownerPID: 500, title: "InPrivate")]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil])
    precondition(resolution.titleFallbackComplete)
}

private func testPIDAndWindowIDMustBothMatch() {
    let bounds = WindowBounds(left: 100, top: 20, width: 300, height: 200)
    let cgWindows = [
        OnScreenCGWindow(windowID: 43, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 44, ownerPID: 600, layer: 0, bounds: bounds, title: ""),
    ]
    let axWindows = [
        AXWindowMetadata(windowID: 43, ownerPID: 600, title: "wrong-pid"),
        AXWindowMetadata(windowID: 99, ownerPID: 500, title: "other-space"),
    ]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil, nil])
    precondition(!resolution.titleFallbackComplete)
}

private func testSameGeometryDifferentSpaceWindowIsRejected() {
    let bounds = WindowBounds(left: 100, top: 20, width: 300, height: 200)
    let cgWindows = [
        OnScreenCGWindow(windowID: 45, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let axWindows = [
        AXWindowMetadata(windowID: 46, ownerPID: 500, title: "other-space-inprivate")
    ]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil])
    precondition(!resolution.titleFallbackComplete)
}

private func testDuplicateIdentitiesNeverCreateManyToOneMatches() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let duplicateCG = [
        OnScreenCGWindow(windowID: 47, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 47, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
    ]
    let oneAXWindow = [AXWindowMetadata(windowID: 47, ownerPID: 500, title: "InPrivate")]
    let duplicateAX = [
        AXWindowMetadata(windowID: 48, ownerPID: 500, title: "first"),
        AXWindowMetadata(windowID: 48, ownerPID: 500, title: "second"),
    ]
    let oneCGWindow = [
        OnScreenCGWindow(windowID: 48, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]

    let duplicateCGResolution = resolveAXWindowMatches(
        cgWindows: duplicateCG,
        axWindows: oneAXWindow
    )
    let duplicateAXResolution = resolveAXWindowMatches(
        cgWindows: oneCGWindow,
        axWindows: duplicateAX
    )

    expectMatches(duplicateCGResolution, [nil, nil])
    precondition(!duplicateCGResolution.titleFallbackComplete)
    expectMatches(duplicateAXResolution, [nil])
    precondition(!duplicateAXResolution.titleFallbackComplete)
}

private func testWindowIDsMustBeGloballyUniqueAcrossPIDs() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let duplicateCGWindowID = [
        OnScreenCGWindow(windowID: 56, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 56, ownerPID: 600, layer: 0, bounds: bounds, title: ""),
    ]
    let oneAXWindow = [AXWindowMetadata(windowID: 56, ownerPID: 500, title: "first")]
    let oneCGWindow = [
        OnScreenCGWindow(windowID: 57, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let duplicateAXWindowID = [
        AXWindowMetadata(windowID: 57, ownerPID: 500, title: "first"),
        AXWindowMetadata(windowID: 57, ownerPID: 600, title: "second"),
    ]

    let duplicateCGResolution = resolveAXWindowMatches(
        cgWindows: duplicateCGWindowID,
        axWindows: oneAXWindow
    )
    let duplicateAXResolution = resolveAXWindowMatches(
        cgWindows: oneCGWindow,
        axWindows: duplicateAXWindowID
    )

    expectMatches(duplicateCGResolution, [nil, nil])
    precondition(!duplicateCGResolution.titleFallbackComplete)
    expectMatches(duplicateAXResolution, [nil])
    precondition(!duplicateAXResolution.titleFallbackComplete)
}

private func testMissingIdentityOrTitleFailsClosedOnlyWhenFallbackIsRequired() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let cgWindows = [
        OnScreenCGWindow(windowID: nil, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 49, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 50, ownerPID: 500, layer: 0, bounds: bounds, title: "CG title"),
    ]
    let axWindows = [
        AXWindowMetadata(windowID: nil, ownerPID: 500, title: "missing-id"),
        AXWindowMetadata(windowID: 49, ownerPID: 500, title: nil),
    ]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil, 1, nil])
    precondition(!resolution.titleFallbackComplete)
}

private func testEmptyAXTitleIsACompletedFallbackRead() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 51,
            ownerPID: 500,
            layer: 0,
            bounds: WindowBounds(left: 0, top: 0, width: 200, height: 100),
            title: ""
        )
    ]
    let axWindows = [AXWindowMetadata(windowID: 51, ownerPID: 500, title: "")]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [0])
    precondition(resolution.titleFallbackComplete)
}

private func testResolvedMetadataPreservesCGTitleAndUsesExactAXFallback() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 52,
            ownerPID: 500,
            layer: 0,
            bounds: bounds,
            title: "CG title"
        ),
        OnScreenCGWindow(windowID: 53, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
    ]
    let axWindows = [
        AXWindowMetadata(
            windowID: 52,
            ownerPID: 500,
            title: "different AX title",
            isFocused: false
        ),
        AXWindowMetadata(
            windowID: 53,
            ownerPID: 500,
            title: "InPrivate",
            isFocused: true
        ),
    ]

    let metadata = resolvedWindowMetadata(cgWindows: cgWindows, axWindows: axWindows)

    precondition(metadata == [
        ResolvedWindowMetadata(title: "CG title", isActive: false),
        ResolvedWindowMetadata(title: "InPrivate", isActive: true),
    ])
}

private func testResolvedMetadataRejectsIncompleteBlankTitle() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 54,
            ownerPID: 500,
            layer: 0,
            bounds: WindowBounds(left: 0, top: 0, width: 200, height: 100),
            title: ""
        )
    ]
    let axWindows = [
        AXWindowMetadata(
            windowID: 55,
            ownerPID: 500,
            title: "same-geometry-other-space",
            isFocused: true
        )
    ]

    precondition(resolvedWindowMetadata(cgWindows: cgWindows, axWindows: axWindows) == nil)
}

@main
enum MacWindowListCoreTests {
    static func main() {
        testExactIdentityAuthorizesNormalLayerFallback()
        testNonWindowLayersCannotAuthorizeFallback()
        testPIDAndWindowIDMustBothMatch()
        testSameGeometryDifferentSpaceWindowIsRejected()
        testDuplicateIdentitiesNeverCreateManyToOneMatches()
        testWindowIDsMustBeGloballyUniqueAcrossPIDs()
        testMissingIdentityOrTitleFailsClosedOnlyWhenFallbackIsRequired()
        testEmptyAXTitleIsACompletedFallbackRead()
        testResolvedMetadataPreservesCGTitleAndUsesExactAXFallback()
        testResolvedMetadataRejectsIncompleteBlankTitle()
        print("MacWindowListCoreTests passed")
    }
}
