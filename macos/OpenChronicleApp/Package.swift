// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "OpenChronicleApp",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "OpenChronicle", targets: ["OpenChronicleApp"]),
    ],
    targets: [
        .executableTarget(
            name: "OpenChronicleApp",
            path: "Sources/OpenChronicleApp"
        ),
        .testTarget(
            name: "OpenChronicleAppTests",
            dependencies: ["OpenChronicleApp"],
            path: "Tests/OpenChronicleAppTests"
        ),
    ],
    swiftLanguageVersions: [.v5]
)
