#if DEBUG
import SwiftUI

/// DEBUG-only authoring lab for the on-device confidence score (spec §4.3
/// / §8 "authoring build"). Drag the weight/base/band levers and watch a
/// representative set of trails re-score and re-band in real time — the
/// same live-recuration loop the spec calls for, with no tile rebuild.
///
/// Compiled OUT of Release entirely (`#if DEBUG`), and reached only via
/// Settings → Developer, so the shipped user build carries no confidence
/// UI (spec §8 "shipped build"). Uses `TrailScoring`, the port of
/// `scoring_reference.py`; the sample trails stand in for real pmtiles
/// feature properties until the NZ pilot tiles land.
struct TrailConfidenceLabView: View {
    @State private var weights = ScoringWeights.default

    private let trails = TrailScoringProps.samples

    /// Score every sample once, sorted by prevalence (real-trail count)
    /// so the dominant patterns surface first. Recomputed on each slider
    /// change (SwiftUI re-renders on `weights` mutation).
    private var scored: [(props: TrailScoringProps, score: Double, band: ScoreBand)] {
        trails
            .map { p in
                let r = TrailScoring.scoreAndBand(p, weights: weights)
                return (p, r.score, r.band)
            }
            .sorted { ($0.props.count ?? 1, $0.score) > ($1.props.count ?? 1, $1.score) }
    }

    /// A sample stands for `count` real trails (or 1 for a fixture), so
    /// the band tally is weighted by prevalence — otherwise the ~300k
    /// "unnamed footway" pattern would read as a single row.
    private func weight(_ p: TrailScoringProps) -> Int { p.count ?? 1 }

    private var totalWeight: Int { scored.reduce(0) { $0 + weight($1.props) } }

    private func bandWeight(_ band: ScoreBand) -> Int {
        scored.filter { $0.band == band }.reduce(0) { $0 + weight($1.props) }
    }

    private func bandPercent(_ band: ScoreBand) -> Int {
        let t = totalWeight
        return t == 0 ? 0 : Int((Double(bandWeight(band)) / Double(t) * 100).rounded())
    }

