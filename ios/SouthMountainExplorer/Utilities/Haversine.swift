import Foundation

// The one home for great-circle distance. The app uses two Earth radii and
// two final steps, kept exactly as they were so no distance changes by even
// the last bit (stored progress, thresholds and tests compare these values):
// - miles, R = 3,958.8 mi, atan2 step: `haversineDistanceMi` (area
//   distances, home and browse lists, trail endpoint gaps).
// - metres, R = 6,371,000 m, atan2 step: `MapMath.haversineMeters`,
//   `AreaDataService` segment miles. Pass `earthRadiusMeters`.
// - metres, R = 6,371,000 m, asin step: `Area.meters`, `TrailProfile.meters`.
//   Use `haversineDistanceAsin`.
// Note: `haversineDistanceM` is miles times 1,609.344, i.e. R = 6,371,150 m,
// not 6,371,000 m. The two metre radii are both in use and are not merged.

/// Mean Earth radius in miles, used by `haversineDistanceMi`.
let earthRadiusMiles = 3958.8
/// Mean Earth radius in metres, used by the metre copies listed above.
let earthRadiusMeters = 6_371_000.0

/// Great-circle distance on a sphere of `radius` (result in the radius unit),
/// atan2 form.
func haversineDistance(lat1: Double, lon1: Double, lat2: Double, lon2: Double, radius: Double) -> Double {
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let la1 = lat1 * .pi / 180
    let la2 = lat2 * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
           + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
    return radius * 2 * atan2(sqrt(a), sqrt(1 - a))
}

/// Great-circle distance on a sphere of `radius`, asin form
/// (`2 R asin(min(1, sqrt(a)))`). Same distance as `haversineDistance` up to
/// the last bits; kept separate so its callers stay bit-for-bit identical.
func haversineDistanceAsin(lat1: Double, lon1: Double, lat2: Double, lon2: Double, radius: Double) -> Double {
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let la1 = lat1 * .pi / 180
    let la2 = lat2 * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
           + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
    return 2 * radius * asin(min(1, sqrt(a)))
}

func haversineDistanceMi(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
    haversineDistance(lat1: lat1, lon1: lon1, lat2: lat2, lon2: lon2, radius: earthRadiusMiles)
}

func haversineDistanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
    haversineDistanceMi(lat1: lat1, lon1: lon1, lat2: lat2, lon2: lon2) * 1609.344
}
