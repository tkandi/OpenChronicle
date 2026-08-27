import Foundation

private func expectMatches(
    _ resolution: AXWindowMatchResolution,
    _ expected: [Int?],
    file: StaticString = #file,
    line: UInt = #line
) {
    precondition(resolution.axIndexByCGIndex == expected, file: file, line: line)
}

private func testWindowRecordEncodesCoreGraphicsWindowID() {
    let record = WindowRecord(
        app_name: "Private Browser",
        bundle_id: "com.example.private",
        title: "Private",
        alternate_title: nil,
        left: -220,
        top: 10,
        width: 300,
        height: 200,
        layer: 3,
        is_active: false,
        title_available: true,
        is_active_candidate: false,
        window_id: 73
    )

    let data = try! JSONEncoder().encode(record)
    let payload = try! JSONSerialization.jsonObject(with: data) as! [String: Any]

    precondition((payload["window_id"] as? NSNumber)?.uint32Value == 73)
    precondition((payload["layer"] as? NSNumber)?.intValue == 3)

    let output = Output(schema_version: 1, windows: [record], displays: [])
    let outputData = try! JSONEncoder().encode(output)
    let outputPayload = try! JSONSerialization.jsonObject(with: outputData) as! [String: Any]
    precondition((outputPayload["schema_version"] as? NSNumber)?.intValue == 1)
}

private func testCoreGraphicsSourceMapsToWindowRecordID() {
    let valid = OnScreenCGWindow(
        windowID: 73,
        ownerPID: 500,
        layer: 0,
        bounds: WindowBounds(left: 0, top: 0, width: 100, height: 100),
        title: "Private"
    )
    let missing = OnScreenCGWindow(
        windowID: nil,
        ownerPID: 500,
        layer: 0,
        bounds: valid.bounds,
        title: ""
    )
    let invalid = OnScreenCGWindow(
        windowID: 0,
        ownerPID: 500,
        layer: 0,
        bounds: valid.bounds,
        title: ""
    )

    precondition(windowRecordID(from: valid) == 73)
    precondition(windowRecordID(from: missing) == nil)
    precondition(windowRecordID(from: invalid) == nil)
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
    let axWindows = [AXWindowMetadata(windowID: 41, ownerPID: 500)]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [0])
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
    let axWindows = [AXWindowMetadata(windowID: 42, ownerPID: 500)]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil])
}

private func testPIDAndWindowIDMustBothMatch() {
    let bounds = WindowBounds(left: 100, top: 20, width: 300, height: 200)
    let cgWindows = [
        OnScreenCGWindow(windowID: 43, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 44, ownerPID: 600, layer: 0, bounds: bounds, title: ""),
    ]
    let axWindows = [
        AXWindowMetadata(windowID: 43, ownerPID: 600),
        AXWindowMetadata(windowID: 99, ownerPID: 500),
    ]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil, nil])
}

