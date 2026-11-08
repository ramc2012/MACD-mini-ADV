import Charts
import SwiftUI

/// Market breadth, as on the web app's Market breadth tab. Views only: none of these is a proven trade signal.
struct BreadthView: View {
    @Environment(AppModel.self) private var model
    @State private var path: [BreadthPage] = []

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section {
                    ForEach(BreadthPage.allCases, id: \.self) { page in
                        NavigationLink(value: page) {
                            Label { VStack(alignment: .leading, spacing: 2) {
                                Text(page.title).font(.headline)
                                Text(page.blurb).font(.caption).foregroundStyle(.secondary)
                            } } icon: { Image(systemName: page.icon).foregroundStyle(Color.coilUp) }
                        }
                    }
                } footer: {
                    Text("Built from the engine's own stored bars and quotes; no broker calls. Views only: none of these is a proven trade signal.")
                }
            }
            .navigationTitle("Market breadth")
            .navigationDestination(for: BreadthPage.self) { page in
                switch page {
                case .highs: DayHighsView()
                case .wheel: SectorWheelView()
                case .coil: SectorCoilView()
                case .atm: AtmHeatmapView()
                case .rrg: RRGView()
                }
            }
            .onAppear {
                if let page = model.launchBreadth { model.launchBreadth = nil; path = [page] }
            }
        }
    }
}

extension BreadthPage {
    var title: String {
        switch self {
        case .highs: "Day highs"
        case .wheel: "Sector wheel"
        case .coil: "Sector coil"
        case .atm: "ATM heat map"
        case .rrg: "Rotation (RRG)"
        }
    }
    var blurb: String {
        switch self {
        case .highs: "Spots and ATM legs closing a 15-min bar at the day's high or low"
        case .wheel: "Sector and stock returns over 1–60 sessions, own or vs NIFTY 50"
        case .coil: "Each sector's path since a start date, turning at each checkpoint"
        case .atm: "Today's ATM CE and PE premium change next to the spot's"
        case .rrg: "Relative strength vs momentum: leading, weakening, lagging, improving"
        }
    }
    var icon: String {
        switch self {
        case .highs: "arrow.up.to.line"
        case .wheel: "circle.dashed"
        case .coil: "arrow.uturn.down.circle"
        case .atm: "square.grid.3x3.fill"
        case .rrg: "scope"
        }
    }
}

// MARK: - Day highs

struct DayHighsView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<DayHighs>()
    @AppStorage("highs.kind") private var kind = "SPOT"
    @AppStorage("highs.filter") private var filter = "high"
    @State private var search = ""

    var body: some View {
        List {
            if let d = data.value {
                Section {
                    Picker("Leg", selection: $kind) {
                        Text("Spots").tag("SPOT"); Text("ATM CE").tag("CE"); Text("ATM PE").tag("PE")
                    }
                    .pickerStyle(.segmented)
                    Picker("Show", selection: $filter) {
                        Text("At high").tag("high"); Text("At low").tag("low"); Text("All").tag("all")
                    }
                    .pickerStyle(.segmented)
                    let listed = d.listed?[kind] ?? 0
                    Text("\(d.atHigh?[kind] ?? 0) of \(listed) at the day high · \(d.atLow?[kind] ?? 0) at the day low")
                        .font(.subheadline.weight(.semibold))
                    Text("Session \(Fmt.dayYear(d.session)) · last completed \((d.timeframeSeconds ?? 900) / 60)-min bar · checked \(Fmt.dateTime(d.generatedAt))")
                        .font(.caption).foregroundStyle(.secondary)
                    StaleNote(error: data.error, updated: data.updated)
                }
                let shown = rows(d)
                Section {
                    ForEach(shown) { DayHighRowView(row: $0) }
                    if shown.isEmpty {
                        Text((d.listed?[kind] ?? 0) == 0 ? "No \(kind == "SPOT" ? "spots" : "ATM \(kind) legs") listed for this session."
                                                         : "Nothing matches.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                } footer: {
                    Text("At high: the last completed bar closed at or above every earlier high of the day. Streak: bars in a row that did so. Lists from 09:45 IST.")
                }
            }
        }
        .overlay { LoadState(loader: data, retry: load) }
        .searchable(text: $search, prompt: "Symbol or group")
        .navigationTitle("Day highs")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .polling(every: 60, id: model.version) { await load() }
    }

    private func rows(_ d: DayHighs) -> [DayHighRow] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        return d.rows.filter { r in
            r.kind == kind
                && (filter == "all" || (filter == "high" ? r.atHigh == true : r.atLow == true))
                && (q.isEmpty || r.symbol.uppercased().contains(q) || r.name.uppercased().contains(q) || (r.group ?? "").uppercased().contains(q))
        }
        .sorted { a, b in
            let sa = filter == "low" ? (a.lowStreak ?? 0) : (a.highStreak ?? 0)
            let sb = filter == "low" ? (b.lowStreak ?? 0) : (b.highStreak ?? 0)
            if sa != sb { return sa > sb }
            return filter == "low" ? (a.changePct ?? 0) < (b.changePct ?? 0) : (a.changePct ?? 0) > (b.changePct ?? 0)
        }
    }

    private func load() async { await data.load { try await model.api().get("api/day-highs") } }
}

