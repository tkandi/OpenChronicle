import Foundation

enum OverlayReasonTrigger: String, Codable {
    case always
    case hover
    case click
}

struct OverlayReason: Codable, Equatable {
    let code: String
    let displayID: UInt32?
    let sourceDisplayID: UInt32?
    let appName: String?
    let bundleID: String?
    let windowTitle: String?
    let rule: String?
    let effectiveResumeAt: String?

    enum CodingKeys: String, CodingKey {
        case code
        case displayID = "display_id"
        case sourceDisplayID = "source_display_id"
        case appName = "app_name"
        case bundleID = "bundle_id"
        case windowTitle = "window_title"
        case rule
        case effectiveResumeAt = "effective_resume_at"
    }

    init(
        code: String,
        displayID: UInt32?,
        sourceDisplayID: UInt32?,
        appName: String?,
        bundleID: String?,
        windowTitle: String?,
        rule: String?,
        effectiveResumeAt: String? = nil
    ) {
        self.code = code
        self.displayID = displayID
        self.sourceDisplayID = sourceDisplayID
        self.appName = appName
        self.bundleID = bundleID
        self.windowTitle = windowTitle
        self.rule = rule
        self.effectiveResumeAt = effectiveResumeAt
    }

    func presentationText(includeExactValues: Bool) -> String {
        let category = categoryText
        guard includeExactValues else { return category }

        let details = [
            appName.map { "应用: \(boundedReasonValue($0))" },
            bundleID.map { "标识: \(boundedReasonValue($0))" },
            windowTitle.map { "标题: \(boundedReasonValue($0))" },
            rule.map { "规则: \(boundedReasonValue($0))" },
            effectiveResumeAt.map { "恢复: \(boundedReasonValue($0))" },
        ].compactMap { $0 }
        return ([category] + details).joined(separator: " · ")
    }

    private var categoryText: String {
        switch code {
        case "app_rule": return "应用规则"
        case "bundle_rule": return "Bundle ID 规则"
        case "window_title_rule": return "窗口标题规则"
        case "window_title_unknown": return "窗口标题未确认"
        case "mode_all_inherited": return "全屏模式继承保护"
        case "diagnostics_reveal": return "诊断详情正在显示"
        case "diagnostics_guard_invalid": return "诊断保护状态无效"
        case "manual_pause": return "手动暂停"
        case "timed_pause": return "定时暂停"
        case "timed_pause_waiting": return "定时暂停等待安全确认"
        case "pause_state_unavailable": return "暂停状态不可用"
        case "inventory_unavailable": return "窗口清单不可用"
        case "helper_exit": return "窗口助手已退出"
        case "helper_parse": return "窗口助手响应无效"
        case "empty_displays": return "未检测到显示器"
        case "invalid_display_inventory": return "显示器清单无效"
        case "multiple_active_windows": return "检测到多个活动窗口"
        case "active_window_unmapped": return "活动窗口无法定位到显示器"
        case "sensitive_window_unmapped": return "敏感窗口无法定位到显示器"
        case "indicator_unconfirmed": return "隐私标识未确认"
        default: return "隐私保护"
        }
    }
}

struct ReasonRevealState {
    let trigger: OverlayReasonTrigger
    private(set) var isExpanded: Bool

    init(trigger: OverlayReasonTrigger) {
        self.trigger = trigger
        isExpanded = trigger == .always
    }

    mutating func update(pointerInside: Bool) {
        guard trigger == .hover else { return }
        isExpanded = pointerInside
    }

    mutating func click() {
        guard trigger == .click else { return }
        isExpanded.toggle()
    }
}

func overlayReasonLines(
    _ reasons: [OverlayReason],
    includeExactValues: Bool,
    maximumLines: Int = 3
) -> [String] {
    guard maximumLines > 0, !reasons.isEmpty else { return [] }
    let lines = reasons.map { $0.presentationText(includeExactValues: includeExactValues) }
    guard lines.count > maximumLines else { return lines }
    guard maximumLines > 1 else { return ["+\(lines.count)"] }
    let visibleCount = maximumLines - 1
    return Array(lines.prefix(visibleCount)) + ["+\(lines.count - visibleCount)"]
}

private func boundedReasonValue(_ value: String) -> String {
    let cleaned = value.unicodeScalars.map { scalar -> String in
        CharacterSet.controlCharacters.contains(scalar) ? " " : String(scalar)
    }.joined()
    guard cleaned.count > 160 else { return cleaned }
    return String(cleaned.prefix(159)) + "…"
}
