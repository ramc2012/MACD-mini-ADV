import SwiftUI

/// The MACD lane's open positions (/api/portfolio).
struct BookView: View {
    enum Sort: String, CaseIterable, Identifiable {
        case pnl = "P&L", ret = "Return", newest = "Newest"
        var id: String { rawValue }
    }

    @Environment(AppModel.self) private var model
    @State private var book = Loader<Portfolio>()
    @State private var sort = Sort.pnl
    @State private var search = ""

    var body: some View {
        NavigationStack {
            List {
                if let p = book.value {
                    Section {
                        Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 10) {
                            GridRow {
                                Stat(title: "Equity", value: Fmt.rupees(p.equity))
                                Stat(title: "Booked", value: Fmt.rupees(p.realizedPnl, signed: true), color: .pnl(p.realizedPnl))
                                Stat(title: "Open P&L", value: Fmt.rupees(p.unrealizedPnl, signed: true), color: .pnl(p.unrealizedPnl))
                            }
                            GridRow {
                                Stat(title: "Cash", value: Fmt.rupees(p.cash))
                                Stat(title: "In positions", value: Fmt.rupees(p.marketValue))
                                Stat(title: "Positions", value: "\(p.positions.count)")
                            }
                        }
                        .padding(.vertical, 4)
                        StaleNote(error: book.error, updated: book.updated)
                    }
                    Section {
                        Picker("Sort", selection: $sort) {
                            ForEach(Sort.allCases) { Text($0.rawValue).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                        ForEach(rows(p.positions)) { PositionRow(position: $0) }
                    } header: {
                        Text("Open positions")
                    }
                }
            }
            .overlay { LoadState(loader: book, retry: load) }
            .searchable(text: $search, prompt: "Symbol")
            .navigationTitle("MACD book")
            .refreshable { await load() }
            .polling(every: 20, id: model.version) { await load() }
        }
    }

    private func rows(_ all: [Position]) -> [Position] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        let shown = q.isEmpty ? all : all.filter { $0.symbol.uppercased().contains(q) }
        switch sort {
        case .pnl: return shown.sorted { ($0.unrealizedPnl ?? 0) > ($1.unrealizedPnl ?? 0) }
        case .ret: return shown.sorted { ($0.returnPct ?? 0) > ($1.returnPct ?? 0) }
        case .newest: return shown.sorted { (Fmt.date($0.openedAt) ?? .distantPast) > (Fmt.date($1.openedAt) ?? .distantPast) }
        }
    }

    private func load() async {
        await book.load { try await model.api().get("api/portfolio") }
    }
}

struct PositionRow: View {
    let position: Position

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(Fmt.symbol(position.symbol)).font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                Spacer()
                Text(Fmt.rupees(position.unrealizedPnl, signed: true)).font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(position.unrealizedPnl))
            }
            HStack(alignment: .firstTextBaseline) {
                Text("\(Int(position.quantity)) qty\(position.lots.map { " · \(Int($0)) lot\($0 == 1 ? "" : "s")" } ?? "") · \(Fmt.price(position.averagePrice)) → \(Fmt.price(position.lastPrice))")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                Spacer()
                Text(Fmt.pct(position.returnPct)).font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(position.returnPct))
            }
            Text("Opened \(Fmt.dateTime(position.openedAt))\(position.trailingStop.map { " · trail \(Fmt.price($0))" } ?? "")")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}
