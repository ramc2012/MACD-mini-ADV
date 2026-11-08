import Charts
import SwiftUI

/// Shown over a list until its first load lands: a spinner, or the error with a way to fix it.
struct LoadState<T>: View {
    @Environment(AppModel.self) private var model
    let loader: Loader<T>
    let retry: () async -> Void

    var body: some View {
        if loader.value == nil {
            if let error = loader.error {
                ContentUnavailableView {
                    Label(model.hasToken ? "Can't load" : "Connect to the engine",
                          systemImage: model.hasToken ? "exclamationmark.triangle" : "key")
                } description: {
                    Text(error)
                } actions: {
                    if model.hasToken {
                        Button("Try again") { Task { await retry() } }.buttonStyle(.borderedProminent)
                    } else {
                        Button("Open Settings") { model.tab = .settings }.buttonStyle(.borderedProminent)
                    }
                }
            } else {
                ProgressView("Loading…")
            }
        }
    }
}

/// Keeps the last good data on screen and says when a refresh failed.
struct StaleNote: View {
    let error: String?
    let updated: Date?
    var body: some View {
        if let error {
            Label { Text("Refresh failed: \(error)") } icon: { Image(systemName: "exclamationmark.triangle.fill") }
                .font(.caption).foregroundStyle(.orange)
        } else if let updated {
            Text("Updated \(Fmt.dateTime(updated))").font(.caption).foregroundStyle(.secondary)
        }
    }
}

/// Engine and feed status from /api/system/health.
struct HealthBanner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if let h = model.health {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Circle().fill(dot(h)).frame(width: 9, height: 9)
                    Text(headline(h)).font(.subheadline.weight(.semibold))
                    Spacer()
                    if let rate = h.tickRatePerSecond, h.feedAlive == true {
                        Text(String(format: "%.0f ticks/s", rate)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
                Text(market(h)).font(.caption).foregroundStyle(.secondary)
                if h.tokenExpired == true || h.socketAuthFailed == true {
                    Button { model.tab = .brokers } label: {
                        Label(h.tokenExpired == true ? "The Fyers token has expired. Log in again on the Brokers tab."
                                                     : "Fyers refused the feed socket's token. Log in again on the Brokers tab.",
                              systemImage: "key.horizontal")
                    }
                    .font(.caption).foregroundStyle(.orange)
                } else if let err = h.error, !err.isEmpty, h.status != "connected" {
                    Text(err).font(.caption).foregroundStyle(.orange)
                }
            }
            .padding(.vertical, 2)
        } else if let err = model.healthError {
            Label(err, systemImage: "bolt.horizontal.circle").font(.caption).foregroundStyle(.orange)
        }
    }

    private func dot(_ h: Health) -> Color {
        if h.status == "connected" && h.feedAlive == true { return .gain }
        if h.status == "connecting" { return .yellow }
        return h.sessionOpen == true ? .loss : .gray
    }

    private func headline(_ h: Health) -> String {
        switch h.status {
        case "connected": return h.feedAlive == true ? "Engine live" : "Engine connected, feed idle"
        case "connecting": return "Engine connecting"
        case "stopped": return "Engine stopped"
        default: return h.tokenExpired == true ? "Engine waiting for a Fyers token" : "Engine error"
        }
    }

    private func market(_ h: Health) -> String {
        if h.sessionOpen == true { return "Market open · checked \(Fmt.dateTime(h.observedAt))" }
        var text = "Market closed"
        if let holiday = h.marketCalendar?.holiday { text += " (\(holiday))" }
        if let next = h.marketCalendar?.nextTradingDay { text += " · next session \(Fmt.dayYear(next))" }
        return text
    }
}

struct Sparkline: View {
    let points: [CurvePoint]
    var body: some View {
        let up = (points.last?.equity ?? 0) >= (points.first?.equity ?? 0)
        Chart(points) { p in
            LineMark(x: .value("Time", p.date), y: .value("Equity", p.equity))
                .interpolationMethod(.linear)
                .lineStyle(StrokeStyle(lineWidth: 1.5))
                .foregroundStyle(up ? Color.gain : Color.loss)
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: .automatic(includesZero: false))
        .chartLegend(.hidden)
        .accessibilityHidden(true)
    }
}

/// A label over a value, for stat grids.
struct Stat: View {
    let title: String
    let value: String
    var color: Color = .primary
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout.weight(.semibold).monospacedDigit()).foregroundStyle(color)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
