import MapKit
import SwiftUI
import UIKit

/// DIAGNOSTICS ONLY (chat/map-touch-diag-ios273). Never merge into a fix.
/// The Map Touch Lab: bare map test screens plus the bisect switches.
struct MapTouchLabView: View {
    @AppStorage(MapTouchLab.noCameraWritesKey) private var noCameraWrites = false
    @AppStorage(MapTouchLab.noOverlaysKey) private var noOverlays = false
    @AppStorage(MapTouchLab.noDelegateKey) private var noDelegate = false
    @AppStorage(MapTouchLab.noSelectionTapKey) private var noSelectionTap = false
    @AppStorage(MapTouchLab.noSheetPanKey) private var noSheetPan = false

    @State private var screen: LabScreen?

    var body: some View {
        List {
            Section {
                Text("Open each test map and drag it. Note which ones move. Tap Done (top left) to come back.")
                    .font(.footnote)
            }
            Section("Test maps") {
                ForEach(LabScreen.allCases) { s in
                    Button(s.title) { screen = s }
                        .accessibilityIdentifier("lab-\(s.rawValue)")
                }
            }
            Section {
                Toggle("1. No camera moves by the app", isOn: $noCameraWrites)
                Toggle("2. No trail lines or overlays", isOn: $noOverlays)
                Toggle("3. No map delegate", isOn: $noDelegate)
                Toggle("4. No trail tap handler", isOn: $noSelectionTap)
                Toggle("5. Turn off sheet drag handling", isOn: $noSheetPan)
            } header: {
                Text("Area map switches")
            } footer: {
                Text("Turn on ONE switch, close any open area, then open South Mountain and drag the map. Turn it off again before trying the next one. Switch 5 also affects the trail list sheet.")
            }
        }
        .navigationTitle("Map Touch Lab")
        .fullScreenCover(item: $screen) { s in LabScreenHost(screen: s) }
    }
}

enum LabScreen: String, CaseIterable, Identifiable {
    case bareFull, bareUnderSheet, bareInSheet, swiftUIMap
    var id: String { rawValue }
    var title: String {
        switch self {
        case .bareFull: return "A. Bare map, full screen"
        case .bareUnderSheet: return "B. Bare map with a sheet over it"
        case .bareInSheet: return "C. Bare map inside a sheet"
        case .swiftUIMap: return "D. Apple SwiftUI map, full screen"
        }
    }
}

/// Hosts one lab screen. B mirrors the area screen (a map with a
/// non-dismissable sheet over it, background interaction on). C mirrors
/// Explore (a map inside a sheet).
private struct LabScreenHost: View {
    let screen: LabScreen
    @Environment(\.dismiss) private var dismiss
    @State private var showSheet = false

    var body: some View {
        ZStack(alignment: .topLeading) {
            switch screen {
            case .bareFull, .bareUnderSheet:
                BareMapView().ignoresSafeArea()
            case .bareInSheet:
                Color(.systemBackground).ignoresSafeArea()
            case .swiftUIMap:
                Map().ignoresSafeArea()
            }
            Button("Done") { showSheet = false; dismiss() }
                .buttonStyle(.borderedProminent)
                .padding()
        }
        .onAppear {
            TouchDiag.log("lab screen=\(screen.rawValue)")
            showSheet = screen == .bareUnderSheet || screen == .bareInSheet
        }
        .sheet(isPresented: $showSheet) {
            if screen == .bareUnderSheet {
                Text("Test sheet. Drag the map above me.")
                    .padding()
                    .presentationDetents([.height(240), .medium])
                    .presentationBackgroundInteraction(.enabled)
                    .presentationContentInteraction(.scrolls)
                    .interactiveDismissDisabled()
            } else {
                VStack(spacing: 0) {
                    Button("Close sheet") { showSheet = false }.padding()
                    BareMapView()
                }
            }
        }
    }
}

/// A plain MKMapView in a view controller, with zero customization.
struct BareMapView: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> UIViewController {
        let vc = UIViewController()
        vc.view = MKMapView()
        TouchDiag.log("lab bare MKMapView created")
        return vc
    }
    func updateUIViewController(_ vc: UIViewController, context: Context) {}
}
