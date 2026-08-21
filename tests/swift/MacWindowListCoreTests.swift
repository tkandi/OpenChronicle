import Foundation

@main
enum MacWindowListCoreTests {
    static func main() {
        let onScreen = [WindowBounds(left: 100, top: 20, width: 300, height: 200)]

        precondition(
            shouldIncludeAXFallback(
                WindowBounds(left: 120, top: 30, width: 200, height: 100),
                onScreenBounds: onScreen
            )
        )
        precondition(
            !shouldIncludeAXFallback(
                WindowBounds(left: 900, top: 30, width: 200, height: 100),
                onScreenBounds: onScreen
            )
        )
        precondition(
            !shouldIncludeAXFallback(
                WindowBounds(left: 400, top: 30, width: 20, height: 100),
                onScreenBounds: onScreen
            )
        )

        print("MacWindowListCoreTests passed")
    }
}
