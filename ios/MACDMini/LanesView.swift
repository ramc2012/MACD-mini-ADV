import Charts
import SwiftUI

/// Every paper book scored alike (/api/scoreboard), with the engine's status on top.
struct LanesView: View {
    @Environment(AppModel.self) private var model
    @State private var board = Loader<Scoreboard>()
    @State private var path: [LaneScore] = []

    var body: some View {
        NavigationStack(path: $path) {
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
        if let name = model.launchLane, let lane = board.value?.lanes.first(where: { $0.lane == name }) {
            model.launchLane = nil
            path = [lane]
        }
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
                let count = "\(lane.openPositions ?? 0) open"
                let booked = "booked \(Fmt.rupees(lane.realized, signed: true))"
                let open = "open P&L \(Fmt.rupees(lane.unrealized, signed: true))"
                ViewThatFits(in: .horizontal) {                         // one line where it fits, two on narrow phones
                    Text("\(count) · \(booked) · \(open)").lineLimit(1)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(count) · \(booked)").lineLimit(1)
                        Text(open).lineLimit(1)
                    }
                }
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
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
    @Environment(AppModel.self) private var model
    @State private var book = Loader<LaneBook>()
    @State private var showAllExits = false
    private let preview = 6

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
                        Stat(title: "Since", value: Fmt.dayMonthYear(lane.since))
                    }
                }
                .padding(.vertical, 4)
                if let note = lane.note, !note.isEmpty { Text(note).font(.caption).foregroundStyle(.secondary) }
            }

            bookSections

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
        .refreshable { await loadBook() }
        .polling(every: LaneBooks.refreshSeconds(lane.lane), id: "\(model.version)|\(lane.lane)") { await loadBook() }
    }

    @ViewBuilder
    private var bookSections: some View {
        if let b = book.value {
            Section {
                let top = Array(PositionsListView.sorted(b.positions, by: .pnl).prefix(preview))
                ForEach(top) { PositionRow(position: $0) }
                if b.positions.isEmpty { Text("No open positions.").font(.callout).foregroundStyle(.secondary) }
                if b.positions.count > preview {
                    NavigationLink("All \(b.positions.count) open positions") {
                        PositionsListView(title: "\(lane.label) · open", positions: b.positions)
                    }
                }
            } header: {
                Text("Open positions · \(Fmt.rupees(b.unrealized, signed: true))")
            } footer: {
                StaleNote(error: book.error, updated: book.updated)
            }
            Section {
                ForEach(b.exits.prefix(preview)) { ExitRow(exit: $0) }
                if b.exits.isEmpty { Text("No recent exits.").font(.callout).foregroundStyle(.secondary) }
                if b.exits.count > preview {
                    NavigationLink("All \(b.exits.count) exits") {
                        ExitsListView(title: "\(lane.label) · exits", exits: b.exits, note: b.exitsNote)
                    }
                }
            } header: {
                Text("Recent exits")
            } footer: {
                if let note = b.exitsNote { Text(note) }
            }
        } else if let error = book.error {
            Section("Book") {
                Label(error, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                Button("Try again") { Task { await loadBook() } }
            }
        } else {
            Section("Book") { HStack { ProgressView(); Text("Loading positions…").foregroundStyle(.secondary) } }
        }
    }

    private func loadBook() async {
        let name = lane.lane
        await book.load { try await LaneBooks.load(name, api: model.api()) }
    }
}