private func testSameGeometryDifferentSpaceWindowIsRejected() {
    let bounds = WindowBounds(left: 100, top: 20, width: 300, height: 200)
    let cgWindows = [
        OnScreenCGWindow(windowID: 45, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let axWindows = [AXWindowMetadata(windowID: 46, ownerPID: 500)]

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)

    expectMatches(resolution, [nil])
}

private func testDuplicateIdentitiesNeverCreateManyToOneMatches() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let duplicateCG = [
        OnScreenCGWindow(windowID: 47, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 47, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
    ]
    let oneAXWindow = [AXWindowMetadata(windowID: 47, ownerPID: 500)]
    let duplicateAX = [
        AXWindowMetadata(windowID: 48, ownerPID: 500),
        AXWindowMetadata(windowID: 48, ownerPID: 500),
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
    expectMatches(duplicateAXResolution, [nil])
}

private func testWindowIDsMustBeGloballyUniqueAcrossPIDs() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let duplicateCGWindowID = [
        OnScreenCGWindow(windowID: 56, ownerPID: 500, layer: 0, bounds: bounds, title: ""),
        OnScreenCGWindow(windowID: 56, ownerPID: 600, layer: 0, bounds: bounds, title: ""),
    ]
    let oneAXWindow = [AXWindowMetadata(windowID: 56, ownerPID: 500)]
    let oneCGWindow = [
        OnScreenCGWindow(windowID: 57, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let duplicateAXWindowID = [
        AXWindowMetadata(windowID: 57, ownerPID: 500),
        AXWindowMetadata(windowID: 57, ownerPID: 600),
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
    expectMatches(duplicateAXResolution, [nil])
}

private func testMissingIdentityOrTitleProducesTypedUnknownMetadata() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let missingIdentityCG = [
        OnScreenCGWindow(windowID: nil, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let titledCG = [
        OnScreenCGWindow(
            windowID: nil,
            ownerPID: 500,
            layer: 0,
            bounds: bounds,
            title: "CG title"
        )
    ]
    let exactCG = [
        OnScreenCGWindow(windowID: 49, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let exactAX = [AXWindowMetadata(windowID: 49, ownerPID: 500)]

    let missingIdentityResolution = resolveAXWindowMatches(
        cgWindows: missingIdentityCG,
        axWindows: []
    )
    var titledWindowReadCount = 0
    let titledMetadata = resolvedWindowMetadata(
        cgWindows: titledCG,
        axWindows: [],
        readAXTitle: { _ in
            titledWindowReadCount += 1
            return nil
        }
    )
    var unavailableTitleReadCount = 0
    let unavailableTitleMetadata = resolvedWindowMetadata(
        cgWindows: exactCG,
        axWindows: exactAX,
        readAXTitle: { _ in
            unavailableTitleReadCount += 1
            return nil
        }
    )

    expectMatches(missingIdentityResolution, [nil])
    precondition(titledWindowReadCount == 0)
    precondition(titledMetadata == [ResolvedWindowMetadata(title: "CG title", isActive: false)])
    precondition(unavailableTitleReadCount == 1)
    precondition(unavailableTitleMetadata == [
        ResolvedWindowMetadata(
            title: "",
            isActive: false,
            titleAvailable: false,
            isActiveCandidate: false
        )
    ])
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
    let axWindows = [AXWindowMetadata(windowID: 51, ownerPID: 500)]
    var titleReadCount = 0

    let resolution = resolveAXWindowMatches(cgWindows: cgWindows, axWindows: axWindows)
    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: axWindows,
        readAXTitle: { _ in
            titleReadCount += 1
            return ""
        }
    )

    expectMatches(resolution, [0])
    precondition(titleReadCount == 1)
    precondition(metadata == [ResolvedWindowMetadata(title: "", isActive: false)])
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
        AXWindowMetadata(windowID: 52, ownerPID: 500),
        AXWindowMetadata(windowID: 53, ownerPID: 500, isFocused: true),
    ]
    var titleReadIndices: [Int] = []

    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: axWindows,
        readAXTitle: { axIndex in
            titleReadIndices.append(axIndex)
            return axIndex == 1 ? "InPrivate" : "different AX title"
        }
    )

    precondition(titleReadIndices == [0, 1])
    precondition(metadata == [
        ResolvedWindowMetadata(
            title: "CG title",
            alternateTitle: "different AX title",
            isActive: false
        ),
        ResolvedWindowMetadata(title: "InPrivate", isActive: true),
    ])
}

private func testDistinctAXTitleIsRetainedForSafelyMatchedTitledWindow() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 58,
            ownerPID: 500,
            layer: 0,
            bounds: WindowBounds(left: 0, top: 0, width: 200, height: 100),
            title: "Google"
        )
    ]
    let axWindows = [AXWindowMetadata(windowID: 58, ownerPID: 500, isFocused: true)]
    var titleReadCount = 0

    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: axWindows,
        readAXTitle: { axIndex in
            precondition(axIndex == 0)
            titleReadCount += 1
            return "Google - Microsoft Edge (InPrivate)"
        }
    )

    precondition(titleReadCount == 1)
    precondition(metadata == [
        ResolvedWindowMetadata(
            title: "Google",
            alternateTitle: "Google - Microsoft Edge (InPrivate)",
            isActive: true
        )
    ])
}