private struct DayHighRowView: View {
    let row: DayHighRow
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(row.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                    if row.stale == true { Badge(text: "STALE", color: .orange) }
                }
                Text(row.group ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                HStack(spacing: 8) {
                    Text(Fmt.price(row.close)).font(.subheadline.monospacedDigit())
                    Text(Fmt.pct(row.changePct, digits: 2)).font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(Color.pnl(row.changePct))
                }
                Text(streak).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var streak: String {
        if row.atHigh == true { return "at high · \(row.highStreak ?? 0) bar\(row.highStreak == 1 ? "" : "s")" }
        if row.atLow == true { return "at low · \(row.lowStreak ?? 0) bar\(row.lowStreak == 1 ? "" : "s")" }
        return "\(Fmt.pct(row.fromHighPct.map { -$0 }, digits: 2)) from high"
    }
}

// MARK: - Sector wheel

struct SectorWheelView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<SectorWheel>()
    @AppStorage("wheel.window") private var window = "5D"
    @AppStorage("wheel.relative") private var relative = true
    @AppStorage("wheel.scope") private var scope = "sector"
    @State private var search = ""

    var body: some View {
        List {
            if let w = data.value {
                Section {
                    Picker("Window", selection: $window) { ForEach(w.windows, id: \.self) { Text($0).tag($0) } }
                        .pickerStyle(.segmented)
                    Picker("Measure", selection: $relative) { Text("Own return").tag(false); Text("vs NIFTY 50").tag(true) }
                        .pickerStyle(.segmented)
                    Picker("Rows", selection: $scope) { Text("Sectors").tag("sector"); Text("Broad").tag("broad"); Text("Stocks").tag("stock") }
                        .pickerStyle(.segmented)
                    if let bench = w.benchmarkChange?[window] ?? nil {
                        Text("NIFTY 50 \(Fmt.pct(bench, digits: 2)) over \(window) · closes to \(Fmt.dayYear(w.asOf))\(w.live == true ? " (live)" : "")")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    StaleNote(error: data.error, updated: data.updated)
                }
                if scope == "sector", let ms = w.monthStart { monthStart(ms, rows: w.rows) }
                let shown = rows(w)
                let scale = max(0.5, shown.compactMap { $0.value(window, relative: relative) }.map(abs).max() ?? 1)
                Section {
                    ForEach(shown) { r in WheelBar(row: r, value: r.value(window, relative: relative), scale: scale, relative: relative) }
                    if shown.isEmpty { Text("Nothing matches.").font(.callout).foregroundStyle(.secondary) }
                } header: {
                    Text(relative ? "\(window) return minus NIFTY 50's" : "\(window) return")
                } footer: {
                    Text("A view only: sector strength has lasted about 2–4 weeks in past studies; as a trade signal it is not proven.")
                }
            }
        }
        .overlay { LoadState(loader: data, retry: load) }
        .searchable(text: $search, prompt: scope == "stock" ? "Stock or sector" : "Name")
        .navigationTitle("Sector wheel")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .polling(every: 120, id: model.version) { await load() }
    }

    @ViewBuilder
    private func monthStart(_ ms: SectorWheel.MonthStart, rows: [WheelRow]) -> some View {
        let name: (String) -> String = { s in rows.first { $0.symbol == s }?.name ?? Fmt.symbol(s) }
        Section {
            if let leaders = ms.leaders, !leaders.isEmpty {
                LabeledContent("Leaders", value: leaders.map(name).joined(separator: ", "))
            }
            if let laggards = ms.laggards, !laggards.isEmpty {
                LabeledContent("Laggards", value: laggards.map(name).joined(separator: ", "))
            }
        } header: {
            Text("Month-start leaders")
        } footer: {
            Text("Formed \(Fmt.dayYear(ms.formedOn)), \(ms.sessionsSince ?? 0) session\(ms.sessionsSince == 1 ? "" : "s") ago; month-start leaders have typically lasted about \(ms.typicalRun ?? 0) sessions.")
        }
    }

    private func rows(_ w: SectorWheel) -> [WheelRow] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        return w.rows
            .filter { $0.kind == scope || (scope == "sector" && $0.kind == "benchmark" && !relative) }
            .filter { q.isEmpty || $0.name.uppercased().contains(q) || $0.symbol.uppercased().contains(q) || ($0.group ?? "").uppercased().contains(q) }
            .sorted { ($0.value(window, relative: relative) ?? -.infinity) > ($1.value(window, relative: relative) ?? -.infinity) }
    }

