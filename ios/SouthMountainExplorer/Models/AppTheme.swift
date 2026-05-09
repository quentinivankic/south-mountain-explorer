import SwiftUI

enum AppTheme: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: return "Match Phone"
        case .light:  return "Light"
        case .dark:   return "Dark"
        }
    }

    /// nil means "let the system decide" — preferredColorScheme(nil)
    /// lets the view follow the user's system-wide light/dark setting.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light:  return .light
        case .dark:   return .dark
        }
    }
}
