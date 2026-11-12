import Foundation
import Testing
@testable import SouthMountainExplorer

/// Settings' Build row. Local Debug builds are stamped with the source
/// commit by scripts/stamp-local-build.sh; Release builds are not.
@MainActor
struct BuildLabelTests {
    @Test func releaseBuildShowsTheBuildNumberAlone() {
        #expect(SettingsView.buildLabel(build: "319", commit: nil) == "319")
        #expect(SettingsView.buildLabel(build: "319", commit: "") == "319")
    }

    @Test func localBuildShowsTheCommitAfterTheBuildNumber() {
        #expect(SettingsView.buildLabel(build: "1.6402", commit: "6a627a641")
            == "1.6402 (6a627a641)")
        #expect(SettingsView.buildLabel(build: "1.6402", commit: "6a627a641-dirty")
            == "1.6402 (6a627a641-dirty)")
    }

    @Test func missingBuildNumberKeepsTheDash() {
        #expect(SettingsView.buildLabel(build: nil, commit: nil) == "—")
    }

    /// The unit test host is a Debug build, so it carries the stamp. The
    /// build number itself is never stamped (TestFlight owns it).
    @Test func debugHostAppIsStampedWithItsCommit() throws {
        let commit = try #require(SettingsView.localBuildCommit())
        #expect(commit.count >= 9, "commit \(commit)")
        #expect(Bundle.main.infoDictionary?["CFBundleVersion"] as? String == "1")
    }
}
