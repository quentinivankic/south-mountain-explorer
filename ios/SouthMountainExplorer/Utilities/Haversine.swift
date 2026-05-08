import Foundation

func haversineDistanceMi(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
    let R = 3958.8
    let dLat = (lat2 - lat1) * .pi / 180
    let dLon = (lon2 - lon1) * .pi / 180
    let la1 = lat1 * .pi / 180
    let la2 = lat2 * .pi / 180
    let a = sin(dLat / 2) * sin(dLat / 2)
           + cos(la1) * cos(la2) * sin(dLon / 2) * sin(dLon / 2)
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
}

func haversineDistanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double) -> Double {
    haversineDistanceMi(lat1: lat1, lon1: lon1, lat2: lat2, lon2: lon2) * 1609.344
}
