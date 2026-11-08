import Charts
import SwiftUI

/// Every paper book scored alike (/api/scoreboard), with the engine's status on top.
struct LanesView: View {
    @Environment(AppModel.self) private var model
    @State private var board = Loader<Scoreboard>()

    var body: some View {
        NavigationStack {
            List {
                Section { HealthBanner() }
                if let lanes = board.value?.lanes {
                    Section {
                        ForEach(lanes) { lane in
                            NavigationLink(value: lane) { LaneRow(lane: lane) }
                        }
                    } header: {
                        Text("Paper books")
                    } footer: {
                        VStack(alignment: .leading, spacing: 4) {
                            if let asOf = board.value?.asOf { Text("Scored \(Fmt.dateTime(asOf))") }
                            StaleNote(error: board.error, updated: nil)
                            Text("Paper trading only. Returns on short samples are not proven.")
                        }
                    }
                }
            }
            .overlay { LoadState(loader: board, retry: load) }
            .navigationTitle("Lanes")
            .navigationDestination(for: LaneScore.self) { lane in
                LaneDetailView(lane: board.value?.lanes.first { $0.lane == lane.lane } ?? lane)
            }
            .refreshable { await load() }
            .polling(every: 30, id: model.version) { await load() }
        }
    }

    private func load() async {
        await model.refreshHealth()
        await board.load { try await model.api().get("api/scoreboard") }
    }
}

struct LaneRow: View {
    let lane: LaneScore

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(lane.label).font(.headline)
                    if lane.bookFull == true {
                        Text("FULL").font(.caption2.weight(.bold)).padding(.horizontal, 4).padding(.vertical, 1)
                            .background(.orange.opacity(0.18), in: .rect(cornerRadius: 3)).foregroundStyle(.orange)
                    }
                }
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(Fmt.rupees(lane.equity)).font(.subheadline.weight(.semibold).monospacedDigit())
                    Text(Fmt.pct(lane.returnPct)).font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(Color.pnl(lane.returnPct))
                }
                Text("\(lane.openPositions ?? 0) open · booked \(Fmt.rupees(lane.realized, signed: true)) · open \(Fmt.rupees(lane.unrealized, signed: true))")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8)
            }
            Spacer(minLength: 4)
            if let curve = lane.curve, curve.count > 1 {
                Sparkline(points: curve).frame(width: 84, height: 36)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct LaneDetailView: View {
    let lane: LaneScore
    @State private var showAllExits = false

    var body: some View {
        List {
            Section {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                    GridRow {
                        Stat(title: "Equity", value: Fmt.rupees(lane.equity))
                        Stat(title: "Return", value: Fmt.pct(lane.returnPct), color: .pnl(lane.returnPct))
                        Stat(title: "Capital", value: Fmt.rupees(lane.capital))
                    }
                    GridRow {
                        Stat(title: "Booked", value: Fmt.rupees(lane.realized, signed: true), color: .pnl(lane.realized))
                        Stat(title: "Open P&L", value: Fmt.rupees(lane.unrealized, signed: true), color: .pnl(lane.unrealized))
                        Stat(title: "Cash", value: Fmt.rupees(lane.cash))
                    }
                    GridRow {
                        Stat(title: "Open positions", value: "\(lane.openPositions ?? 0)\(lane.slots.map { " / \($0)" } ?? "")")
                        Stat(title: "Max drawdown", value: Fmt.pct(lane.maxDrawdownPct.map { -$0 }), color: .loss)
                        Stat(title: "Since", value: Fmt.day(lane.since))
                    }
                }
                .padding(.vertical, 4)
                if let note = lane.note, !note.isEmpty { Text(note).font(.caption).foregroundStyle(.secondary) }
            }

            if let curve = lane.curve, curve.count > 1 {
                Section("Equity") {
                    Chart {
                        ForEach(curve) { p in
                            LineMark(x: .value("Time", p.date), y: .value("Equity", p.equity))
                                .foregroundStyle(Color.coilUp)
                                .lineStyle(StrokeStyle(lineWidth: 2))
                        }
                        if let capital = lane.capital {
                            RuleMark(y: .value("Capital", capital))
                                .foregroundStyle(.secondary)
                                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
                                .annotation(position: .top, alignment: .leading) {
                                    Text("capital").font(.caption2).foregroundStyle(.secondary)
                                }
                        }
                    }
                    .chartYScale(domain: .automatic(includesZero: false))
                    .chartYAxis { AxisMarks { v in
                        AxisGridLine()
                        AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.rupees(d)).font(.caption2) } }
                    } }
                    .frame(height: 200)
                    .padding(.vertical, 6)
                }
            }

            if let weeks = lane.weekly, !weeks.isEmpty {
                Section {
                    Chart(weeks) { w in
                        BarMark(x: .value("Week", w.date, unit: .weekOfYear), y: .value("Net", w.net))
                            .foregroundStyle(w.net >= 0 ? Color.gain : Color.loss)
                            .cornerRadius(3)
                    }
                    .chartYAxis { AxisMarks { v in
                        AxisGridLine()
                        AxisValueLabel { if let d = v.as(Double.self) { Text(Fmt.rupees(d)).font(.caption2) } }
                    } }
                    .frame(height: 150)
                    .padding(.vertical, 6)
                } header: {
                    Text("Net by week")
                } footer: {
                    if let best = lane.bestWeek {
                        Text("Best week \(Fmt.day(best.week)): \(Fmt.rupees(best.net, signed: true)). Without it: \(Fmt.rupees(lane.netWithoutBestWeek, signed: true)).")
                    }
                }
            }

            if let c = lane.closed, (c.trades ?? 0) > 0 {
                Section("Closed trades") {
                    Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                        GridRow {
                            Stat(title: "Trades", value: "\(c.trades ?? 0)")
                            Stat(title: "Win rate", value: Fmt.pct((c.winRate ?? 0) * 100, digits: 0, signed: false))
                            Stat(title: "Profit factor", value: c.profitFactor.map { String(format: "%.2f", $0) } ?? "—")
                        }
                        GridRow {
                            Stat(title: "Net", value: Fmt.rupees(c.net, signed: true), color: .pnl(c.net))
                            Stat(title: "Avg win", value: Fmt.rupees(c.avgWin, signed: true), color: .gain)
                            Stat(title: "Avg loss", value: Fmt.rupees(c.avgLoss, signed: true), color: .loss)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }

            if let exits = lane.exits, !exits.isEmpty {
                let sorted = exits.sorted { abs($0.net ?? 0) > abs($1.net ?? 0) }
                Section {
                    ForEach(showAllExits ? sorted : Array(sorted.prefix(8))) { e in
                        HStack(alignment: .firstTextBaseline) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(Fmt.words(e.reason)).font(.subheadline)
                                Text("held \(e.hold ?? "—") · \(e.trades ?? 0) trades · \(Fmt.pct((e.winRate ?? 0) * 100, digits: 0, signed: false)) won")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(Fmt.rupees(e.net, signed: true)).font(.subheadline.monospacedDigit()).foregroundStyle(Color.pnl(e.net))
                        }
                    }
                    if sorted.count > 8 {
                        Button(showAllExits ? "Show fewer" : "Show all \(sorted.count)") { showAllExits.toggle() }
                    }
                } header: {
                    Text("Exits by reason")
                }
            }
        }
        .navigationTitle(lane.label)
        .navigationBarTitleDisplayMode(.inline)
    }
}
