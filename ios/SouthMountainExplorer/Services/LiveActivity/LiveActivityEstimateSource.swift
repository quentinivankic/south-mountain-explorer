import CoreLocation
import Foundation

/// Read-only adapter over the existing estimate code. This is the ONLY file
/// in the Live Activity feature that calls `TrailETA`; if those signatures
/// change, fix it here. Both fields are optional and the UI hides nil ones.
@MainActor
enum LiveActivityEstimateSource {
    static func estimates(for rec: ActiveRecording,
                          recording: RecordingService,
                          location: LocationService,
                          areas: AreaDataService) -> LiveActivityEstimates {
        let pace = recording.smoothedPaceMetersPerSec()
        let back = TrailETA.returnToStart(walkedMeters: rec.distanceMi * 1609.344,
                                          paceMetersPerSec: pace)
        var trailEnd: TimeInterval?
        if rec.mode == .trail, let trailId = rec.trailId,
           let area = areas.cachedArea(id: rec.areaId),
           let trail = (area.rawTrails ?? area.trails).first(where: { $0.id == trailId }),
           let coord = location.liveLocation ?? location.userLocation {
            var prior: CLLocationCoordinate2D?
            if rec.path.count >= 2 {
                let p = rec.path[rec.path.count - 2]
                if p.count >= 2 { prior = CLLocationCoordinate2D(latitude: p[0], longitude: p[1]) }
            }
            trailEnd = TrailETA.compute(currentLocation: coord, priorLocation: prior,
                                        trail: trail, paceMetersPerSec: pace)
        }
        return LiveActivityEstimates(trailEndSeconds: trailEnd, backSeconds: back)
    }
}