    private func load() async { await data.load { try await model.api().get("api/sector-wheel") } }
}

private struct WheelBar: View {
    let row: WheelRow
    let value: Double?
    let scale: Double
    let relative: Bool

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(row.name).font(.subheadline.weight(row.kind == "benchmark" ? .bold : .regular)).lineLimit(1)
                if row.kind == "stock", let g = row.group { Text(g).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
            }
            .frame(width: 118, alignment: .leading)
            GeometryReader { geo in
                let half = geo.size.width / 2
                let w = CGFloat(min(1, abs(value ?? 0) / scale)) * half
                ZStack(alignment: .leading) {
                    Rectangle().fill(.secondary.opacity(0.35)).frame(width: 1).offset(x: half)
                    RoundedRectangle(cornerRadius: 3)
                        .fill((value ?? 0) >= 0 ? Color.coilUp : Color.coilDown)
                        .frame(width: max(1, w), height: 12)
                        .offset(x: (value ?? 0) >= 0 ? half : half - w)
                }
                .frame(maxHeight: .infinity)
            }
            .frame(height: 18)
            Text(value.map { relative ? String(format: "%@%.2f", $0 < 0 ? "−" : "+", abs($0)) : Fmt.pct($0, digits: 2) } ?? "—")
                .font(.caption.weight(.semibold).monospacedDigit()).frame(width: 58, alignment: .trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(row.name), \(Fmt.pct(value, digits: 2))")
    }
}

// MARK: - ATM heat map

struct AtmHeatmapView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<AtmHeatmap>()
    @AppStorage("atm.period") private var period = "1D"
    @AppStorage("atm.kind") private var kind = "index"
    @AppStorage("atm.sort") private var sort = "spot"
    @State private var search = ""

    var body: some View {
        List {
            if let h = data.value {
                Section {
                    Picker("Period", selection: $period) { ForEach(h.periods, id: \.self) { Text($0).tag($0) } }
                        .pickerStyle(.segmented)
                    Picker("Rows", selection: $kind) { Text("Indices").tag("index"); Text("Sectors").tag("sector"); Text("Stocks").tag("stock") }
                        .pickerStyle(.segmented)
                    Picker("Sort by", selection: $sort) { Text("Spot").tag("spot"); Text("CE").tag("ce"); Text("PE").tag("pe") }
                        .pickerStyle(.segmented)
                    Text("Change since \(Fmt.dateTime(h.references?[period])) · as of \(Fmt.dateTime(h.asOf))")
                        .font(.caption).foregroundStyle(.secondary)
                    StaleNote(error: data.error, updated: data.updated)
                }
                let shown = rows(h)
                Section {
                    HStack {
                        Text("Underlying").frame(maxWidth: .infinity, alignment: .leading)
                        Text("Spot").frame(width: 62); Text("ATM CE").frame(width: 66); Text("ATM PE").frame(width: 66)
                    }
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(shown) { AtmRowView(row: $0, period: period) }
                    if shown.isEmpty { Text("Nothing matches.").font(.callout).foregroundStyle(.secondary) }
                } footer: {
                    Text("Premium change on each leg's own price; * = the leg started trading after the period began, so its change runs from its first trade. A view only.")
                }
            }
        }
        .overlay { LoadState(loader: data, retry: load) }
        .searchable(text: $search, prompt: "Underlying")
        .navigationTitle("ATM heat map")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .polling(every: 60, id: model.version) { await load() }
    }

    private func rows(_ h: AtmHeatmap) -> [AtmRow] {
        let q = search.trimmingCharacters(in: .whitespaces).uppercased()
        let key: (AtmRow) -> Double = { r in
            let v: Double?? = sort == "ce" ? r.ce?.change?[period] : sort == "pe" ? r.pe?.change?[period] : r.spotChange?[period]
            return (v ?? nil) ?? -.infinity
        }
        return h.rows.filter { $0.kind == kind && (q.isEmpty || $0.underlying.uppercased().contains(q)) }.sorted { key($0) > key($1) }
    }

    private func load() async { await data.load { try await model.api().get("api/heatmap/atm") } }
}

