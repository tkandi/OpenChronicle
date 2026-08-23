enum PrivacyIndicatorPlacementOption: String, CaseIterable, Identifiable {
  case bottomLeftFlush = "bottom-left-flush"
  case bottomLeftInset = "bottom-left-inset"
  case bottomRightWorkArea = "bottom-right-work-area"

  static let defaultValue: Self = .bottomLeftFlush

  var id: String { rawValue }

  var title: String {
    switch self {
    case .bottomLeftFlush: return "左下角贴边"
    case .bottomLeftInset: return "左下角留白"
    case .bottomRightWorkArea: return "右下角避开 Dock"
    }
  }

  var systemImage: String {
    switch self {
    case .bottomLeftFlush: return "arrow.down.left"
    case .bottomLeftInset: return "arrow.down.left.circle"
    case .bottomRightWorkArea: return "arrow.down.right"
    }
  }

  static func isEnabled(for style: PrivacyIndicatorStyleOption) -> Bool {
    style != .off && style != .banner
  }
}
