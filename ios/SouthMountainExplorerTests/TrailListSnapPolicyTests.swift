import Testing
@testable import SouthMountainExplorer

struct TrailListSnapPolicyTests {
    @Test func snapsAtStandardSizes() {
        #expect(TrailListSnapPolicy.snapsToRows(isAccessibilitySize: false))
    }
    @Test func doesNotSnapAtAccessibilitySizes() {
        #expect(!TrailListSnapPolicy.snapsToRows(isAccessibilitySize: true))
    }
}
