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
    @AppStorage(MapTouchLab.showPOIKey) private var showPOI = false
    @AppStorage(MapTouchLab.noUserDotKey) private var noUserDot = false
    @AppStorage(MapTouchLab.noPinsKey) private var noPins = false
    @AppStorage(MapTouchLab.standardTypeKey) private var standardType = false

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
                Toggle("6. Show points of interest", isOn: $showPOI)
                Toggle("7. Hide my location dot", isOn: $noUserDot)
                Toggle("8. No parking pins", isOn: $noPins)
                Toggle("9. Standard map type", isOn: $standardType)
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
    case noPOI, userDot, pins, southMountain, areaSettings, everything
    case swiftUINoPOI, swiftUIMarkers, exploreCopy, areaCopy
    var id: String { rawValue }
    var title: String {
        switch self {
        case .bareFull: return "A. Bare map, full screen"
        case .bareUnderSheet: return "B. Bare map with a sheet over it"
        case .bareInSheet: return "C. Bare map inside a sheet"
        case .swiftUIMap: return "D. Apple SwiftUI map, full screen"
        case .noPOI: return "E. Bare map, points of interest hidden"
        case .userDot: return "F. Bare map with my location dot"
        case .pins: return "G. Bare map with 20 pins"
        case .southMountain: return "H. Bare map zoomed to South Mountain"
        case .areaSettings: return "I. Bare map with area map settings"
        case .everything: return "J. Bare map with E to I together"
        case .swiftUINoPOI: return "K. SwiftUI map, flat, points of interest hidden"
        case .swiftUIMarkers: return "L. SwiftUI map with 20 markers"
        case .exploreCopy: return "M. Copy of Explore: map in a sheet with a title bar"
        case .areaCopy: return "N. Copy of an area: map J under a scrolling list sheet"
        }
    }
    var config: BareMapConfig {
        switch self {
        case .noPOI: return BareMapConfig(noPOI: true)
        case .userDot: return BareMapConfig(userDot: true)
        case .pins: return BareMapConfig(pins: true)
        case .southMountain: return BareMapConfig(region: true)
        case .areaSettings: return BareMapConfig(areaSettings: true)
        case .everything:
            return BareMapConfig(noPOI: true, userDot: true, pins: true, region: true, areaSettings: true)
        default: return BareMapConfig()
        }
    }
}

/// Which area-map features a bare lab map adds.
struct BareMapConfig {
    var noPOI = false
    var userDot = false
    var pins = false
    var region = false
    var areaSettings = false
}

/// Public center of South Mountain Park and Preserve (park data, not a
/// user position). Used for the zoomed lab maps and the lab pins.
let labParkCenter = CLLocationCoordinate2D(latitude: 33.33, longitude: -112.06)

func labPinCoordinates() -> [CLLocationCoordinate2D] {
    var out: [CLLocationCoordinate2D] = []
    for i in 0..<20 {
        let row: Double = Double(i % 5) - 2.0
        let col: Double = Double(i / 5) - 2.0
        let lat: Double = labParkCenter.latitude + row * 0.01
        let lon: Double = labParkCenter.longitude + col * 0.012
        out.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
    }
    return out
}

private let labParkRegion = MKCoordinateRegion(
    center: labParkCenter, latitudinalMeters: 20_000, longitudinalMeters: 20_000)

/// Hosts one lab screen. B mirrors the area screen (a map with a
/// non-dismissable sheet over it, background interaction on). C mirrors
/// Explore (a map inside a sheet).
private struct LabScreenHost: View {
    let screen: LabScreen
    @Environment(\.dismiss) private var dismiss
    @State private var showSheet = false

    var body: some View {
        ZStack(alignment: .topLeading) {
            background
            Button("Done") { showSheet = false; dismiss() }
                .buttonStyle(.borderedProminent)
                .padding()
        }
        .onAppear {
            TouchDiag.log("lab screen=\(screen.rawValue)")
            showSheet = [.bareUnderSheet, .bareInSheet, .exploreCopy, .areaCopy].contains(screen)
        }
        .sheet(isPresented: $showSheet) { sheetContent }
    }

