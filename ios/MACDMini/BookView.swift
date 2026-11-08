import SwiftUI

/// A lane's open positions in full: sortable and searchable. Pushed from the lane page.
struct PositionsListView: View {
    enum Sort: String, CaseIterable, Identifiable {
        case pnl = "P&L", ret = "Return", newest = "Newest"
        var id: String { rawValue }
    }

    let title: String
    let positions: [BookPosition]
    @State private var sort = Sort.pnl
    @State private var search = ""

    var body: some View {
        List {
            Section {
                Picker("Sort", selection: $sort) {
                    ForEach(Sort.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
                let shown = rows
                ForEach(shown) { PositionRow(position: $0) }
                if shown.isEmpty {
                    Text(search.isEmpty ? "No open positions." : "No open position matches “\(search)”.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            } header: {
                Text("\(positions.count) open")
            }
        }
        .searchable(text: $search, prompt: "Symbol")
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }

    private var rows: [BookPosition] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        let shown = q.isEmpty ? positions : positions.filter { $0.symbol.uppercased().contains(q) }
        return PositionsListView.sorted(shown, by: sort)
    }

    static func sorted(_ rows: [BookPosition], by sort: Sort) -> [BookPosition] {
        switch sort {
        case .pnl: return rows.sorted { ($0.pnl ?? 0) > ($1.pnl ?? 0) }
        case .ret: return rows.sorted { ($0.returnPct ?? 0) > ($1.returnPct ?? 0) }
        case .newest: return rows.sorted { (Fmt.date($0.openedAt) ?? .distantPast) > (Fmt.date($1.openedAt) ?? .distantPast) }
        }
    }
}

/// A lane's exits in full, newest first, searchable.
struct ExitsListView: View {
    let title: String
    let exits: [BookExit]
    let note: String?
    @State private var search = ""

    var body: some View {
        List {
            let q = search.trimmingCharacters(in: .whitespaces).uppercased()
            let shown = q.isEmpty ? exits : exits.filter { $0.symbol.uppercased().contains(q) || ($0.reason ?? "").contains(q) }
            Section {
                ForEach(shown) { ExitRow(exit: $0) }
                if shown.isEmpty {
                    Text(search.isEmpty ? "No exits." : "No exit matches “\(search)”.").font(.callout).foregroundStyle(.secondary)
                }
            } header: {
                Text("\(exits.count) exits · net \(Fmt.rupees(exits.compactMap(\.pnl).reduce(0, +), signed: true))")
            } footer: {
                if let note { Text(note) }
            }
        }
        .searchable(text: $search, prompt: "Symbol or exit reason")
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct PositionRow: View {
    let position: BookPosition

    var body: some View {
        let p = position
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(Fmt.symbol(p.symbol)).font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                if p.quantity < 0 { Badge(text: "SHORT", color: .loss) }
                if let note = p.note { Badge(text: note.uppercased(), color: .secondary) }
                Spacer()
                Text(Fmt.rupees(p.pnl, signed: true)).font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(p.pnl))
            }
            HStack(alignment: .firstTextBaseline) {
                Text("\(Fmt.qty(p.quantity)) qty\(p.lots.map { " · \(Int($0)) lot\($0 == 1 ? "" : "s")" } ?? "") · \(Fmt.price(p.entry)) → \(Fmt.price(p.last))")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8)
                Spacer()
                Text(Fmt.pct(p.returnPct)).font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(p.returnPct))
            }
            Text("Opened \(Fmt.dateTime(p.openedAt))\(p.stop.map { " · \(p.stopLabel) \(Fmt.price($0))" } ?? "")")
                .font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct ExitRow: View {
    let exit: BookExit

    var body: some View {
        let e = exit
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(Fmt.symbol(e.symbol)).font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                if e.side == "SHORT" { Badge(text: "SHORT", color: .loss) }
                Spacer()
                Text(Fmt.rupees(e.pnl, signed: true)).font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(e.pnl))
            }
            HStack(alignment: .firstTextBaseline) {
                Text("\(e.quantity.map { "\(Fmt.qty($0)) qty · " } ?? "")\(Fmt.price(e.entry)) → \(Fmt.price(e.exit)) · \(Fmt.words(e.reason))")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(2)
                Spacer()
                Text(Fmt.pct(e.returnPct)).font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(Color.pnl(e.returnPct))
            }
            Text("\(Fmt.dateTime(e.entryTime)) → \(Fmt.dateTime(e.exitTime))")
                .font(.caption2).foregroundStyle(.tertiary).lineLimit(1).minimumScaleFactor(0.7)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct Badge: View {
    let text: String
    let color: Color
    var body: some View {
        Text(text).font(.caption2.weight(.bold)).padding(.horizontal, 4).padding(.vertical, 1)
            .background(color.opacity(0.16), in: .rect(cornerRadius: 3)).foregroundStyle(color)
    }
}
