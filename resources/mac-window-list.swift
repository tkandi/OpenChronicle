// mac-window-list - enumerate on-screen windows for screenshot privacy checks.

import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation

struct DisplayRecord: Codable {
    let id: UInt32
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let is_primary: Bool
}

struct WindowRecord: Codable {
    let app_name: String
    let bundle_id: String
    let title: String
    let alternate_title: String?
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let is_active: Bool
    let title_available: Bool
    let is_active_candidate: Bool
    let window_id: UInt32?
}

struct Output: Codable {
    let windows: [WindowRecord]
    let displays: [DisplayRecord]
}

private struct CGWindowSource {
    let metadata: OnScreenCGWindow
    let appName: String
    let bundleID: String
}

private struct AXWindowSource {
    let metadata: AXWindowMetadata
    let element: AXUIElement
}

private typealias AXUIElementGetWindowFunction = @convention(c) (
    AXUIElement,
    UnsafeMutablePointer<CGWindowID>
) -> AXError

private let windowIdentityFrameworkPaths = [
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices",
    "/System/Library/Frameworks/ApplicationServices.framework/Versions/A/Frameworks/"
        + "HIServices.framework/HIServices",
]

private func resolveAXUIElementGetWindow() -> AXUIElementGetWindowFunction? {
    for path in windowIdentityFrameworkPaths {
        guard let handle = dlopen(path, RTLD_NOW | RTLD_LOCAL) else { continue }
        guard let symbol = dlsym(handle, "_AXUIElementGetWindow") else {
            dlclose(handle)
            continue
        }
        // Keep the framework handle open for the helper's short process lifetime.
        return unsafeBitCast(symbol, to: AXUIElementGetWindowFunction.self)
    }
    return nil
}

func axAttribute(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    return error == .success ? value : nil
}

func axString(_ element: AXUIElement, _ attribute: String) -> String? {
    return axAttribute(element, attribute) as? String
}

func axBool(_ element: AXUIElement, _ attribute: String) -> Bool? {
    return axAttribute(element, attribute) as? Bool
}

private func axWindowSources(
    pid: pid_t,
    frontmostPID: pid_t?,
    getWindowID: AXUIElementGetWindowFunction
) -> [AXWindowSource] {
    let app = AXUIElementCreateApplication(pid)
    guard
        let rawWindows = axAttribute(app, kAXWindowsAttribute as String),
        let axWindows = rawWindows as? [AXUIElement]
    else { return [] }

    let focusedWindow = axAttribute(app, kAXFocusedWindowAttribute as String)

    var sources: [AXWindowSource] = []
    for window in axWindows {
        if axBool(window, kAXMinimizedAttribute as String) == true { continue }
        var resolvedWindowID = kCGNullWindowID
        let identityError = getWindowID(window, &resolvedWindowID)
        let windowID = identityError == .success && resolvedWindowID != kCGNullWindowID
            ? resolvedWindowID
            : nil
        sources.append(AXWindowSource(
            metadata: AXWindowMetadata(
                windowID: windowID,
                ownerPID: pid,
                isFocused: pid == frontmostPID
                    && focusedWindow.map { CFEqual(window, $0) } == true
            ),
            element: window
        ))
    }
    return sources
}

#if !TESTING
@main
#endif
enum MacWindowList {
    static func main() {
        if #available(macOS 10.15, *), !CGPreflightScreenCaptureAccess() {
            fputs("Screen Recording permission is not granted\n", stderr)
            exit(2)
        }

        guard let windowInfo = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else {
            fputs("Could not enumerate visible windows\n", stderr)
            exit(1)
        }

        guard let getWindowID = resolveAXUIElementGetWindow() else {
            fputs("Could not resolve window identity API\n", stderr)
            exit(3)
        }

