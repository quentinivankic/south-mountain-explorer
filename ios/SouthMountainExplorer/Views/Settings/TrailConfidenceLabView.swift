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

    /// Score every sample once, sorted high → low. Recomputed on each
    /// slider change (SwiftUI re-renders on `weights` mutation).
    private var scored: [(props: TrailScoringProps, score: Double, band: ScoreBand)] {
        trails
            .map { p in
                let r = TrailScoring.scoreAndBand(p, weights: weights)
                return (p, r.score, r.band)
            }
            .sorted { $0.score > $1.score }
    }

    private func count(_ band: ScoreBand) -> Int {
        scored.filter { $0.band == band }.count
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
                     + "which trails you curate into the tiles. Samples stand in for "
                     + "real pmtiles until the NZ pilot lands.")
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
                    Text("\(band.label) \(count(band))")
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
        Section("Trails (\(trails.count))") {
            ForEach(scored, id: \.props.id) { row in
                HStack(spacing: 12) {
                    Circle().fill(row.band.color).frame(width: 12, height: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.props.name)
                        Text(firedSummary(row.props))
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
    /// A representative spread across the signal space so every lever
    /// visibly moves at least one trail. Mix of the Python conformance
    /// cases + plausible NZ/US trails. Scores with default weights noted.
    // Default-weight scores noted per row (base 50). They shift live as
    // you drag the levers — that's the point.
    static let samples: [TrailScoringProps] = [
        // 100 (clamped from 120): authoritative + operator + name + whitelist + high region
        TrailScoringProps(name: "Kepler Track", authoritativeMatch: true,
                          hasKnownOperator: true, hasName: true,
                          inOfficialWhitelist: true, regionTrust: "high",
                          sacScale: "hiking"),
        // 90: authoritative (+20) + name (+10) + high region (+10)
        TrailScoringProps(name: "Roys Peak Track", authoritativeMatch: true,
                          hasName: true, regionTrust: "high", sacScale: "mountain_hiking"),
        // 60 medium: bare named path (+10)
        TrailScoringProps(name: "Ridgeline Path", hasName: true),
        // 50 medium: unnamed, no signals
        TrailScoringProps(name: "unnamed path"),
        // 45 medium: named (+10) + recently edited (−15)
        TrailScoringProps(name: "New Cutoff Trail", hasName: true, editedDaysAgo: 5),
        // 40 medium (edge): named (+10) + SAC demanding (−20)
        TrailScoringProps(name: "Alpine Route", hasName: true,
                          sacScale: "demanding_mountain_hiking"),
        // 45 medium: named (+10) + TIGER unreviewed (−15)
        TrailScoringProps(name: "County Line Trail", hasName: true, tigerUnreviewed: true),
        // 35 low: named (+10) + poor visibility (−25)
        TrailScoringProps(name: "Faint Spur", hasName: true, trailVisibility: "bad"),
        // 25 low: named (+10) + informal (−35)
        TrailScoringProps(name: "Desire Line", hasName: true, informal: true),
        // 10 low: access=private (−40)
        TrailScoringProps(name: "Private Farm Track", access: "private", sacScale: "hiking"),
        // 0 low (clamped from −35): informal (−35) + abandoned (−50)
        TrailScoringProps(name: "Old Mine Trail", informal: true, lifecycle: "abandoned"),
    ]
}
#endif
