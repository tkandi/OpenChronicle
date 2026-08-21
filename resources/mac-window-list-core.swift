import Foundation

struct WindowBounds: Equatable {
    let left: Double
    let top: Double
    let width: Double
    let height: Double
}

func shouldIncludeAXFallback(
    _ axBounds: WindowBounds,
    onScreenBounds: [WindowBounds]
) -> Bool {
    onScreenBounds.contains { cgBounds in
        let overlapWidth = max(
            0,
            min(axBounds.left + axBounds.width, cgBounds.left + cgBounds.width)
                - max(axBounds.left, cgBounds.left)
        )
        let overlapHeight = max(
            0,
            min(axBounds.top + axBounds.height, cgBounds.top + cgBounds.height)
                - max(axBounds.top, cgBounds.top)
        )
        return overlapWidth > 0 && overlapHeight > 0
    }
}
