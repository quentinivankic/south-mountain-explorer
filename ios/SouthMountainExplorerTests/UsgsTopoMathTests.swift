import XCTest
@testable import SouthMountainExplorer

final class UsgsTopoMathTests: XCTestCase {
    func testTileIndexKnownPoint() {
        XCTAssertEqual(UsgsTopoMath.tileX(lon: -112.07, z: 8), 48)
        XCTAssertEqual(UsgsTopoMath.tileY(lat: 33.33, z: 8), 102)
    }

    func testSouthMountainCountsMatchReportEstimate() {
        let b = UsgsTopoMath.southMountainBBox
        func count(_ zs: ClosedRange<Int>) -> Int {
            UsgsTopoMath.tiles(west: b.west, south: b.south, east: b.east, north: b.north, zooms: zs).count
        }
        XCTAssertEqual(count(8...14), 72)
        XCTAssertEqual(count(8...15), 234)
        XCTAssertEqual(count(8...16), 864)
    }

    func testPathAndURLMapping() {
        let t = UsgsTopoTile(z: 15, x: 6, y: 9)
        XCTAssertEqual(UsgsTopoMath.relativePath(t), "15/6/9.tile")
        XCTAssertTrue(UsgsTopoMath.remoteURL(t).absoluteString.hasSuffix("/tile/15/9/6"))
    }

    func testModeDecision() {
        typealias M = UsgsTopoMath
        XCTAssertFalse(M.shouldShow(mode: .off, pathSatisfied: false, hasTiles: true))
        XCTAssertTrue(M.shouldShow(mode: .always, pathSatisfied: true, hasTiles: true))
        XCTAssertFalse(M.shouldShow(mode: .always, pathSatisfied: true, hasTiles: false))
        XCTAssertTrue(M.shouldShow(mode: .whenOffline, pathSatisfied: false, hasTiles: true))
        XCTAssertFalse(M.shouldShow(mode: .whenOffline, pathSatisfied: true, hasTiles: true))
    }
}
