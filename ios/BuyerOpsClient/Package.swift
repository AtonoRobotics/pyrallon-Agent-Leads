// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "BuyerOpsClient",
    platforms: [.iOS(.v17)],
    products: [.library(name: "BuyerOpsClient", targets: ["BuyerOpsClient"])],
    targets: [
        .target(name: "BuyerOpsClient"),
        .testTarget(name: "BuyerOpsClientTests", dependencies: ["BuyerOpsClient"]),
    ]
)
