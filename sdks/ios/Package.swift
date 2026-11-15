// swift-tools-version: 6.1
import PackageDescription

let package = Package(
    name: "MegooFlags",
    platforms: [.iOS(.v15), .macOS(.v12)],
    products: [
        .library(name: "MegooFlags", targets: ["MegooFlags"])
    ],
    dependencies: [
        .package(url: "https://github.com/open-feature/swift-sdk.git", .upToNextMinor(from: "0.7.0"))
    ],
    targets: [
        .target(
            name: "MegooFlags",
            dependencies: [.product(name: "OpenFeature", package: "swift-sdk")]
        ),
        .testTarget(
            name: "MegooFlagsTests",
            dependencies: ["MegooFlags", .product(name: "OpenFeature", package: "swift-sdk")]
        ),
        .testTarget(
            name: "MegooFlagsE2ETests",
            dependencies: ["MegooFlags", .product(name: "OpenFeature", package: "swift-sdk")]
        ),
    ]
)
