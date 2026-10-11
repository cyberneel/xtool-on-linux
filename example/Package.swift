// swift-tools-version: 6.2

import PackageDescription

// The app and each extension are library products; xtool turns them into bundles.
// MyAppShared holds what both sides need (ActivityAttributes, App Intents used by widget buttons).
let package = Package(
    name: "MyApp",
    platforms: [
        .iOS(.v26),
        .macOS(.v14),
    ],
    products: [
        .library(name: "MyApp", targets: ["MyApp"]),
        .library(name: "MyAppWidget", targets: ["MyAppWidget"]),
    ],
    targets: [
        .target(name: "MyApp", dependencies: ["MyAppShared"]),
        .target(name: "MyAppWidget", dependencies: ["MyAppShared"]),
        .target(name: "MyAppShared"),
    ]
)
