import Foundation

struct SizeEstimate: Equatable, Sendable {
    var wireBytes: Int
    var onDeviceBytes: Int

    static let zero = SizeEstimate(wireBytes: 0, onDeviceBytes: 0)

    static func + (lhs: SizeEstimate, rhs: SizeEstimate) -> SizeEstimate {
        SizeEstimate(
            wireBytes: lhs.wireBytes + rhs.wireBytes,
            onDeviceBytes: lhs.onDeviceBytes + rhs.onDeviceBytes
        )
    }
}

/// Bundled per-state byte totals measured from the CDN (see
/// `Resources/state-sizes.json`). Estimates are trail-weighted: bytes follow
/// trail geometry, so each area gets `trailCount x (stateBytes / stateTrails)`.
struct StateSizeTable: Codable, Sendable {
    struct Row: Codable, Equatable, Sendable {
        let state: String
        let areas: Int
        let trails: Int
        let rawBytes: Int
        let wireBytes: Int
    }

    let states: [Row]
    let global: Row

    /// Multiplier from CDN raw JSON bytes to the re-encoded file the app keeps
    /// on disk. Unmeasured, so 1.0 until a real run sets it.
    static let onDeviceFactor: Double = 1.0

    func row(for state: String) -> Row? {
        states.first { $0.state == state }
    }

    func estimate(for scope: StateScope, missingIDs: Set<String>? = nil) -> SizeEstimate {
        let row = row(for: scope.id) ?? global
        let trails = max(row.trails, 1)
        let rawPerTrail = Double(row.rawBytes) / Double(trails)
        let wirePerTrail = Double(row.wireBytes) / Double(trails)
        let avgTrailsPerArea = max(Double(row.trails) / Double(max(row.areas, 1)), 1)

        var trailSum = 0.0
        for id in scope.areaIDs {
            if let missingIDs, !missingIDs.contains(id) { continue }
            trailSum += Double(scope.trailCounts[id] ?? Int(avgTrailsPerArea.rounded()))
        }
        let raw = Int((trailSum * rawPerTrail).rounded())
        let wire = Int((trailSum * wirePerTrail).rounded())
        return SizeEstimate(
            wireBytes: wire,
            onDeviceBytes: Int((Double(raw) * Self.onDeviceFactor).rounded())
        )
    }

    static func loadBundled(bundle: Bundle = .main) -> StateSizeTable? {
        guard let url = bundle.url(forResource: "state-sizes", withExtension: "json"),
              let data = try? Data(contentsOf: url)
        else { return nil }
        return try? JSONDecoder().decode(StateSizeTable.self, from: data)
    }
}
