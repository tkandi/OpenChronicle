enum PrivacyIndicatorStyleOption: String, CaseIterable, Identifiable {
  case off
  case border
  case shield
  case pill
  case quietShield = "quiet-shield"
  case banner

  static let defaultStyle: Self = .pill

  var id: String { rawValue }

  var title: String {
    switch self {
    case .off: return "关闭"
    case .border: return "A · 边缘框"
    case .shield: return "B1 · 盾牌"
    case .pill: return "B2 · 已保护"
    case .quietShield: return "B3 · 轻量盾牌"
    case .banner: return "C · 状态条"
    }
  }

  var systemImage: String {
    switch self {
    case .off: return "xmark.circle"
    case .border: return "rectangle.inset.filled"
    case .shield: return "shield.fill"
    case .pill: return "checkmark.shield.fill"
    case .quietShield: return "shield"
    case .banner: return "rectangle.topthird.inset.filled"
    }
  }

  var sampleText: String? {
    self == .pill ? "已保护" : nil
  }
}