    var body: some View {
        Form {
            trailsSection
            leversSection(title: "Base & bands", rows: baseRows)
            leversSection(title: "Positive signals", rows: positiveRows)
            leversSection(title: "Negative signals", rows: negativeRows)

            Section {
                EmptyView()
            } footer: {
                Text("Dev-only. Mirrors data-pipeline/build/scoring_reference.py. "
                     + "The shipped build has no confidence UI — this only informs "
                     + "which trails you curate into the tiles. Rows are the 40 most "
                     + "common signal patterns from the real NZ build (354,985 trails); "
                     + "the tally is weighted by how many trails share each pattern.")
            }
        }
        .navigationTitle("Trail Confidence Lab")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Reset") { weights = .default }
                    .disabled(weights == .default)
            }
        }
        // Always-visible band tally so you see the effect while scrolling
        // through the levers below.
        .safeAreaInset(edge: .top) { bandSummary }
    }

    // MARK: - Band summary (pinned)

    private var bandSummary: some View {
        HStack(spacing: 8) {
            ForEach([ScoreBand.high, .medium, .low], id: \.self) { band in
                HStack(spacing: 6) {
                    Circle().fill(band.color).frame(width: 10, height: 10)
                    Text("\(band.label) \(bandPercent(band))%")
                        .font(.subheadline.weight(.semibold))
                        .monospacedDigit()
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(band.color.opacity(0.12), in: Capsule())
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }

    // MARK: - Trails preview

    private var trailsSection: some View {
        Section("NZ trails · \(totalWeight.formatted()) across \(trails.count) patterns") {
            ForEach(scored, id: \.props.id) { row in
                HStack(spacing: 12) {
                    Circle().fill(row.band.color).frame(width: 12, height: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.props.name)
                        Text(rowSubtitle(row.props))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Text("\(Int(row.score.rounded()))")
                        .font(.title3.weight(.semibold).monospacedDigit())
                        .foregroundStyle(row.band.color)
                }
                .padding(.vertical, 2)
            }
        }
    }

    /// "306,840 trails · <fired signals>" — prevalence first so the real
    /// distribution is visible per row.
    private func rowSubtitle(_ p: TrailScoringProps) -> String {
        let signals = firedSummary(p)
        guard let c = p.count else { return signals }
        return "\(c.formatted()) trails · \(signals)"
    }

    private func firedSummary(_ p: TrailScoringProps) -> String {
        let fired = TrailScoring.firedSignals(p)
        if fired.isEmpty { return "no signals" }
        return ScoreSignal.allCases
            .filter { fired.contains($0) }
            .map(\.label)
            .joined(separator: " · ")
    }

    // MARK: - Levers

    private struct LeverRow: Identifiable {
        let id: String
        let title: String
        let binding: Binding<Double>
        let range: ClosedRange<Double>
    }

    private var baseRows: [LeverRow] {
        [
            LeverRow(id: "base", title: "Base", binding: $weights.base, range: 0...100),
            LeverRow(id: "bandMedium", title: "Band: medium ≥",
                     binding: $weights.bandMedium, range: 0...100),
            LeverRow(id: "bandHigh", title: "Band: high ≥",
                     binding: $weights.bandHigh, range: 0...100),
        ]
    }

    private var positiveRows: [LeverRow] {
        ScoreSignal.allCases.filter(\.isPositive).map { signal in
            LeverRow(id: signal.rawValue, title: signal.label,
                     binding: weightBinding(signal), range: 0...50)
        }
    }

    private var negativeRows: [LeverRow] {
        ScoreSignal.allCases.filter { !$0.isPositive }.map { signal in
            LeverRow(id: signal.rawValue, title: signal.label,
                     binding: weightBinding(signal), range: -60...0)
        }
    }

    private func weightBinding(_ signal: ScoreSignal) -> Binding<Double> {
        Binding(
            get: { weights.weights[signal] ?? 0 },
            set: { weights.weights[signal] = $0 }
        )
    }

    private func leversSection(title: String, rows: [LeverRow]) -> some View {
        Section(title) {
            ForEach(rows) { row in
                VStack(spacing: 4) {
                    HStack {
                        Text(row.title)
                        Spacer()
                        Text("\(Int(row.binding.wrappedValue.rounded()))")
                            .font(.body.weight(.semibold).monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    Slider(value: row.binding, in: row.range, step: 1)
                }
                .padding(.vertical, 2)
            }
        }
    }
}

// MARK: - Sample trails

extension TrailScoringProps {
    /// The 40 most common signal patterns from the real New Zealand
    /// build (run #6, 354,985 trails). Each row carries `count` = how
    /// many trails share that exact signature, so the lab reflects the
    /// real distribution — e.g. 86% of NZ "trails" are unnamed
    /// access=yes footways. Regenerate from a build via
    /// `data-pipeline/build/sample_trails.py`.
    static let samples: [TrailScoringProps] = [
        TrailScoringProps(name: "unnamed footway (access=yes)", regionTrust: "high", access: "yes", count: 306840),
        TrailScoringProps(name: "unnamed · access=no", regionTrust: "high", access: "no", count: 19283),
        TrailScoringProps(name: "Te Araroa Trail", hasName: true, regionTrust: "high", count: 11874),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", count: 5736),
        TrailScoringProps(name: "Smugglers Bay Track/ Te Araroa Trail", authoritativeMatch: true, hasName: true, inOfficialWhitelist: true, regionTrust: "high", count: 3057),
        TrailScoringProps(name: "unnamed · DOC-matched", authoritativeMatch: true, inOfficialWhitelist: true, regionTrust: "high", count: 1959),
        TrailScoringProps(name: "Scott's Track", hasName: true, inOfficialWhitelist: true, regionTrust: "high", count: 1812),
        TrailScoringProps(name: "unnamed · informal", regionTrust: "high", informal: true, count: 1603),
        TrailScoringProps(name: "Airport Perimeter Walkway", hasName: true, regionTrust: "high", access: "no", count: 990),
        TrailScoringProps(name: "unnamed · informal", regionTrust: "high", informal: true, trailVisibility: "no", count: 314),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", access: "no", trailVisibility: "good", count: 312),
        TrailScoringProps(name: "unnamed · visibility=horrible", regionTrust: "high", trailVisibility: "horrible", count: 173),
        TrailScoringProps(name: "unnamed · access=no", regionTrust: "high", access: "no", informal: true, count: 124),
        TrailScoringProps(name: "Hooker Valley Track", authoritativeMatch: true, hasName: true, inOfficialWhitelist: true, regionTrust: "high", access: "no", trailVisibility: "excellent", sacScale: "hiking", count: 110),
        TrailScoringProps(name: "Franz Josef Glacier Walk - section closed", hasName: true, inOfficialWhitelist: true, regionTrust: "high", access: "no", count: 107),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", informal: true, trailVisibility: "excellent", sacScale: "hiking", count: 66),
        TrailScoringProps(name: "Shortcut onto Taui St", hasName: true, regionTrust: "high", informal: true, count: 47),
        TrailScoringProps(name: "unnamed · DOC-matched", authoritativeMatch: true, inOfficialWhitelist: true, regionTrust: "high", access: "no", count: 45),
        TrailScoringProps(name: "Scott's Track", authoritativeMatch: true, hasName: true, inOfficialWhitelist: true, regionTrust: "high", sacScale: "demanding_mountain_hiking", count: 40),
        TrailScoringProps(name: "Kellys Track", hasName: true, inOfficialWhitelist: true, regionTrust: "high", sacScale: "demanding_mountain_hiking", count: 37),
        TrailScoringProps(name: "Ridge Track (unmarked)", hasName: true, inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "bad", sacScale: "hiking", count: 34),
        TrailScoringProps(name: "unnamed · alpine_hiking", regionTrust: "high", sacScale: "alpine_hiking", count: 30),
        TrailScoringProps(name: "Kahui Farm Privat Bush Walk", hasName: true, regionTrust: "high", sacScale: "demanding_mountain_hiking", count: 29),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "bad", sacScale: "mountain_hiking", count: 26),
        TrailScoringProps(name: "Newton Creek track", hasName: true, regionTrust: "high", trailVisibility: "bad", sacScale: "hiking", count: 25),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", lifecycle: "abandoned", count: 23),
        TrailScoringProps(name: "unnamed · DOC-matched", authoritativeMatch: true, inOfficialWhitelist: true, regionTrust: "high", sacScale: "demanding_mountain_hiking", count: 21),
        TrailScoringProps(name: "unnamed · DOC-matched", authoritativeMatch: true, hasKnownOperator: true, inOfficialWhitelist: true, regionTrust: "high", sacScale: "hiking", count: 21),
        TrailScoringProps(name: "Dome Summit Track", hasName: true, inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "horrible", sacScale: "demanding_mountain_hiking", count: 19),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", sacScale: "alpine_hiking", count: 19),
        TrailScoringProps(name: "Old Satara Cres Walkway", hasName: true, regionTrust: "high", access: "no", informal: true, count: 18),
        TrailScoringProps(name: "The Chasm Walkway", authoritativeMatch: true, hasKnownOperator: true, hasName: true, inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "excellent", sacScale: "hiking", count: 17),
        TrailScoringProps(name: "unnamed · DOC-matched", authoritativeMatch: true, inOfficialWhitelist: true, regionTrust: "high", informal: true, count: 17),
        TrailScoringProps(name: "unnamed · access=private", regionTrust: "high", access: "private", trailVisibility: "horrible", count: 13),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "horrible", sacScale: "alpine_hiking", count: 13),
        TrailScoringProps(name: "unnamed · in conservation land", inOfficialWhitelist: true, regionTrust: "high", informal: true, trailVisibility: "no", sacScale: "hiking", count: 11),
        TrailScoringProps(name: "unnamed · disused", regionTrust: "high", lifecycle: "disused", count: 10),
        TrailScoringProps(name: "Hobson Bay Walkway", hasName: true, regionTrust: "high", informal: true, trailVisibility: "no", count: 9),
        TrailScoringProps(name: "Northern Summit Route", authoritativeMatch: true, hasName: true, inOfficialWhitelist: true, regionTrust: "high", trailVisibility: "bad", sacScale: "alpine_hiking", count: 8),
        TrailScoringProps(name: "Walker Kauri Track", hasName: true, regionTrust: "high", access: "no", trailVisibility: "bad", sacScale: "hiking", count: 6),
    ]
}
#endif