private func testResolvedMetadataKeepsIncompleteBlankTitleAsActiveCandidate() {
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 54,
            ownerPID: 500,
            layer: 0,
            bounds: WindowBounds(left: 0, top: 0, width: 200, height: 100),
            title: ""
        )
    ]
    let axWindows = [AXWindowMetadata(windowID: 55, ownerPID: 500, isFocused: true)]
    var titleReadCount = 0

    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: axWindows,
        frontmostPID: 500,
        readAXTitle: { _ in
            titleReadCount += 1
            return "same-geometry-other-space"
        }
    )

    precondition(metadata == [
        ResolvedWindowMetadata(
            title: "",
            isActive: false,
            titleAvailable: false,
            isActiveCandidate: true
        )
    ])
    precondition(titleReadCount == 0)
}

private func testUnrelatedUnsupportedBlankTitleDoesNotDiscardKnownWindows() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let cgWindows = [
        OnScreenCGWindow(
            windowID: 70,
            ownerPID: 500,
            layer: 0,
            bounds: bounds,
            title: "Known title"
        ),
        OnScreenCGWindow(windowID: nil, ownerPID: 900, layer: 0, bounds: bounds, title: ""),
    ]

    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: [],
        frontmostPID: 500,
        readAXTitle: { _ in
            preconditionFailure("unsupported identity must not authorize an AX title read")
        }
    )

    precondition(metadata == [
        ResolvedWindowMetadata(
            title: "Known title",
            isActive: false,
            titleAvailable: true,
            isActiveCandidate: true
        ),
        ResolvedWindowMetadata(
            title: "",
            isActive: false,
            titleAvailable: false,
            isActiveCandidate: false
        ),
    ])
}

private func testExactFocusedIdentitySuppressesActiveCandidates() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let cgWindows = [
        OnScreenCGWindow(windowID: 71, ownerPID: 500, layer: 0, bounds: bounds, title: "one"),
        OnScreenCGWindow(windowID: 72, ownerPID: 500, layer: 0, bounds: bounds, title: "two"),
    ]
    let axWindows = [
        AXWindowMetadata(windowID: 71, ownerPID: 500, isFocused: true),
        AXWindowMetadata(windowID: 72, ownerPID: 500),
    ]
    var titleReadIndices: [Int] = []

    let metadata = resolvedWindowMetadata(
        cgWindows: cgWindows,
        axWindows: axWindows,
        frontmostPID: 500,
        readAXTitle: { axIndex in
            titleReadIndices.append(axIndex)
            return axIndex == 0 ? "one" : "two"
        }
    )

    precondition(titleReadIndices == [0, 1])
    precondition(metadata == [
        ResolvedWindowMetadata(
            title: "one",
            isActive: true,
            titleAvailable: true,
            isActiveCandidate: false
        ),
        ResolvedWindowMetadata(
            title: "two",
            isActive: false,
            titleAvailable: true,
            isActiveCandidate: false
        ),
    ])
}

