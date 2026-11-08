import SwiftUI

/// The engine's latest signals (/api/signals), newest first, grouped by IST day.
struct SignalsView: View {
    enum Side: String, CaseIterable, Identifiable {
        case all = "All", buy = "Buy", sell = "Sell"
        var id: String { rawValue }
    }

    @Environment(AppModel.self) private var model
    @State private var signals = Loader<[Signal]>()
    @State private var side = Side.all
    @State private var search = ""

    var body: some View {
        NavigationStack {
            List {
                if signals.value != nil {
                    Section {
                        Picker("Side", selection: $side) {
                            ForEach(Side.allCases) { Text($0.rawValue).tag($0) }
                        }
                        .pickerStyle(.segmented)
                        .listRowBackground(Color.clear)
                        .listRowInsets(EdgeInsets())
                    }
                    Section {
                        StaleNote(error: signals.error, updated: signals.updated)
                        if days.isEmpty {
                            Text(search.isEmpty ? "No \(side == .all ? "" : side.rawValue.lowercased() + " ")signals." : "No signal matches “\(search)”.")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                    }
                    ForEach(days, id: \.day) { group in
                        Section(Fmt.dayYear(group.day)) {
                            ForEach(group.items) { SignalRow(signal: $0) }
                        }
                    }
                }
            }
            .overlay { LoadState(loader: signals, retry: load) }
            .searchable(text: $search, prompt: "Symbol")
            .navigationTitle("Signals")
            .refreshable { await load() }
            .polling(every: 20, id: model.version) { await load() }
        }
    }

    private var days: [(day: String, items: [Signal])] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        let shown = (signals.value ?? []).filter { s in
            (side == .all || s.side.uppercased() == side.rawValue.uppercased()) && (q.isEmpty || s.symbol.uppercased().contains(q))
        }
        let ist = DateFormatter()
        ist.locale = Locale(identifier: "en_US_POSIX")
        ist.timeZone = Fmt.ist
        ist.dateFormat = "yyyy-MM-dd"
        let sorted = shown.sorted { (Fmt.date($0.timestamp) ?? .distantPast) > (Fmt.date($1.timestamp) ?? .distantPast) }
        var out: [(day: String, items: [Signal])] = []
        for s in sorted {
            let day = Fmt.date(s.timestamp).map(ist.string) ?? "—"
            if out.last?.day == day { out[out.count - 1].items.append(s) } else { out.append((day, [s])) }
        }
        return out
    }

    private func load() async {
        await signals.load { try await model.api().get("api/signals") }
    }
}

struct SignalRow: View {
    let signal: Signal
    private var buy: Bool { signal.side.uppercased() == "BUY" }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(buy ? "BUY" : "SELL")
                .font(.caption2.weight(.bold)).frame(width: 38).padding(.vertical, 3)
                .background((buy ? Color.gain : Color.loss).opacity(0.15), in: .rect(cornerRadius: 4))
                .foregroundStyle(buy ? Color.gain : Color.loss)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline) {
                    Text(Fmt.symbol(signal.symbol)).font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                    Spacer()
                    Text(Fmt.price(signal.price)).font(.subheadline.monospacedDigit())
                }
                Text(Fmt.words(signal.kind)).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                HStack {
                    Text(Fmt.dateTime(signal.timestamp))
                    if let f = signal.sizeFraction, f < 0.999 { Text("· size \(Fmt.pct(f * 100, digits: 0, signed: false))") }
                }
                .font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
