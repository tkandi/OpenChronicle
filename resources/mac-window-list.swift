// mac-window-list - enumerate on-screen windows for screenshot privacy checks.

import AppKit
import ApplicationServices
import CoreGraphics
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
    let left: Double
    let top: Double
    let width: Double
    let height: Double
    let is_active: Bool
}

struct Output: Codable {
    let windows: [WindowRecord]
    let displays: [DisplayRecord]
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

func axPoint(_ element: AXUIElement, _ attribute: String) -> CGPoint? {
    guard let raw = axAttribute(element, attribute) else { return nil }
    let value = raw as! AXValue
    guard AXValueGetType(value) == .cgPoint else { return nil }
    var point = CGPoint.zero
    return AXValueGetValue(value, .cgPoint, &point) ? point : nil
}

func axSize(_ element: AXUIElement, _ attribute: String) -> CGSize? {
    guard let raw = axAttribute(element, attribute) else { return nil }
    let value = raw as! AXValue
    guard AXValueGetType(value) == .cgSize else { return nil }
    var size = CGSize.zero
    return AXValueGetValue(value, .cgSize, &size) ? size : nil
}

func axWindowRecords(
    pid: pid_t,
    appName: String,
    bundleID: String,
    frontmostPID: pid_t?
) -> [WindowRecord] {
    let app = AXUIElementCreateApplication(pid)
    guard
        let rawWindows = axAttribute(app, kAXWindowsAttribute as String),
        let axWindows = rawWindows as? [AXUIElement]
    else { return [] }

    let focusedWindow = axAttribute(app, kAXFocusedWindowAttribute as String)

    var records: [WindowRecord] = []
    for window in axWindows {
        if axBool(window, kAXMinimizedAttribute as String) == true { continue }
        guard
            let title = axString(window, kAXTitleAttribute as String),
            !title.isEmpty,
            let position = axPoint(window, kAXPositionAttribute as String),
            let size = axSize(window, kAXSizeAttribute as String),
            size.width > 0,
            size.height > 0
        else { continue }

        records.append(WindowRecord(
            app_name: appName,
            bundle_id: bundleID,
            title: title,
            left: position.x,
            top: position.y,
            width: size.width,
            height: size.height,
            is_active: pid == frontmostPID && focusedWindow.map { CFEqual(window, $0) } == true
        ))
    }
    return records
}

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

var windows: [WindowRecord] = []
var visibleApps: [pid_t: (name: String, bundleID: String)] = [:]
let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
for info in windowInfo {
    let alpha = info[kCGWindowAlpha as String] as? Double ?? 0
    guard alpha > 0 else { continue }

    guard
        let bounds = info[kCGWindowBounds as String] as? [String: Any],
        let left = (bounds["X"] as? NSNumber)?.doubleValue,
        let top = (bounds["Y"] as? NSNumber)?.doubleValue,
        let width = (bounds["Width"] as? NSNumber)?.doubleValue,
        let height = (bounds["Height"] as? NSNumber)?.doubleValue,
        width > 0,
        height > 0
    else { continue }

    let pid = info[kCGWindowOwnerPID as String] as? pid_t ?? 0
    let app = pid > 0 ? NSRunningApplication(processIdentifier: pid) : nil
    let appName = info[kCGWindowOwnerName as String] as? String ?? ""
    let bundleID = app?.bundleIdentifier ?? ""
    if pid > 0 {
        visibleApps[pid] = (name: appName, bundleID: bundleID)
    }
    windows.append(WindowRecord(
        app_name: appName,
        bundle_id: bundleID,
        title: info[kCGWindowName as String] as? String ?? "",
        left: left,
        top: top,
        width: width,
        height: height,
        is_active: false
    ))
}

// CGWindowName can be empty for background browser windows even with Screen Recording
// permission. Query only AX window metadata as a fallback; never traverse window contents.
for (pid, metadata) in visibleApps {
    windows.append(contentsOf: axWindowRecords(
        pid: pid,
        appName: metadata.name,
        bundleID: metadata.bundleID,
        frontmostPID: frontmostPID
    ))
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
    fputs("Could not encode window metadata: \(error)\n", stderr)
    exit(1)
}