private struct AtmRowView: View {
    let row: AtmRow
    let period: String

    var body: some View {
        HStack(spacing: 4) {
            VStack(alignment: .leading, spacing: 1) {
                Text(row.underlying).font(.subheadline.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.8)
                Text(sub).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            cell((row.spotChange?[period]) ?? nil, scale: 3, partial: false).frame(width: 62)
            cell((row.ce?.change?[period]) ?? nil, scale: 100, partial: row.ce?.partial?[period] != nil).frame(width: 66)
            cell((row.pe?.change?[period]) ?? nil, scale: 100, partial: row.pe?.partial?[period] != nil).frame(width: 66)
        }
        .accessibilityElement(children: .combine)
    }

    private var sub: String {
        var parts: [String] = []
        if let s = row.strike { parts.append(Fmt.price(s).replacingOccurrences(of: ".00", with: "")) }
        if let e = row.expiry, !e.isEmpty { parts.append(Fmt.day(e)) }
        return parts.isEmpty ? Fmt.price(row.spot) : parts.joined(separator: " · ")
    }

    private func cell(_ v: Double?, scale: Double, partial: Bool) -> some View {
        let strength = min(1, abs(v ?? 0) / scale)
        let tint = (v ?? 0) >= 0 ? Color.gain : Color.loss
        return Text(v.map { Fmt.pct($0, digits: abs($0) >= 100 ? 0 : 1) + (partial ? "*" : "") } ?? "—")
            .font(.caption.weight(.semibold).monospacedDigit())
            .lineLimit(1).minimumScaleFactor(0.7)
            .frame(maxWidth: .infinity).padding(.vertical, 6)
            .background(v == nil ? Color.clear : tint.opacity(0.10 + 0.55 * strength), in: .rect(cornerRadius: 4))
    }
}

// MARK: - RRG

struct RRGView: View {
    @Environment(AppModel.self) private var model
    @State private var data = Loader<RRG>()
    @AppStorage("rrg.timeframe") private var timeframe = 1800
    @State private var picked: RRGSector?

    private let frames = [(900, "15 min"), (1800, "30 min"), (3600, "1 hour"), (86_400, "1 day")]
    static let quadrants = ["leading", "weakening", "lagging", "improving"]
    private let tailShown = 6

