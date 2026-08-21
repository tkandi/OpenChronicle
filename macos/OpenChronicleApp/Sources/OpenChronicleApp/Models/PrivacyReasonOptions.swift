enum PrivacyReasonDisplayOption: String, CaseIterable, Identifiable {
  case overlay
  case diagnostics
  case hybrid

  static let defaultValue: Self = .hybrid

  var id: String { rawValue }

  var title: String {
    switch self {
    case .overlay: return "遮罩提示"
    case .diagnostics: return "诊断信息"
    case .hybrid: return "混合显示"
    }
  }

  var detail: String {
    switch self {
    case .overlay: return "在已保护区域显示简洁提示。"
    case .diagnostics: return "显示隐私保护的诊断原因。"
    case .hybrid: return "同时提供提示和诊断信息。"
    }
  }

  var systemImage: String {
    switch self {
    case .overlay: return "rectangle.inset.filled"
    case .diagnostics: return "stethoscope"
    case .hybrid: return "rectangle.3.group.fill"
    }
  }
}

enum PrivacyReasonDetailOption: String, CaseIterable, Identifiable {
  case category
  case exact
  case tiered

  static let defaultValue: Self = .exact

  var id: String { rawValue }

  var title: String {
    switch self {
    case .category: return "类别"
    case .exact: return "精确规则"
    case .tiered: return "分级详情"
    }
  }

  var detail: String {
    switch self {
    case .category: return "仅显示保护原因的类别。"
    case .exact: return "显示触发保护的精确原因。"
    case .tiered: return "按敏感程度显示不同详情。"
    }
  }

  var systemImage: String {
    switch self {
    case .category: return "tag"
    case .exact: return "scope"
    case .tiered: return "line.3.horizontal.decrease.circle"
    }
  }
}

enum PrivacyReasonTriggerOption: String, CaseIterable, Identifiable {
  case always
  case hover
  case click

  static let defaultValue: Self = .hover

  var id: String { rawValue }

  var title: String {
    switch self {
    case .always: return "始终显示"
    case .hover: return "悬停显示"
    case .click: return "点按显示"
    }
  }

  var detail: String {
    switch self {
    case .always: return "持续显示隐私保护原因。"
    case .hover: return "将指针停留时显示原因。"
    case .click: return "点按后显示原因。"
    }
  }

  var systemImage: String {
    switch self {
    case .always: return "eye"
    case .hover: return "cursorarrow"
    case .click: return "hand.tap"
    }
  }
}
