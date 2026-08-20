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

  var previewDescriptor: PrivacyIndicatorPreviewDescriptor {
    switch self {
    case .off:
      return PrivacyIndicatorPreviewDescriptor(composition: .none, placement: .none, text: nil)
    case .border:
      return PrivacyIndicatorPreviewDescriptor(
        composition: .borderAndBadge,
        placement: .lowerTrailing,
        text: "已保护"
      )
    case .shield:
      return PrivacyIndicatorPreviewDescriptor(
        composition: .solidShield,
        placement: .lowerTrailing,
        text: nil
      )
    case .pill:
      return PrivacyIndicatorPreviewDescriptor(
        composition: .pill,
        placement: .lowerTrailing,
        text: "已保护"
      )
    case .quietShield:
      return PrivacyIndicatorPreviewDescriptor(
        composition: .quietShield,
        placement: .lowerTrailing,
        text: nil
      )
    case .banner:
      return PrivacyIndicatorPreviewDescriptor(composition: .banner, placement: .top, text: "已保护")
    }
  }
}

struct PrivacyIndicatorPreviewDescriptor: Equatable {
  enum Composition: Equatable {
    case none
    case borderAndBadge
    case solidShield
    case pill
    case quietShield
    case banner
  }

  enum Placement: Equatable {
    case none
    case lowerTrailing
    case top
  }

  let composition: Composition
  let placement: Placement
  let text: String?
}