    var body: some View {
        List {
            Section {
                Picker("Bars", selection: $timeframe) { ForEach(frames, id: \.0) { Text($0.1).tag($0.0) } }
                    .pickerStyle(.segmented)
                if let r = data.value {
                    chart(r).frame(height: 340).padding(.vertical, 6)
                    legend
                    Text("vs \(Fmt.symbol(r.benchmark ?? "NIFTY 50")) · \(r.window ?? 14)-bar window · last \(tailShown) bars of each tail · \(Fmt.dateTime(r.generatedAt))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                StaleNote(error: data.error, updated: data.updated)
            } footer: {
                Text("Right of 100: stronger than the benchmark. Above 100: that strength is rising. Tap a sector for its stocks.")
            }
            if let r = data.value {
                ForEach(Self.quadrants, id: \.self) { q in
                    let sectors = r.sectors.filter { $0.quadrant == q }.sorted { $0.x > $1.x }
                    if !sectors.isEmpty {
                        Section {
                            ForEach(sectors) { s in
                                Button { picked = s } label: {
                                    HStack {
                                        Text(s.sector).foregroundStyle(.primary)
                                        Spacer()
                                        Text(String(format: "%.2f · %.2f", s.x, s.y)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        } header: {
                            Text("\(q.capitalized) · \(sectors.count) sector\(sectors.count == 1 ? "" : "s"), \(r.symbols.filter { $0.quadrant == q }.count) stocks")
                        }
                    }
                }
            }
        }
        .overlay { LoadState(loader: data, retry: load) }
        .navigationTitle("Rotation (RRG)")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .polling(every: 120, id: "\(model.version)|\(timeframe)") { await load() }
        .sheet(item: $picked) { s in RRGSectorDetail(sector: s, stocks: data.value?.symbols.filter { $0.sector == s.sector } ?? []) }
    }

    static func color(_ quadrant: String?) -> Color {
        switch quadrant {
        case "leading": .gain
        case "weakening": .orange
        case "lagging": .loss
        default: .coilUp
        }
    }

    private var legend: some View {
        HStack(spacing: 12) {
            ForEach(Self.quadrants, id: \.self) { q in
                HStack(spacing: 4) { Circle().fill(Self.color(q)).frame(width: 8, height: 8); Text(q.capitalized) }
            }
        }
        .font(.caption)
    }

    private func chart(_ r: RRG) -> some View {
        let pts = r.sectors.flatMap { ($0.tail ?? []).suffix(tailShown) + [RRGPoint(x: $0.x, y: $0.y)] }
        let xs = pts.map(\.x) + [100], ys = pts.map(\.y) + [100]
        let padX = max(0.2, ((xs.max() ?? 101) - (xs.min() ?? 99)) * 0.08), padY = max(0.2, ((ys.max() ?? 101) - (ys.min() ?? 99)) * 0.08)
        let xd = ((xs.min() ?? 99) - padX)...((xs.max() ?? 101) + padX)
        let yd = ((ys.min() ?? 99) - padY)...((ys.max() ?? 101) + padY)
        return Chart {
            RuleMark(x: .value("x", 100)).foregroundStyle(.secondary.opacity(0.5))
            RuleMark(y: .value("y", 100)).foregroundStyle(.secondary.opacity(0.5))
            ForEach(r.sectors) { s in
                ForEach(Array((s.tail ?? []).suffix(tailShown).enumerated()), id: \.offset) { _, p in
                    LineMark(x: .value("RS ratio", p.x), y: .value("RS momentum", p.y), series: .value("Sector", s.sector))
                        .foregroundStyle(Self.color(s.quadrant).opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 1.5))
                }
                PointMark(x: .value("RS ratio", s.x), y: .value("RS momentum", s.y))
                    .foregroundStyle(Self.color(s.quadrant))
                    .symbolSize(60)
                    .annotation(position: .top, spacing: 2) {
                        Text(short(s.sector)).font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
                    }
            }
        }
        .chartXScale(domain: xd)
        .chartYScale(domain: yd)
        .chartXAxisLabel("RS ratio →")
        .chartYAxisLabel("RS momentum →")
    }

    private func short(_ s: String) -> String {
        let first = s.split(separator: " ").first.map(String.init) ?? s
        return first == "Capital" ? "Cap goods" : first == "Consumer" ? "Durables" : first
    }

    private func load() async {
        await data.load { try await model.api().get("api/rrg", query: ["timeframe_seconds": "\(timeframe)", "tail": "15"]) }
    }
}

private struct RRGSectorDetail: View {
    let sector: RRGSector
    let stocks: [RRGSymbol]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(RRGView.quadrants, id: \.self) { q in
                    let rows = stocks.filter { $0.quadrant == q }.sorted { $0.x > $1.x }
                    if !rows.isEmpty {
                        Section("\(q.capitalized) · \(rows.count)") {
                            ForEach(rows) { s in
                                HStack {
                                    Circle().fill(RRGView.color(q)).frame(width: 8, height: 8)
                                    Text(Fmt.symbol(s.symbol).replacingOccurrences(of: "-EQ", with: ""))
                                    Spacer()
                                    Text(String(format: "%.2f · %.2f", s.x, s.y)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                if stocks.isEmpty { Text("No member stocks in this snapshot.").foregroundStyle(.secondary) }
            }
            .navigationTitle(sector.sector)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}
