import Foundation

private let exactCommand = #"{"version":1,"displays":[{"id":123,"width":1920,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[789]}"#

private func requireSuccess<T>(
    _ result: Result<T, CaptureErrorCode>,
    file: StaticString = #file,
    line: UInt = #line
) -> T {
    switch result {
    case let .success(value):
        return value
    case let .failure(error):
        preconditionFailure("expected success, got \(error.rawValue)", file: file, line: line)
    }
}

private func expectFailure<T>(
    _ result: Result<T, CaptureErrorCode>,
    _ expected: CaptureErrorCode,
    file: StaticString = #file,
    line: UInt = #line
) {
    switch result {
    case .success:
        preconditionFailure("expected \(expected.rawValue)", file: file, line: line)
    case let .failure(actual):
        precondition(actual == expected, "expected \(expected), got \(actual)", file: file, line: line)
    }
}

private func validCommand(
    displays: [CaptureDisplayRequest] = [CaptureDisplayRequest(id: 123, width: nil, height: nil)],
    protectedWindowIDs: [UInt32] = [456],
    overlayWindowIDs: [UInt32] = [789]
) -> CaptureCommand {
    CaptureCommand(
        version: 1,
        displays: displays,
        protectedWindowIDs: protectedWindowIDs,
        overlayWindowIDs: overlayWindowIDs
    )
}

private func sourceDisplay(
    id: UInt32 = 123,
    left: Double = -1440,
    top: Double = 0,
    pointWidth: Double = 1440,
    pointHeight: Double = 900
) -> CaptureDisplaySource {
    CaptureDisplaySource(
        id: id,
        left: left,
        top: top,
        pointWidth: pointWidth,
        pointHeight: pointHeight
    )
}

private func sourceApplication(
    processID: Int32,
    bundleIdentifier: String,
    applicationName: String
) -> CaptureApplicationSource {
    CaptureApplicationSource(
        processID: processID,
        bundleIdentifier: bundleIdentifier,
        applicationName: applicationName
    )
}

private func sourceWindow(
    id: UInt32,
    owner: CaptureApplicationSource?,
    left: Double = 10,
    top: Double = 20,
    width: Double = 300,
    height: Double = 200,
    title: String? = "window"
) -> CaptureWindowSource {
    CaptureWindowSource(
        id: id,
        owner: owner,
        left: left,
        top: top,
        width: width,
        height: height,
        title: title
    )
}

private let protectedApplication = sourceApplication(
    processID: 500,
    bundleIdentifier: "com.example.private",
    applicationName: "Private"
)
private let overlayApplication = sourceApplication(
    processID: 600,
    bundleIdentifier: "com.openchronicle.overlay",
    applicationName: "OpenChronicle Overlay"
)

private func fingerprint(
    displays: [CaptureDisplaySource] = [sourceDisplay()],
    windows: [CaptureWindowSource],
    scope: CapturePrivacyFingerprintScope? = nil
) -> CapturePrivacyFingerprint {
    let resolvedScope = scope ?? CapturePrivacyFingerprintScope(
        requestedDisplayIDs: displays.map(\.id),
        excludedApplicationProcessIDs: Array(Set(windows.compactMap(\.owner?.processID))).sorted()
    )
    return requireSuccess(capturePrivacyFingerprint(
        displays: displays,
        windows: windows,
        scope: resolvedScope
    ))
}

