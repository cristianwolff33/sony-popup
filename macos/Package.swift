// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "SonyPopup",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "SonyPopup",
            path: "Sources/SonyPopup",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("SwiftUI"),
                .linkedFramework("IOBluetooth"),
                .linkedFramework("ServiceManagement"),
            ]
        )
    ]
)