        var cgSources: [CGWindowSource] = []
        var visiblePIDs = Set<pid_t>()
        let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
        for info in windowInfo {
            let alpha = info[kCGWindowAlpha as String] as? Double ?? 0
            guard alpha > 0 else { continue }

            guard
                let layer = info[kCGWindowLayer as String] as? NSNumber,
                layer.intValue == 0,
                let bounds = info[kCGWindowBounds as String] as? [String: Any],
                let left = (bounds["X"] as? NSNumber)?.doubleValue,
                let top = (bounds["Y"] as? NSNumber)?.doubleValue,
                let width = (bounds["Width"] as? NSNumber)?.doubleValue,
                let height = (bounds["Height"] as? NSNumber)?.doubleValue,
                width > 0,
                height > 0
            else { continue }

            let rawPID = (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value ?? 0
            let pid = rawPID > 0 ? rawPID : 0
            let windowID = (info[kCGWindowNumber as String] as? NSNumber).flatMap { value in
                let identifier = value.uint32Value
                return identifier == kCGNullWindowID ? nil : identifier
            }
            let app = pid > 0 ? NSRunningApplication(processIdentifier: pid) : nil
            let appName = info[kCGWindowOwnerName as String] as? String ?? ""
            let bundleID = app?.bundleIdentifier ?? ""
            let title = info[kCGWindowName as String] as? String ?? ""
            let metadata = OnScreenCGWindow(
                windowID: windowID,
                ownerPID: pid,
                layer: layer.intValue,
                bounds: WindowBounds(left: left, top: top, width: width, height: height),
                title: title
            )
            cgSources.append(CGWindowSource(
                metadata: metadata,
                appName: appName,
                bundleID: bundleID
            ))
            if pid > 0 {
                visiblePIDs.insert(pid)
            }
        }

        // Phase one records AX elements and identities without reading titles.
        var axSources: [AXWindowSource] = []
        for pid in visiblePIDs.sorted() {
            axSources.append(contentsOf: axWindowSources(
                pid: pid,
                frontmostPID: frontmostPID,
                getWindowID: getWindowID
            ))
        }

        let cgWindows = cgSources.map(\.metadata)
        let axWindows = axSources.map(\.metadata)
        let resolvedMetadata = resolvedWindowMetadata(
            cgWindows: cgWindows,
            axWindows: axWindows,
            frontmostPID: frontmostPID,
            readAXTitle: { axIndex in
                axString(axSources[axIndex].element, kAXTitleAttribute as String)
            }
        )
        let windows = zip(cgSources, resolvedMetadata).map { source, resolved in
            WindowRecord(
                app_name: source.appName,
                bundle_id: source.bundleID,
                title: resolved.title,
                alternate_title: resolved.alternateTitle,
                left: source.metadata.bounds.left,
                top: source.metadata.bounds.top,
                width: source.metadata.bounds.width,
                height: source.metadata.bounds.height,
                is_active: resolved.isActive,
                title_available: resolved.titleAvailable,
                is_active_candidate: resolved.isActiveCandidate,
                window_id: windowRecordID(from: source.metadata)
            )
        }

        var displayCount: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &displayCount) == .success else {
            fputs("Could not enumerate active displays\n", stderr)
            exit(1)
        }
        var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(displayCount))
        guard CGGetActiveDisplayList(displayCount, &displayIDs, &displayCount) == .success else {
            fputs("Could not enumerate active displays\n", stderr)
            exit(1)
        }
        let displays = displayIDs.prefix(Int(displayCount)).map { displayID in
            let bounds = CGDisplayBounds(displayID)
            return DisplayRecord(
                id: displayID,
                left: bounds.origin.x,
                top: bounds.origin.y,
                width: bounds.width,
                height: bounds.height,
                is_primary: CGDisplayIsMain(displayID) != 0
            )
        }

        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys]
            let data = try encoder.encode(Output(windows: windows, displays: displays))
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            fputs("Could not encode window metadata\n", stderr)
            exit(1)
        }
    }
}
