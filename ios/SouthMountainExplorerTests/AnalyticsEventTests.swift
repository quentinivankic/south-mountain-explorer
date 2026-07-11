import Foundation
import Testing
@testable import SouthMountainExplorer

/// Pins the analytics event wire contract. Event names are what PostHog
/// groups on, so a rename is a deliberate, reviewed change — these tests
/// make an accidental one fail CI. Also covers the bucketing that keeps
/// continuous values non-identifying.
struct AnalyticsEventTests {

    // MARK: - Names + properties

    @Test func eventNamesAreStableSnakeCase() {
        #expect(AnalyticsEvent.appLaunched(build: "42").name == "app_launched")
        #expect(AnalyticsEvent.areaOpened(areaId: "a").name == "area_opened")
        #expect(AnalyticsEvent.hikeStarted(areaId: "a", mode: "trail").name == "hike_started")
        #expect(AnalyticsEvent.hikeSaved(areaId: "a", distanceMi: 2, durationSeconds: 100, mode: "roam").name == "hike_saved")
        #expect(AnalyticsEvent.hikeDiscarded(areaId: "a").name == "hike_discarded")
        #expect(AnalyticsEvent.trailCompleted(areaId: "a").name == "trail_completed")
        #expect(AnalyticsEvent.areaCompleted(areaId: "a").name == "area_completed")
        #expect(AnalyticsEvent.dexOpened(areaId: "a").name == "dex_opened")
        #expect(AnalyticsEvent.unitsChanged(value: "metric").name == "units_changed")
        #expect(AnalyticsEvent.themeChanged(value: "dark").name == "theme_changed")
        #expect(AnalyticsEvent.dataExported().name == "data_exported")
        #expect(AnalyticsEvent.dataImported().name == "data_imported")
        #expect(AnalyticsEvent.feedbackSubmitted(category: "bug", message: "x", email: nil).name == "feedback_submitted")
        #expect(AnalyticsEvent.crashDetected(count: 1).name == "crash_detected")
        #expect(AnalyticsEvent.hangDetected(count: 1).name == "hang_detected")
        #expect(AnalyticsEvent.waitlistJoined(country: "FR", email: "a@b.com").name == "waitlist_joined")
    }

    @Test func waitlistCarriesCountryAndEmail() {
        let e = AnalyticsEvent.waitlistJoined(country: "FR", email: "hiker@example.com")
        #expect(e.properties["country"] == "FR")
        #expect(e.properties["email"] == "hiker@example.com")
        #expect(e.properties["beta_interest"] == "false")   // defaults off
    }

    @Test func waitlistFlagsBetaInterest() {
        let e = AnalyticsEvent.waitlistJoined(country: "DE", email: "t@e.com", wantsBeta: true)
        #expect(e.properties["beta_interest"] == "true")
        #expect(e.properties["country"] == "DE")
    }

    @Test func diagnosticEventsCarryCount() {
        #expect(AnalyticsEvent.crashDetected(count: 3).properties["count"] == "3")
        #expect(AnalyticsEvent.hangDetected(count: 2).properties["count"] == "2")
    }

    @Test func hikeSavedCarriesBucketsAndMode() {
        let e = AnalyticsEvent.hikeSaved(areaId: "south-mountain",
                                         distanceMi: 4.2, durationSeconds: 4000, mode: "trail")
        #expect(e.properties["area_id"] == "south-mountain")
        #expect(e.properties["distance_bucket"] == "3-6mi")
        #expect(e.properties["duration_bucket"] == "1-3h")
        #expect(e.properties["mode"] == "trail")
    }

    @Test func feedbackCarriesMessageAndEmailFlag() {
        let withEmail = AnalyticsEvent.feedbackSubmitted(
            category: "idea", message: "great app", email: "a@b.com")
        #expect(withEmail.properties["category"] == "idea")
        #expect(withEmail.properties["message"] == "great app")
        #expect(withEmail.properties["has_email"] == "true")
        #expect(withEmail.properties["email"] == "a@b.com")

        let noEmail = AnalyticsEvent.feedbackSubmitted(
            category: "idea", message: "great app", email: nil)
        #expect(noEmail.properties["has_email"] == "false")
        #expect(noEmail.properties["email"] == nil)

        // Empty string counts as no email — no stray "email": "" key.
        let blankEmail = AnalyticsEvent.feedbackSubmitted(
            category: "bug", message: "x", email: "")
        #expect(blankEmail.properties["has_email"] == "false")
        #expect(blankEmail.properties["email"] == nil)
    }

    @Test func trailReportCarriesReasonTrailAndDetailsFlag() {
        let e = AnalyticsEvent.trailReported(
            reason: "is_a_road", details: "this is clearly a fire road",
            trailId: "t42", trailName: "Maxwell Ranch Road", areaId: "a7")
        #expect(e.name == "trail_reported")
        #expect(e.properties["reason"] == "is_a_road")
        #expect(e.properties["trail_id"] == "t42")
        #expect(e.properties["trail_name"] == "Maxwell Ranch Road")
        #expect(e.properties["area_id"] == "a7")
        #expect(e.properties["has_details"] == "true")
        #expect(e.properties["details"] == "this is clearly a fire road")

        // No details → flag false, no stray "details" key.
        let bare = AnalyticsEvent.trailReported(
            reason: "not_a_trail", details: "", trailId: "t1",
            trailName: "X", areaId: "a1")
        #expect(bare.properties["has_details"] == "false")
        #expect(bare.properties["details"] == nil)
    }

    // MARK: - Privacy: no coordinates or raw continuous values leak

    @Test func noPropertyValueLooksLikeACoordinate() {
        // Guard against a factory accidentally embedding lat/lon. All
        // current property values are ids/enums/buckets — none should
        // parse as a plausible coordinate pair or a long decimal.
        let events: [AnalyticsEvent] = [
            .hikeSaved(areaId: "a", distanceMi: 12.9, durationSeconds: 9999, mode: "trail"),
            .areaOpened(areaId: "a"),
            .trailCompleted(areaId: "a"),
        ]
        for e in events {
            for value in e.properties.values {
                #expect(!value.contains(","), "property value should not look like a coordinate pair: \(value)")
            }
        }
    }

    // MARK: - Bucketing

    @Test func distanceBucketsCoverRanges() {
        #expect(AnalyticsEvent.distanceBucket(miles: 0) == "0-1mi")
        #expect(AnalyticsEvent.distanceBucket(miles: -5) == "0-1mi")   // degenerate → lowest band
        #expect(AnalyticsEvent.distanceBucket(miles: 0.9) == "0-1mi")
        #expect(AnalyticsEvent.distanceBucket(miles: 1) == "1-3mi")
        #expect(AnalyticsEvent.distanceBucket(miles: 5.9) == "3-6mi")
        #expect(AnalyticsEvent.distanceBucket(miles: 6) == "6-10mi")
        #expect(AnalyticsEvent.distanceBucket(miles: 42) == "10mi+")
    }

    @Test func durationBucketsCoverRanges() {
        #expect(AnalyticsEvent.durationBucket(seconds: 0) == "0-30min")
        #expect(AnalyticsEvent.durationBucket(seconds: 1799) == "0-30min")
        #expect(AnalyticsEvent.durationBucket(seconds: 1800) == "30-60min")
        #expect(AnalyticsEvent.durationBucket(seconds: 3600) == "1-3h")
        #expect(AnalyticsEvent.durationBucket(seconds: 10800) == "3h+")
    }
}
