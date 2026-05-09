import SwiftUI
import UIKit
import MapKit

/// 1080² square share card. When a `mapImage` is supplied (a pre-rendered
/// MKMapSnapshotter.Snapshot with the GPS path drawn on top), it's used as the
/// background; otherwise we fall back to a gradient. Stats overlay sits on
/// a dark scrim at the bottom so the text is legible regardless of which
/// background is in play.
struct ShareableHikeCard: View {
    let hike: SavedRecording
    let areaName: String
    let mapImage: UIImage?

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            background

            // Top branding pill
            HStack(spacing: 8) {
                Image(systemName: "mountain.2.fill")
                    .font(.title2)
                Text("South Mountain Explorer")
                    .font(.headline)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(.black.opacity(0.45), in: Capsule())
            .padding(48)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            // Bottom scrim + stats
            VStack(alignment: .leading, spacing: 0) {
                Spacer()
                LinearGradient(
                    colors: [.clear, .black.opacity(0.85)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 480)
                .overlay(alignment: .bottomLeading) {
                    statsOverlay
                        .padding(48)
                }
            }
        }
        .frame(width: 1080, height: 1080)
    }

    @ViewBuilder
    private var background: some View {
        if let img = mapImage {
            Image(uiImage: img)
                .resizable()
                .scaledToFill()
                .frame(width: 1080, height: 1080)
                .clipped()
        } else {
            LinearGradient(
                colors: [.cyan, .indigo],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    private var statsOverlay: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(areaName)
                .font(.system(size: 56, weight: .bold))
                .foregroundStyle(.white)
                .lineLimit(2)

            HStack(spacing: 28) {
                stat(value: String(format: "%.2f", hike.distanceMi), unit: "mi", label: "Distance")
                stat(value: durationValue, unit: durationUnit, label: "Duration")
                if !hike.completedTrailIds.isEmpty {
                    stat(value: "\(hike.completedTrailIds.count)",
                         unit: hike.completedTrailIds.count == 1 ? "trail" : "trails",
                         label: "Completed")
                }
            }

            Text(dateString)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.75))
        }
    }

    private func stat(value: String, unit: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .lastTextBaseline, spacing: 4) {
                Text(value)
                    .font(.system(size: 40, weight: .bold).monospacedDigit())
                    .foregroundStyle(.white)
                Text(unit)
                    .font(.headline)
                    .foregroundStyle(.white.opacity(0.85))
            }
            Text(label.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(.white.opacity(0.7))
                .tracking(1)
        }
    }

    private var durationValue: String {
        let h = hike.durationSeconds / 3600
        let m = (hike.durationSeconds % 3600) / 60
        if h > 0 { return "\(h):\(String(format: "%02d", m))" }
        return "\(m)"
    }

    private var durationUnit: String {
        hike.durationSeconds >= 3600 ? "h" : "min"
    }

    private var dateString: String {
        let f = DateFormatter()
        f.dateStyle = .long
        f.timeStyle = .none
        return f.string(from: hike.startedAt)
    }
}

/// Off-the-main-tree helper that renders an MKMapSnapshotter.Snapshot tightly cropped
/// around the GPS path, then composites the path on top in cyan. Used as the
/// background image for ShareableHikeCard.
enum HikeMapSnapshot {
    static func render(path: [GpsPoint], size: CGSize = CGSize(width: 1080, height: 1080)) async -> UIImage? {
        let coords = path.compactMap { p -> CLLocationCoordinate2D? in
            guard p.count >= 2 else { return nil }
            return CLLocationCoordinate2D(latitude: p[0], longitude: p[1])
        }
        guard coords.count >= 2 else { return nil }

        let lats = coords.map { $0.latitude }
        let lons = coords.map { $0.longitude }
        let region = MKCoordinateRegion(
            center: CLLocationCoordinate2D(
                latitude: (lats.min()! + lats.max()!) / 2,
                longitude: (lons.min()! + lons.max()!) / 2
            ),
            span: MKCoordinateSpan(
                latitudeDelta: max((lats.max()! - lats.min()!) * 1.4, 0.005),
                longitudeDelta: max((lons.max()! - lons.min()!) * 1.4, 0.005)
            )
        )

        let options = MKMapSnapshotter.Options()
        options.region = region
        options.size = size
        options.scale = 1
        options.mapType = .standard

        let snapshotter = MKMapSnapshotter(options: options)

        do {
            let snapshot = try await snapshotter.start()
            return composite(snapshot: snapshot, coords: coords, size: size)
        } catch {
            return nil
        }
    }

    private static func composite(
        snapshot: MKMapSnapshotter.Snapshot,
        coords: [CLLocationCoordinate2D],
        size: CGSize
    ) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        return renderer.image { ctx in
            snapshot.image.draw(at: .zero)

            let cg = ctx.cgContext
            cg.setLineCap(.round)
            cg.setLineJoin(.round)

            // Translucent white halo first so the cyan path stays readable
            // against busy basemap colors.
            cg.setStrokeColor(UIColor.white.withAlphaComponent(0.55).cgColor)
            cg.setLineWidth(18)
            stroke(coords: coords, in: cg, snapshot: snapshot)

            // Cyan path on top
            cg.setStrokeColor(UIColor.systemCyan.cgColor)
            cg.setLineWidth(10)
            stroke(coords: coords, in: cg, snapshot: snapshot)
        }
    }

    private static func stroke(
        coords: [CLLocationCoordinate2D],
        in cg: CGContext,
        snapshot: MKMapSnapshotter.Snapshot
    ) {
        cg.beginPath()
        for (i, c) in coords.enumerated() {
            let p = snapshot.point(for: c)
            if i == 0 { cg.move(to: p) } else { cg.addLine(to: p) }
        }
        cg.strokePath()
    }
}