private func testTitleReadsOccurOnlyForGloballyAcceptedIdentity() {
    let bounds = WindowBounds(left: 0, top: 0, width: 200, height: 100)
    let rejectedCases = [
        (
            [OnScreenCGWindow(windowID: nil, ownerPID: 500, layer: 0, bounds: bounds, title: "")],
            [AXWindowMetadata(windowID: nil, ownerPID: 500)]
        ),
        (
            [OnScreenCGWindow(windowID: 60, ownerPID: 500, layer: 0, bounds: bounds, title: "")],
            [AXWindowMetadata(windowID: 60, ownerPID: 600)]
        ),
        (
            [OnScreenCGWindow(windowID: 61, ownerPID: 500, layer: 0, bounds: bounds, title: "")],
            [AXWindowMetadata(windowID: 62, ownerPID: 500)]
        ),
        (
            [
                OnScreenCGWindow(
                    windowID: 63,
                    ownerPID: 500,
                    layer: 0,
                    bounds: bounds,
                    title: ""
                ),
                OnScreenCGWindow(
                    windowID: 63,
                    ownerPID: 600,
                    layer: 0,
                    bounds: bounds,
                    title: ""
                ),
            ],
            [
                AXWindowMetadata(windowID: 63, ownerPID: 500),
                AXWindowMetadata(windowID: 63, ownerPID: 600),
            ]
        ),
        (
            [OnScreenCGWindow(windowID: 64, ownerPID: 500, layer: 0, bounds: bounds, title: "")],
            [
                AXWindowMetadata(windowID: 64, ownerPID: 500),
                AXWindowMetadata(windowID: 64, ownerPID: 500),
            ]
        ),
        (
            [
                OnScreenCGWindow(
                    windowID: 66,
                    ownerPID: 500,
                    layer: 0,
                    bounds: bounds,
                    title: "CG title"
                )
            ],
            [AXWindowMetadata(windowID: 66, ownerPID: 600)]
        ),
    ]

    for (cgWindows, axWindows) in rejectedCases {
        var titleReadCount = 0
        let metadata = resolvedWindowMetadata(
            cgWindows: cgWindows,
            axWindows: axWindows,
            readAXTitle: { _ in
                titleReadCount += 1
                return "must-not-be-read"
            }
        )

        precondition(metadata.count == cgWindows.count)
        precondition(metadata.map(\.titleAvailable) == cgWindows.map { !$0.title.isEmpty })
        precondition(titleReadCount == 0)
    }

    let acceptedCG = [
        OnScreenCGWindow(windowID: 65, ownerPID: 500, layer: 0, bounds: bounds, title: "")
    ]
    let acceptedAX = [
        AXWindowMetadata(windowID: 65, ownerPID: 500, isFocused: true)
    ]
    var acceptedTitleReadCount = 0

    let acceptedMetadata = resolvedWindowMetadata(
        cgWindows: acceptedCG,
        axWindows: acceptedAX,
        readAXTitle: { axIndex in
            precondition(axIndex == 0)
            acceptedTitleReadCount += 1
            return "InPrivate"
        }
    )

    precondition(acceptedTitleReadCount == 1)
    precondition(acceptedMetadata == [
        ResolvedWindowMetadata(title: "InPrivate", isActive: true)
    ])
}

@main
enum MacWindowListCoreTests {
    static func main() {
        testWindowRecordEncodesCoreGraphicsWindowID()
        testCoreGraphicsSourceMapsToWindowRecordID()
        testExactIdentityAuthorizesNormalLayerFallback()
        testNonWindowLayersCannotAuthorizeFallback()
        testPIDAndWindowIDMustBothMatch()
        testSameGeometryDifferentSpaceWindowIsRejected()
        testDuplicateIdentitiesNeverCreateManyToOneMatches()
        testWindowIDsMustBeGloballyUniqueAcrossPIDs()
        testMissingIdentityOrTitleProducesTypedUnknownMetadata()
        testEmptyAXTitleIsACompletedFallbackRead()
        testResolvedMetadataPreservesCGTitleAndUsesExactAXFallback()
        testDistinctAXTitleIsRetainedForSafelyMatchedTitledWindow()
        testResolvedMetadataKeepsIncompleteBlankTitleAsActiveCandidate()
        testUnrelatedUnsupportedBlankTitleDoesNotDiscardKnownWindows()
        testExactFocusedIdentitySuppressesActiveCandidates()
        testTitleReadsOccurOnlyForGloballyAcceptedIdentity()
        print("MacWindowListCoreTests passed")
    }
}