    @ViewBuilder private var background: some View {
        switch screen {
        case .bareInSheet, .exploreCopy:
            Color(.systemBackground).ignoresSafeArea()
        case .areaCopy:
            BareMapView(config: LabScreen.everything.config).ignoresSafeArea()
        case .swiftUIMap:
            Map().ignoresSafeArea()
        case .swiftUINoPOI:
            Map(initialPosition: .region(labParkRegion))
                .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
                .ignoresSafeArea()
        case .swiftUIMarkers:
            markerMap.ignoresSafeArea()
        default:
            BareMapView(config: screen.config).ignoresSafeArea()
        }
    }

    private var markerMap: some View {
        Map(initialPosition: .region(labParkRegion)) {
            ForEach(0..<20, id: \.self) { i in
                Marker("Pin \(i)", systemImage: "mountain.2.fill", coordinate: labPinCoordinates()[i])
            }
        }
    }

    @ViewBuilder private var sheetContent: some View {
        switch screen {
        case .exploreCopy: exploreCopySheet
        case .areaCopy: areaCopySheet
        case .bareUnderSheet: underSheet
        default: inSheet
        }
    }

    private var exploreCopySheet: some View {
        NavigationStack {
            markerMap
                .mapStyle(.standard(elevation: .flat, pointsOfInterest: .excludingAll))
                .navigationTitle("All Areas")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Done") { showSheet = false }
                    }
                }
        }
    }

    private var areaCopySheet: some View {
        List(0..<40, id: \.self) { i in Text("Test trail \(i)") }
            .ignoresSafeArea(edges: .bottom)
            .presentationDetents([.height(240), .medium, .large])
            .presentationDragIndicator(.visible)
            .presentationBackgroundInteraction(.enabled)
            .presentationContentInteraction(.scrolls)
            .presentationCornerRadius(20)
            .presentationBackground { Color(.secondarySystemBackground).ignoresSafeArea() }
            .interactiveDismissDisabled()
    }

    private var underSheet: some View {
        Text("Test sheet. Drag the map above me.")
            .padding()
            .presentationDetents([.height(240), .medium])
            .presentationBackgroundInteraction(.enabled)
            .presentationContentInteraction(.scrolls)
            .interactiveDismissDisabled()
    }

    private var inSheet: some View {
        VStack(spacing: 0) {
            Button("Close sheet") { showSheet = false }.padding()
            BareMapView()
        }
    }
}

/// A plain MKMapView in a view controller. With the default config it has
/// zero customization; each config flag adds one thing the area map has.
struct BareMapView: UIViewControllerRepresentable {
    var config = BareMapConfig()

    func makeUIViewController(context: Context) -> UIViewController {
        let vc = UIViewController()
        let mv = MKMapView()
        if config.noPOI { mv.pointOfInterestFilter = .excludingAll }
        if config.userDot { mv.showsUserLocation = true }
        if config.pins {
            for (i, c) in labPinCoordinates().enumerated() {
                let a = MKPointAnnotation()
                a.coordinate = c
                a.title = "Pin \(i)"
                mv.addAnnotation(a)
            }
        }
        if config.region { mv.setRegion(labParkRegion, animated: false) }
        if config.areaSettings {
            // Same settings MapKitMapView.makeUIView applies.
            mv.mapType = (UserDefaults.standard.string(forKey: StorageKeys.mapStyle)
                .flatMap(MapStylePreference.init(rawValue:)) ?? .standard).mkMapType
            mv.isPitchEnabled = false
            mv.isRotateEnabled = true
            mv.showsCompass = true
            mv.showsScale = true
            mv.layoutMargins = UIEdgeInsets(top: 60, left: 0, bottom: 0, right: 0)
        }
        vc.view = mv
        TouchDiag.log("lab bare MKMapView created config=\(config)")
        return vc
    }
    func updateUIViewController(_ vc: UIViewController, context: Context) {}
}