private func testExactCommandDecodes() {
    let command = requireSuccess(prepareCaptureCommand(Data(exactCommand.utf8), supportedOS: true))

    precondition(command == CaptureCommand(
        version: 1,
        displays: [CaptureDisplayRequest(id: 123, width: 1920, height: 1080)],
        protectedWindowIDs: [456],
        overlayWindowIDs: [789]
    ))

    let native = requireSuccess(prepareCaptureCommand(
        Data(#"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#.utf8),
        supportedOS: true
    ))
    precondition(native.displays == [CaptureDisplayRequest(id: 123, width: nil, height: nil)])

    let escapedKeys = requireSuccess(prepareCaptureCommand(
        Data(#"{"\u0076ersion":1,"\u0064isplays":[{"\u0069d":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#.utf8),
        supportedOS: true
    ))
    precondition(escapedKeys == CaptureCommand(
        version: 1,
        displays: [CaptureDisplayRequest(id: 123, width: nil, height: nil)],
        protectedWindowIDs: [456],
        overlayWindowIDs: []
    ))
}

private func testUnsupportedOSPrecedesCommandParsing() {
    expectFailure(
        prepareCaptureCommand(Data("private malformed command".utf8), supportedOS: false),
        .unsupportedOS
    )
}

private func testMalformedAndNonExactCommandsAreInvalid() {
    let invalidCommands = [
        "",
        "[]",
        "null",
        "{private-marker}",
        #"{"version":2,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":true,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[],"extra":"private"}"#,
        #"{"version":1,"displays":[{"id":123,"extra":"private"}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":"123","protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]

    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }
}

private func testIDsMustBePositiveUniqueUInt32Values() {
    let invalidCommands = [
        #"{"version":1,"displays":[{"id":0}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":-1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":4294967296}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":4294967295.0000000001}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":true}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123},{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[0],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456,456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[0]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[789,789]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[456]}"#,
    ]

    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }
}

private func testDimensionsMustBePairedPositiveIntegersWithinIntRange() {
    let invalidCommands = [
        #"{"version":1,"displays":[{"id":123,"width":1920}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":0,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":1920,"height":-1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":1.5,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":true,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":9223372036854775808,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":9223372036854775807.0000000001,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]

    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }

    let minimum = requireSuccess(prepareCaptureCommand(
        Data(#"{"version":1,"displays":[{"id":123,"width":1,"height":1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#.utf8),
        supportedOS: true
    ))
    precondition(minimum.displays[0].width == 1)
    precondition(minimum.displays[0].height == 1)
}

private func testNumericFieldsRequirePlainIntegerTokens() {
    let overPrecisionFraction = "1.0000000000000000000000000000000000000001"
    let invalidCommands = [
        #"{"version":\#(overPrecisionFraction),"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1e0,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":\#(overPrecisionFraction)}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123e0}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[\#(overPrecisionFraction)],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456e0],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[\#(overPrecisionFraction)]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[789e0]}"#,
        #"{"version":1,"displays":[{"id":123,"width":\#(overPrecisionFraction),"height":1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":1e3,"height":1080}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]

    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }
}

private func testPlainIntegerTokenBoundaries() {
    let command = requireSuccess(prepareCaptureCommand(
        Data(#"{"version":1,"displays":[{"id":4294967295,"width":16384,"height":1}],"protected_window_ids":[4294967295],"overlay_window_ids":[]}"#.utf8),
        supportedOS: true
    ))
    precondition(command.displays == [CaptureDisplayRequest(
        id: UInt32.max,
        width: CaptureResourceLimits.maxDimension,
        height: 1
    )])
    precondition(command.protectedWindowIDs == [UInt32.max])

    let invalidCommands = [
        #"{"version":1,"displays":[{"id":4294967296}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[4294967296],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":9223372036854775808,"height":1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]
    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }
}

private func testResourceLimitConstantsAndCommandBoundaries() {
    precondition(CaptureResourceLimits.maxCommandBytes == 65_536)
    precondition(CaptureResourceLimits.maxDisplayCount == 16)
    precondition(CaptureResourceLimits.maxDimension == 16_384)
    precondition(CaptureResourceLimits.maxAggregatePixels == 128_000_000)
    precondition(CaptureResourceLimits.maxPNGBytes == 67_108_864)
    precondition(CaptureResourceLimits.maxAggregatePNGBytes == 134_217_728)
    precondition(CaptureResourceLimits.maxResponseBytes == 188_743_680)
    precondition(CaptureResourceLimits.maxStderrBytes == 65_536)

    let prefix = #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#
    let exact = prefix + String(
        repeating: " ",
        count: CaptureResourceLimits.maxCommandBytes - prefix.utf8.count
    )
    precondition(exact.utf8.count == CaptureResourceLimits.maxCommandBytes)
    _ = requireSuccess(prepareCaptureCommand(Data(exact.utf8), supportedOS: true))
    expectFailure(
        prepareCaptureCommand(Data((exact + " ").utf8), supportedOS: true),
        .invalidCommand
    )

    let maximumDisplays = (1...CaptureResourceLimits.maxDisplayCount)
        .map { #"{"id":\#($0),"width":1,"height":1}"# }
        .joined(separator: ",")
    _ = requireSuccess(prepareCaptureCommand(
        Data((#"{"version":1,"displays":["# + maximumDisplays
            + #"],"protected_window_ids":[456],"overlay_window_ids":[]}"#).utf8),
        supportedOS: true
    ))
    let tooManyDisplays = maximumDisplays
        + #",{"id":999,"width":1,"height":1}"#
    expectFailure(
        prepareCaptureCommand(
            Data((#"{"version":1,"displays":["# + tooManyDisplays
                + #"],"protected_window_ids":[456],"overlay_window_ids":[]}"#).utf8),
            supportedOS: true
        ),
        .invalidCommand
    )

    let oversizedDimension = CaptureResourceLimits.maxDimension + 1
    expectFailure(
        prepareCaptureCommand(
            Data(#"{"version":1,"displays":[{"id":123,"width":\#(oversizedDimension),"height":1}],"protected_window_ids":[456],"overlay_window_ids":[]}"#.utf8),
            supportedOS: true
        ),
        .invalidCommand
    )
}

private func testDuplicateObjectMembersAreInvalidAfterKeyDecoding() {
    let invalidCommands = [
        #"{"version":1,"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"\u0076ersion":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123}],"protected_window_ids":[456],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"id":124}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"\u0069d":124}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"width":1920,"height":1080,"\u0077idth":1920}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]

    for raw in invalidCommands {
        expectFailure(prepareCaptureCommand(Data(raw.utf8), supportedOS: true), .invalidCommand)
    }
}

private func testResolverReturnsOnlyUniqueExactTargets() {
    let command = validCommand(
        displays: [
            CaptureDisplayRequest(id: 123, width: nil, height: nil),
            CaptureDisplayRequest(id: 124, width: 800, height: 600),
        ],
        protectedWindowIDs: [456, 457],
        overlayWindowIDs: [789]
    )
    let displays = [sourceDisplay(id: 124), sourceDisplay(id: 123)]
    let applications = [overlayApplication, protectedApplication]
    let windows = [
        sourceWindow(id: 789, owner: overlayApplication),
        sourceWindow(id: 999, owner: protectedApplication),
        sourceWindow(id: 456, owner: protectedApplication),
        sourceWindow(id: 457, owner: protectedApplication),
    ]

    let targets = requireSuccess(resolveCaptureTargets(
        command: command,
        displays: displays,
        windows: windows,
        applications: applications
    ))

    precondition(targets.displayIndices == [1, 0])
    precondition(targets.excludedApplicationIndices == [1, 0])
}

private func testResolverPromotesProtectedOverlayAndAuxiliaryWindowsToOwningApplications() {
    let otherApplication = sourceApplication(
        processID: 700,
        bundleIdentifier: "com.example.allowed",
        applicationName: "Allowed"
    )
    let windows = [
        sourceWindow(id: 456, owner: protectedApplication, title: "matched"),
        sourceWindow(id: 457, owner: protectedApplication, title: "sheet"),
        sourceWindow(id: 458, owner: protectedApplication, title: "new popover"),
        sourceWindow(id: 789, owner: overlayApplication, title: "indicator"),
        sourceWindow(id: 999, owner: otherApplication, title: "allowed"),
    ]
    let applications = [otherApplication, protectedApplication, overlayApplication]

    let targets = requireSuccess(resolveCaptureTargets(
        command: validCommand(),
        displays: [sourceDisplay()],
        windows: windows,
        applications: applications
    ))

    precondition(targets.excludedApplicationIndices == [1, 2])
    let excludedOwners = Set(targets.excludedApplicationIndices.map { applications[$0] })
    precondition(excludedOwners.contains(windows[1].owner!))
    precondition(excludedOwners.contains(windows[2].owner!))
    precondition(!excludedOwners.contains(windows[4].owner!))
}

private func testResolverRejectsMissingAndAmbiguousDisplays() {
    let command = validCommand()

    expectFailure(
        resolveCaptureTargets(
            command: command,
            displays: [],
            windows: [sourceWindow(id: 456, owner: protectedApplication)],
            applications: [protectedApplication, overlayApplication]
        ),
        .displayNotFound
    )
    expectFailure(
        resolveCaptureTargets(
            command: command,
            displays: [sourceDisplay(), sourceDisplay()],
            windows: [
                sourceWindow(id: 456, owner: protectedApplication),
                sourceWindow(id: 789, owner: overlayApplication),
            ],
            applications: [protectedApplication, overlayApplication]
        ),
        .ambiguousDisplay
    )
}

private func testResolverRejectsMissingAndAmbiguousExcludedWindows() {
    let command = validCommand()

    expectFailure(
        resolveCaptureTargets(
            command: command,
            displays: [sourceDisplay()],
            windows: [],
            applications: [protectedApplication, overlayApplication]
        ),
        .windowNotFound
    )
    expectFailure(
        resolveCaptureTargets(
            command: command,
            displays: [sourceDisplay()],
            windows: [
                sourceWindow(id: 456, owner: protectedApplication),
                sourceWindow(id: 456, owner: protectedApplication),
                sourceWindow(id: 789, owner: overlayApplication),
            ],
            applications: [protectedApplication, overlayApplication]
        ),
        .ambiguousWindow
    )
}

private func testResolverRejectsMissingDuplicateAndInconsistentOwners() {
    let inconsistent = sourceApplication(
        processID: protectedApplication.processID,
        bundleIdentifier: "com.example.changed",
        applicationName: protectedApplication.applicationName
    )
    let cases: [([CaptureWindowSource], [CaptureApplicationSource])] = [
        (
            [
                sourceWindow(id: 456, owner: nil),
                sourceWindow(id: 789, owner: overlayApplication),
            ],
            [protectedApplication, overlayApplication]
        ),
        (
            [
                sourceWindow(id: 456, owner: protectedApplication),
                sourceWindow(id: 789, owner: overlayApplication),
            ],
            [protectedApplication, protectedApplication, overlayApplication]
        ),
        (
            [
                sourceWindow(id: 456, owner: inconsistent),
                sourceWindow(id: 789, owner: overlayApplication),
            ],
            [protectedApplication, overlayApplication]
        ),
    ]

    for (windows, applications) in cases {
        expectFailure(
            resolveCaptureTargets(
                command: validCommand(),
                displays: [sourceDisplay()],
                windows: windows,
                applications: applications
            ),
            .windowOwnerUnavailable
        )
    }
}

private func testResolverRevalidatesSafetyCriticalCommandState() {
    let invalidCommands = [
        validCommand(protectedWindowIDs: []),
        validCommand(protectedWindowIDs: [456, 456]),
        validCommand(protectedWindowIDs: [456], overlayWindowIDs: [456]),
        validCommand(displays: [
            CaptureDisplayRequest(id: 123, width: nil, height: nil),
            CaptureDisplayRequest(id: 123, width: nil, height: nil),
        ]),
    ]

    for command in invalidCommands {
        expectFailure(
            resolveCaptureTargets(
                command: command,
                displays: [sourceDisplay()],
                windows: [
                    sourceWindow(id: 456, owner: protectedApplication),
                    sourceWindow(id: 789, owner: overlayApplication),
                ],
                applications: [protectedApplication, overlayApplication]
            ),
            .invalidCommand
        )
    }
}

private func testResolverRejectsUnrepresentableDisplayGeometry() {
    let invalidDisplays = [
        sourceDisplay(left: .infinity),
        sourceDisplay(top: .nan),
        sourceDisplay(pointWidth: 0),
        sourceDisplay(pointHeight: -1),
        sourceDisplay(pointWidth: .greatestFiniteMagnitude),
    ]

    for display in invalidDisplays {
        expectFailure(
            resolveCaptureTargets(
                command: validCommand(),
                displays: [display],
                windows: [
                    sourceWindow(id: 456, owner: protectedApplication),
                    sourceWindow(id: 789, owner: overlayApplication),
                ],
                applications: [protectedApplication, overlayApplication]
            ),
            .contentUnavailable
        )
    }
}

private func testPrivacyFingerprintIsCanonicalAndDetectsWindowChanges() {
    let firstWindows = [
        sourceWindow(id: 456, owner: protectedApplication, title: "Private"),
        sourceWindow(id: 789, owner: overlayApplication, title: "Protected"),
    ]
    let first = fingerprint(windows: firstWindows)
    let reordered = fingerprint(windows: Array(firstWindows.reversed()))
    precondition(first == reordered)

    let changedTitle = fingerprint(windows: [
        sourceWindow(id: 456, owner: protectedApplication, title: "Allowed"),
        firstWindows[1],
    ])
    let changedOwner = fingerprint(windows: [
        sourceWindow(id: 456, owner: overlayApplication, title: "Private"),
        firstWindows[1],
    ])
    let changedFrame = fingerprint(windows: [
        sourceWindow(id: 456, owner: protectedApplication, left: 11, title: "Private"),
        firstWindows[1],
    ])
    let addedSameAppWindow = fingerprint(windows: firstWindows + [
        sourceWindow(id: 457, owner: protectedApplication, title: "new sheet")
    ])
    precondition(first != changedTitle)
    precondition(first != changedOwner)
    precondition(first != changedFrame)
    precondition(first != addedSameAppWindow)

    expectFailure(
        capturePrivacyFingerprint(
            displays: [sourceDisplay()],
            windows: [sourceWindow(id: 456, owner: protectedApplication, left: .nan)],
            scope: CapturePrivacyFingerprintScope(
                requestedDisplayIDs: [123],
                excludedApplicationProcessIDs: [protectedApplication.processID]
            )
        ),
        .contentUnavailable
    )
}

private func testPrivacyFingerprintMustIgnoreUnrelatedApplicationChanges() {
    let otherApplication = sourceApplication(
        processID: 700,
        bundleIdentifier: "com.example.allowed",
        applicationName: "Allowed"
    )
    let beforeWindows = [
        sourceWindow(id: 456, owner: protectedApplication, title: "Private"),
        sourceWindow(id: 789, owner: overlayApplication, title: "Protected"),
        sourceWindow(id: 999, owner: otherApplication, title: "allowed"),
    ]
    let scope = CapturePrivacyFingerprintScope(
        requestedDisplayIDs: [123],
        excludedApplicationProcessIDs: [
            protectedApplication.processID,
            overlayApplication.processID,
        ]
    )
    let displays = [sourceDisplay(), sourceDisplay(id: 124, left: 0)]
    let before = fingerprint(displays: displays, windows: beforeWindows, scope: scope)

    let unrelatedWindowSetChange = fingerprint(windows: beforeWindows + [
        sourceWindow(id: 1_000, owner: otherApplication, title: "transient"),
    ], scope: scope)
    let unrelatedTitleChange = fingerprint(windows: [
        beforeWindows[0],
        beforeWindows[1],
        sourceWindow(id: 999, owner: otherApplication, title: "renamed"),
    ], scope: scope)
    let unrelatedFrameChange = fingerprint(windows: [
        beforeWindows[0],
        beforeWindows[1],
        sourceWindow(id: 999, owner: otherApplication, left: 999, title: "allowed"),
    ], scope: scope)
    let changedUnrequestedDisplay = fingerprint(
        displays: [sourceDisplay(), sourceDisplay(id: 124, left: 1)],
        windows: beforeWindows,
        scope: scope
    )

    precondition(before == unrelatedWindowSetChange)
    precondition(before == unrelatedTitleChange)
    precondition(before == unrelatedFrameChange)
    precondition(before == changedUnrequestedDisplay)

    let relevantWindowSetChange = fingerprint(windows: beforeWindows + [
        sourceWindow(id: 457, owner: protectedApplication, title: "sheet"),
    ], scope: scope)
    let relevantIDChange = fingerprint(windows: [
        sourceWindow(id: 458, owner: protectedApplication, title: "Private"),
        beforeWindows[1],
        beforeWindows[2],
    ], scope: scope)
    let relevantFrameChange = fingerprint(windows: [
        sourceWindow(id: 456, owner: protectedApplication, left: 11, title: "Private"),
        beforeWindows[1],
        beforeWindows[2],
    ], scope: scope)
    let relevantTitleChange = fingerprint(windows: [
        sourceWindow(id: 456, owner: protectedApplication, title: "Renamed"),
        beforeWindows[1],
        beforeWindows[2],
    ], scope: scope)
    let changedRequestedDisplay = fingerprint(
        displays: [sourceDisplay(pointWidth: 1_441), sourceDisplay(id: 124, left: 0)],
        windows: beforeWindows,
        scope: scope
    )

    precondition(before != relevantWindowSetChange)
    precondition(before != relevantIDChange)
    precondition(before != relevantFrameChange)
    precondition(before != relevantTitleChange)
    precondition(before != changedRequestedDisplay)

    expectFailure(
        capturePrivacyFingerprint(
            displays: [sourceDisplay()],
            windows: [sourceWindow(id: 456, owner: protectedApplication, left: .nan)],
            scope: scope
        ),
        .contentUnavailable
    )
    let invalidProtectedOwner = sourceApplication(
        processID: protectedApplication.processID,
        bundleIdentifier: "",
        applicationName: protectedApplication.applicationName
    )
    expectFailure(
        capturePrivacyFingerprint(
            displays: displays,
            windows: [sourceWindow(id: 456, owner: invalidProtectedOwner)],
            scope: scope
        ),
        .contentUnavailable
    )
}

private func testOutputDimensionsUseExplicitOrNativePixels() {
    let explicit = requireSuccess(resolveOutputDimensions(
        request: CaptureDisplayRequest(id: 123, width: 1920, height: 1080),
        pointWidth: 1440,
        pointHeight: 900,
        pointPixelScale: 2
    ))
    precondition(explicit == CapturePixelSize(width: 1920, height: 1080))

    let native = requireSuccess(resolveOutputDimensions(
        request: CaptureDisplayRequest(id: 123, width: nil, height: nil),
        pointWidth: 1440,
        pointHeight: 900,
        pointPixelScale: 2
    ))
    precondition(native == CapturePixelSize(width: 2880, height: 1800))

    let roundedNative = requireSuccess(resolveOutputDimensions(
        request: CaptureDisplayRequest(id: 123, width: nil, height: nil),
        pointWidth: 100.25,
        pointHeight: 50.25,
        pointPixelScale: 2
    ))
    precondition(roundedNative == CapturePixelSize(width: 201, height: 101))
}

private func testOutputDimensionBoundariesFailClosed() {
    let invalidCases: [(CaptureDisplayRequest, Double, Double, Double, CaptureErrorCode)] = [
        (CaptureDisplayRequest(id: 123, width: 1, height: nil), 100, 100, 1, .invalidCommand),
        (CaptureDisplayRequest(id: 123, width: nil, height: nil), 100, 100, 0, .contentUnavailable),
        (CaptureDisplayRequest(id: 123, width: nil, height: nil), 100, 100, .infinity, .contentUnavailable),
        (CaptureDisplayRequest(id: 123, width: nil, height: nil), .greatestFiniteMagnitude, 100, 2, .contentUnavailable),
    ]

    for (request, pointWidth, pointHeight, scale, expected) in invalidCases {
        expectFailure(
            resolveOutputDimensions(
                request: request,
                pointWidth: pointWidth,
                pointHeight: pointHeight,
                pointPixelScale: scale
            ),
            expected
        )
    }
}

private struct FakeCaptureResource: Equatable {
    let displayIndex: Int
}

private func testCaptureSequencePreparesAllTargetsBeforeProducingPNGs() async {
    let command = validCommand(
        displays: [
            CaptureDisplayRequest(id: 123, width: nil, height: nil),
            CaptureDisplayRequest(id: 124, width: 800, height: 600),
        ],
        protectedWindowIDs: [456, 457],
        overlayWindowIDs: [789]
    )
    let displays = [sourceDisplay(id: 124), sourceDisplay(id: 123)]
    let applications = [overlayApplication, protectedApplication]
    let windows = [
        sourceWindow(id: 789, owner: overlayApplication),
        sourceWindow(id: 999, owner: protectedApplication),
        sourceWindow(id: 456, owner: protectedApplication),
        sourceWindow(id: 457, owner: protectedApplication),
    ]
    var preparationCalls: [(Int, [Int])] = []

    let prepared = requireSuccess(prepareCaptureSequence(
        command: command,
        displays: displays,
        windows: windows,
        applications: applications,
        prepareResource: { displayIndex, excludedApplicationIndices in
            preparationCalls.append((displayIndex, excludedApplicationIndices))
            let scale = displayIndex == 1 ? 2.0 : 1.5
            return .success((FakeCaptureResource(displayIndex: displayIndex), scale))
        }
    ))

    precondition(preparationCalls.count == 2)
    precondition(preparationCalls[0].0 == 1)
    precondition(preparationCalls[0].1 == [1, 0])
    precondition(preparationCalls[1].0 == 0)
    precondition(preparationCalls[1].1 == [1, 0])
    precondition(prepared.map(\.pixelSize) == [
        CapturePixelSize(width: 2880, height: 1800),
        CapturePixelSize(width: 800, height: 600),
    ])
    precondition(prepared.allSatisfy {
        $0.fingerprintScope == CapturePrivacyFingerprintScope(
            requestedDisplayIDs: [123, 124],
            excludedApplicationProcessIDs: [
                protectedApplication.processID,
                overlayApplication.processID,
            ]
        )
    })

    var events: [String] = []
    var captureCalls: [(FakeCaptureResource, CapturePixelSize)] = []
    let initialFingerprint = fingerprint(
        displays: displays,
        windows: windows,
        scope: prepared[0].fingerprintScope
    )
    let captured = requireSuccess(await executePreparedCaptures(
        prepared,
        initialFingerprint: initialFingerprint,
        capture: { resource, pixelSize in
            events.append("capture-\(resource.displayIndex)")
            captureCalls.append((resource, pixelSize))
            return .success(CapturedFrame(
                pixelWidth: pixelSize.width,
                pixelHeight: pixelSize.height,
                payload: UInt8(resource.displayIndex)
            ))
        },
        currentFingerprint: {
            events.append("fingerprint")
            return .success(initialFingerprint)
        },
        encodePNG: { payload in
            events.append("encode-\(payload)")
            return Data([0x89, payload])
        }
    ))

    precondition(captureCalls.count == 2)
    precondition(captureCalls[0].0 == FakeCaptureResource(displayIndex: 1))
    precondition(captureCalls[0].1 == CapturePixelSize(width: 2880, height: 1800))
    precondition(captureCalls[1].0 == FakeCaptureResource(displayIndex: 0))
    precondition(captureCalls[1].1 == CapturePixelSize(width: 800, height: 600))
    precondition(captured.map(\.id) == [123, 124])
    precondition(captured.map(\.pixelWidth) == [2880, 800])
    precondition(captured.map(\.pngData) == [Data([0x89, 1]), Data([0x89, 0])])
    precondition(events == ["capture-1", "capture-0", "fingerprint", "encode-1", "encode-0"])
}

private func testFingerprintChangePreventsEveryPNGEncode() async {
    let windows = [
        sourceWindow(id: 456, owner: protectedApplication),
        sourceWindow(id: 789, owner: overlayApplication),
    ]
    let prepared = requireSuccess(prepareCaptureSequence(
        command: validCommand(),
        displays: [sourceDisplay()],
        windows: windows,
        applications: [protectedApplication, overlayApplication],
        prepareResource: { displayIndex, _ in
            .success((FakeCaptureResource(displayIndex: displayIndex), 1))
        }
    ))
    let scope = prepared[0].fingerprintScope
    let before = fingerprint(windows: windows, scope: scope)
    let after = fingerprint(windows: windows + [
        sourceWindow(id: 457, owner: protectedApplication, title: "new panel")
    ], scope: scope)
    var encodeCalls = 0

    let result = await executePreparedCaptures(
        prepared,
        initialFingerprint: before,
        capture: { _, pixelSize in
            .success(CapturedFrame(
                pixelWidth: pixelSize.width,
                pixelHeight: pixelSize.height,
                payload: UInt8(1)
            ))
        },
        currentFingerprint: { .success(after) },
        encodePNG: { _ in
            encodeCalls += 1
            return Data([1])
        }
    )

    expectFailure(result, .contentChanged)
    precondition(encodeCalls == 0)
}

private func testCaptureSequencePreparationFailuresPreventCapture() async {
    var prepareCallCount = 0
    let missingWindow = prepareCaptureSequence(
        command: validCommand(),
        displays: [sourceDisplay()],
        windows: [],
        applications: [protectedApplication, overlayApplication],
        prepareResource: { _, _ -> Result<(FakeCaptureResource, Double), CaptureErrorCode> in
            prepareCallCount += 1
            return .success((FakeCaptureResource(displayIndex: 0), 2))
        }
    )
    expectFailure(missingWindow, .windowNotFound)
    precondition(prepareCallCount == 0)

    let twoDisplays = validCommand(displays: [
        CaptureDisplayRequest(id: 123, width: nil, height: nil),
        CaptureDisplayRequest(id: 124, width: nil, height: nil),
    ])
    prepareCallCount = 0
    let resourceFailure = prepareCaptureSequence(
        command: twoDisplays,
        displays: [sourceDisplay(id: 123), sourceDisplay(id: 124)],
        windows: [
            sourceWindow(id: 456, owner: protectedApplication),
            sourceWindow(id: 789, owner: overlayApplication),
        ],
        applications: [protectedApplication, overlayApplication],
        prepareResource: { displayIndex, _ -> Result<
            (FakeCaptureResource, Double), CaptureErrorCode
        > in
            prepareCallCount += 1
            if displayIndex == 1 { return .failure(.contentUnavailable) }
            return .success((FakeCaptureResource(displayIndex: displayIndex), 2))
        }
    )
    expectFailure(resourceFailure, .contentUnavailable)
    precondition(prepareCallCount == 2)

    prepareCallCount = 0
    let dimensionFailure = prepareCaptureSequence(
        command: twoDisplays,
        displays: [sourceDisplay(id: 123), sourceDisplay(id: 124)],
        windows: [
            sourceWindow(id: 456, owner: protectedApplication),
            sourceWindow(id: 789, owner: overlayApplication),
        ],
        applications: [protectedApplication, overlayApplication],
        prepareResource: { displayIndex, _ in
            prepareCallCount += 1
            return .success((FakeCaptureResource(displayIndex: displayIndex), displayIndex == 0 ? 2 : 0))
        }
    )
    expectFailure(dimensionFailure, .contentUnavailable)
    precondition(prepareCallCount == 2)
}

private func testCaptureSequenceMidstreamFailuresReturnNoPartialDisplays() async {
    let command = validCommand(displays: [
        CaptureDisplayRequest(id: 123, width: 100, height: 100),
        CaptureDisplayRequest(id: 124, width: 100, height: 100),
        CaptureDisplayRequest(id: 125, width: 100, height: 100),
    ])
    let prepared = requireSuccess(prepareCaptureSequence(
        command: command,
        displays: [sourceDisplay(id: 123), sourceDisplay(id: 124), sourceDisplay(id: 125)],
        windows: [
            sourceWindow(id: 456, owner: protectedApplication),
            sourceWindow(id: 789, owner: overlayApplication),
        ],
        applications: [protectedApplication, overlayApplication],
        prepareResource: { displayIndex, _ in
            .success((FakeCaptureResource(displayIndex: displayIndex), 2))
        }
    ))

    var captureCallCount = 0
    let baseline = fingerprint(windows: [
        sourceWindow(id: 456, owner: protectedApplication),
        sourceWindow(id: 789, owner: overlayApplication),
    ])
    let captureFailure = await executePreparedCaptures(
        prepared,
        initialFingerprint: baseline,
        capture: { _, pixelSize in
            captureCallCount += 1
            if captureCallCount == 2 { return .failure(.captureFailed) }
            return .success(CapturedFrame(
                pixelWidth: pixelSize.width,
                pixelHeight: pixelSize.height,
                payload: Data([1])
            ))
        },
        currentFingerprint: { .success(baseline) },
        encodePNG: { $0 }
    )
    expectFailure(captureFailure, .captureFailed)
    precondition(captureCallCount == 2)

    let wrongSize = await executePreparedCaptures(
        prepared,
        initialFingerprint: baseline,
        capture: { _, _ in
            .success(CapturedFrame(pixelWidth: 99, pixelHeight: 100, payload: Data([1])))
        },
        currentFingerprint: { .success(baseline) },
        encodePNG: { $0 }
    )
    expectFailure(wrongSize, .captureFailed)

    let emptyPNG = await executePreparedCaptures(
        prepared,
        initialFingerprint: baseline,
        capture: { _, pixelSize in
            .success(CapturedFrame(
                pixelWidth: pixelSize.width,
                pixelHeight: pixelSize.height,
                payload: Data()
            ))
        },
        currentFingerprint: { .success(baseline) },
        encodePNG: { $0 }
    )
    expectFailure(emptyPNG, .encodeFailed)
}

private func testFixedErrorPayloadsAreExactSingleLines() {
    let expected: [(CaptureErrorCode, String)] = [
        (.unsupportedOS, "unsupported_os"),
        (.invalidCommand, "invalid_command"),
        (.contentUnavailable, "content_unavailable"),
        (.displayNotFound, "display_not_found"),
        (.windowNotFound, "window_not_found"),
        (.ambiguousDisplay, "ambiguous_display"),
        (.ambiguousWindow, "ambiguous_window"),
        (.windowOwnerUnavailable, "window_owner_unavailable"),
        (.contentChanged, "content_changed"),
        (.captureFailed, "capture_failed"),
        (.encodeFailed, "encode_failed"),
    ]

    for (code, rawValue) in expected {
        let output = String(decoding: errorResponseLine(code), as: UTF8.self)
        precondition(output == #"{"version":1,"status":"error","error":"\#(rawValue)"}"# + "\n")
        precondition(output.filter { $0 == "\n" }.count == 1)
    }
}

private func testAggregatePixelAndEncodedResponseResourceBoundaries() {
    let exactPixels = [
        CapturePixelSize(width: 10_000, height: 10_000),
        CapturePixelSize(width: 10_000, height: 2_800),
    ]
    precondition(capturePixelSizesAreWithinLimits(exactPixels))
    precondition(!capturePixelSizesAreWithinLimits(
        exactPixels + [CapturePixelSize(width: 1, height: 1)]
    ))
    precondition(!capturePixelSizesAreWithinLimits([
        CapturePixelSize(width: CaptureResourceLimits.maxDimension + 1, height: 1)
    ]))

    precondition(capturePNGByteCountsAreWithinLimits([
        CaptureResourceLimits.maxPNGBytes,
        CaptureResourceLimits.maxAggregatePNGBytes - CaptureResourceLimits.maxPNGBytes,
    ]))
    precondition(!capturePNGByteCountsAreWithinLimits([
        CaptureResourceLimits.maxPNGBytes + 1
    ]))
    precondition(!capturePNGByteCountsAreWithinLimits([
        CaptureResourceLimits.maxPNGBytes,
        CaptureResourceLimits.maxAggregatePNGBytes
            - CaptureResourceLimits.maxPNGBytes + 1,
    ]))
    precondition(estimatedBase64Length(0) == 0)
    precondition(estimatedBase64Length(1) == 4)
    precondition(estimatedBase64Length(3) == 4)
    precondition(estimatedBase64Length(Int.max) == nil)
}

private func testSuccessPayloadHasOnlyBoundedPublicFields() throws {
    let privateMarker = "private-title-and-app-marker"
    let display = CapturedDisplay(
        id: 123,
        left: -1440,
        top: 20,
        pointWidth: 1440,
        pointHeight: 900,
        pixelWidth: 2,
        pixelHeight: 1,
        pngData: Data([0x89, 0x50, 0x4e, 0x47])
    )

    let line = requireSuccess(encodeSuccessResponseLine(displays: [display]))
    let text = String(decoding: line, as: UTF8.self)
    precondition(text.filter { $0 == "\n" }.count == 1)
    precondition(!text.contains(privateMarker))
    precondition(!text.contains("title"))
    precondition(!text.contains("app_name"))

    let object = try JSONSerialization.jsonObject(with: line) as! [String: Any]
    precondition(Set(object.keys) == ["version", "status", "displays"])
    precondition((object["version"] as? NSNumber)?.intValue == 1)
    precondition(object["status"] as? String == "ok")
    let displays = object["displays"] as! [[String: Any]]
    precondition(displays.count == 1)
    precondition(Set(displays[0].keys) == [
        "id", "left", "top", "point_width", "point_height",
        "pixel_width", "pixel_height", "png_base64",
    ])
    precondition(displays[0]["png_base64"] as? String == "iVBORw==")
}

private func testInvalidSuccessBoundsReturnEncodeFailed() {
    let invalidDisplays = [
        CapturedDisplay(
            id: 123, left: .nan, top: 0, pointWidth: 100, pointHeight: 100,
            pixelWidth: 100, pixelHeight: 100, pngData: Data([1])
        ),
        CapturedDisplay(
            id: 123, left: 0, top: 0, pointWidth: 0, pointHeight: 100,
            pixelWidth: 100, pixelHeight: 100, pngData: Data([1])
        ),
        CapturedDisplay(
            id: 123, left: 0, top: 0, pointWidth: 100, pointHeight: 100,
            pixelWidth: 0, pixelHeight: 100, pngData: Data([1])
        ),
        CapturedDisplay(
            id: 123, left: 0, top: 0, pointWidth: 100, pointHeight: 100,
            pixelWidth: 100, pixelHeight: 100, pngData: Data()
        ),
    ]

    expectFailure(encodeSuccessResponseLine(displays: []), .encodeFailed)
    for display in invalidDisplays {
        expectFailure(encodeSuccessResponseLine(displays: [display]), .encodeFailed)
    }

    let duplicate = CapturedDisplay(
        id: 123, left: 0, top: 0, pointWidth: 100, pointHeight: 100,
        pixelWidth: 100, pixelHeight: 100, pngData: Data([1])
    )
    expectFailure(encodeSuccessResponseLine(displays: [duplicate, duplicate]), .encodeFailed)
}

private struct HelperResult {
    let output: Data
    let error: Data
    let status: Int32
}

private func runHelper(_ helper: String, input: String) throws -> HelperResult {
    let process = Process()
    let standardInput = Pipe()
    let standardOutput = Pipe()
    let standardError = Pipe()
    let terminated = DispatchSemaphore(value: 0)

    process.executableURL = URL(fileURLWithPath: helper)
    process.standardInput = standardInput
    process.standardOutput = standardOutput
    process.standardError = standardError
    process.terminationHandler = { _ in terminated.signal() }
    try process.run()

    standardInput.fileHandleForWriting.write(Data(input.utf8))
    standardInput.fileHandleForWriting.closeFile()
    guard terminated.wait(timeout: .now() + 5) == .success else {
        process.terminate()
        throw NSError(domain: "MacScreenCaptureCoreTests", code: 1)
    }
    return HelperResult(
        output: standardOutput.fileHandleForReading.readDataToEndOfFile(),
        error: standardError.fileHandleForReading.readDataToEndOfFile(),
        status: process.terminationStatus
    )
}

private func testHelperInvalidInputIsOneShotAndSilent(_ helper: String) throws {
    let privateMarker = "private-command-marker"
    let malformed = try runHelper(helper, input: "{\(privateMarker)}\nsecond-private-line\n")

    precondition(malformed.status == 0)
    precondition(String(decoding: malformed.output, as: UTF8.self) == (
        #"{"version":1,"status":"error","error":"invalid_command"}"# + "\n"
    ))
    precondition(malformed.error.isEmpty)
    precondition(!String(decoding: malformed.output, as: UTF8.self).contains(privateMarker))

    let privateID = UInt32.max
    let emptyProtected = try runHelper(
        helper,
        input: #"{"version":1,"displays":[{"id":\#(privateID)}],"protected_window_ids":[],"overlay_window_ids":[]}"# + "\n"
    )
    precondition(emptyProtected.status == 0)
    precondition(String(decoding: emptyProtected.output, as: UTF8.self) == (
        #"{"version":1,"status":"error","error":"invalid_command"}"# + "\n"
    ))
    precondition(emptyProtected.error.isEmpty)
    precondition(!String(decoding: emptyProtected.output, as: UTF8.self).contains(String(privateID)))

    let fractionalID = "4294967295.0000000001"
    let fractional = try runHelper(
        helper,
        input: #"{"version":1,"displays":[{"id":\#(fractionalID)}],"protected_window_ids":[456],"overlay_window_ids":[]}"# + "\n"
    )
    precondition(fractional.status == 0)
    precondition(String(decoding: fractional.output, as: UTF8.self) == (
        #"{"version":1,"status":"error","error":"invalid_command"}"# + "\n"
    ))
    precondition(fractional.error.isEmpty)
    precondition(!String(decoding: fractional.output, as: UTF8.self).contains(fractionalID))

    let strictInvalidInputs = [
        #"{"version":1e0,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1.0000000000000000000000000000000000000001,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"\u0076ersion":1,"displays":[{"id":123}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
        #"{"version":1,"displays":[{"id":123,"\u0069d":124}],"protected_window_ids":[456],"overlay_window_ids":[]}"#,
    ]
    for input in strictInvalidInputs {
        let result = try runHelper(helper, input: input + "\n")
        precondition(result.status == 0)
        precondition(String(decoding: result.output, as: UTF8.self) == (
            #"{"version":1,"status":"error","error":"invalid_command"}"# + "\n"
        ))
        precondition(result.error.isEmpty)
        precondition(!String(decoding: result.output, as: UTF8.self).contains("0000000001"))
    }
}

@main
enum MacScreenCaptureCoreTests {
    static func main() async throws {
        testExactCommandDecodes()
        testUnsupportedOSPrecedesCommandParsing()
        testMalformedAndNonExactCommandsAreInvalid()
        testIDsMustBePositiveUniqueUInt32Values()
        testDimensionsMustBePairedPositiveIntegersWithinIntRange()
        testNumericFieldsRequirePlainIntegerTokens()
        testPlainIntegerTokenBoundaries()
        testResourceLimitConstantsAndCommandBoundaries()
        testDuplicateObjectMembersAreInvalidAfterKeyDecoding()
        testResolverReturnsOnlyUniqueExactTargets()
        testResolverPromotesProtectedOverlayAndAuxiliaryWindowsToOwningApplications()
        testResolverRejectsMissingAndAmbiguousDisplays()
        testResolverRejectsMissingAndAmbiguousExcludedWindows()
        testResolverRejectsMissingDuplicateAndInconsistentOwners()
        testResolverRevalidatesSafetyCriticalCommandState()
        testResolverRejectsUnrepresentableDisplayGeometry()
        testOutputDimensionsUseExplicitOrNativePixels()
        testOutputDimensionBoundariesFailClosed()
        testPrivacyFingerprintIsCanonicalAndDetectsWindowChanges()
        testPrivacyFingerprintMustIgnoreUnrelatedApplicationChanges()
        await testCaptureSequencePreparesAllTargetsBeforeProducingPNGs()
        await testFingerprintChangePreventsEveryPNGEncode()
        await testCaptureSequencePreparationFailuresPreventCapture()
        await testCaptureSequenceMidstreamFailuresReturnNoPartialDisplays()
        testFixedErrorPayloadsAreExactSingleLines()
        testAggregatePixelAndEncodedResponseResourceBoundaries()
        try testSuccessPayloadHasOnlyBoundedPublicFields()
        testInvalidSuccessBoundsReturnEncodeFailed()

        if CommandLine.arguments.count == 2 {
            try testHelperInvalidInputIsOneShotAndSilent(CommandLine.arguments[1])
        } else {
            precondition(CommandLine.arguments.count == 1)
        }
        print("MacScreenCaptureCoreTests passed")
    }
}
