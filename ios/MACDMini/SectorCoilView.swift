// Native Sector coil (/api/sector-coil), the same geometry as frontend/src/sectorCoilMath.ts: each index starts at 0 %
// on the base date and runs along one row to its return at each checkpoint. A reversal is a half-circle U-turn;
// a continuation is a small loop. Blue right of zero, red left of it, an arrow at today's value.
import SwiftUI

enum CoilMath {
    struct Axis { let lo: Double; let hi: Double; let ticks: [Double] }

    /// A round tick step and padded domain that always include zero.
    static func axis(_ values: [Double], maxTicks: Double = 6) -> Axis {
        let lo0 = min(0, values.min() ?? 0), hi0 = max(0, values.max() ?? 0)
        let span = max(1, hi0 - lo0)
        let step = [1, 2, 2.5, 5, 10, 20, 25, 50, 100].first { span / $0 <= maxTicks } ?? 200
        let lo = (lo0 / step - 0.15).rounded(.down) * step, hi = (hi0 / step + 0.15).rounded(.up) * step
        let ticks = stride(from: lo, through: hi + 1e-9, by: step).map { ($0 * 100).rounded() / 100 }
        return Axis(lo: lo, hi: hi, ticks: ticks)
    }

    /// Direction of travel on each row (1 right, -1 left); a flat row keeps the previous direction.
    static func directions(_ xs: [CGFloat]) -> [CGFloat] {
        var out: [CGFloat] = []
        for k in 1..<max(1, xs.count) {
            let d = xs[k] - xs[k - 1]
            out.append(d > 0 ? 1 : d < 0 ? -1 : (out.last ?? 1))
        }
        return out
    }

    static func path(_ xs: [CGFloat], top: CGFloat, rowH: CGFloat) -> Path {
        var p = Path()
        guard xs.count > 1 else { return p }
        let dirs = directions(xs)
        let k = rowH / 2 * 4 / 3, c = rowH * 0.9          // cubic half-circle; loop reach
        p.move(to: CGPoint(x: xs[0], y: top))
        for i in 1..<xs.count {
            let y = top + CGFloat(i - 1) * rowH, x = xs[i]
            p.addLine(to: CGPoint(x: x, y: y))
            if i == xs.count - 1 { break }
            let dir = dirs[i - 1], next = dirs[i], y2 = y + rowH
            if next != dir {
                p.addCurve(to: CGPoint(x: x, y: y2), control1: CGPoint(x: x + dir * k, y: y), control2: CGPoint(x: x + dir * k, y: y2))
            } else {
                p.addCurve(to: CGPoint(x: x, y: y2), control1: CGPoint(x: x + dir * c, y: y), control2: CGPoint(x: x - dir * c, y: y2))
            }
        }
        return p
    }

    static func arrow(_ xs: [CGFloat], top: CGFloat, rowH: CGFloat, size: CGFloat = 4.5) -> Path {
        var p = Path()
        guard xs.count > 1, let x = xs.last else { return p }
        let dir = directions(xs).last ?? 1
        let y = top + CGFloat(xs.count - 2) * rowH
        p.move(to: CGPoint(x: x + dir * size, y: y))
        p.addLine(to: CGPoint(x: x - dir * size * 0.6, y: y - size * 0.75))
        p.addLine(to: CGPoint(x: x - dir * size * 0.6, y: y + size * 0.75))
        p.closeSubpath()
        return p
    }

    static func rowHeight(_ segments: Int) -> CGFloat { max(2.6, min(11, 52 / CGFloat(max(1, segments)))) }

    /// One-line reading: the biggest give-back from a positive peak and the biggest gain over the last step.
    static func story(_ rows: [CoilRow], days: [String], relative: Bool = false) -> (title: String, text: String) {
        // Own return reads in %; vs NIFTY 50 reads in points of outperformance.
        let val = { (v: Double) in relative ? Fmt.pct(v).replacingOccurrences(of: "%", with: " pts") : Fmt.pct(v) }
        let ahead = relative ? " ahead of NIFTY 50" : ""
        let sectors = rows.filter { $0.kind != "benchmark" }
        guard !sectors.isEmpty, days.count >= 3 else { return ("Sector progress", "") }
        let n = days.count - 1
        var giver: (label: String, peak: Double, at: Int, now: Double, drop: Double)?
        var gainer: (label: String, gain: Double, now: Double)?
        for r in sectors {
            let v = r.filled
            guard v.count == days.count else { continue }
            var at = 1
            for i in 1..<n where v[i] > v[at] { at = i }
            let drop = v[at] - v[n]
            if v[at] > 0, drop > (giver?.drop ?? -.infinity) { giver = (r.label, v[at], at, v[n], drop) }
            let gain = v[n] - v[n - 1]
            if gain > (gainer?.gain ?? -.infinity) { gainer = (r.label, gain, v[n]) }
        }
        var parts: [String] = []
        if let g = giver, g.drop >= 1 {
            parts.append("On \(Fmt.day(days[g.at])) \(g.label) was \(val(g.peak))\(ahead) since \(Fmt.day(days[0])); it is \(val(g.now)) now.")
        }
        if let g = gainer, g.gain > 0 {
            parts.append("\(g.label) made up the most ground since \(Fmt.day(days[n - 1])): \(Fmt.pct(g.gain).replacingOccurrences(of: "%", with: " pts")), to \(val(g.now)).")
        }
        let title = (giver?.drop ?? 0) >= 5 && (gainer?.gain ?? 0) >= 3 ? "The big reversal"
            : (giver?.drop ?? 0) >= 5 ? "Leaders giving back" : "Sector progress"
        return (title, parts.joined(separator: " "))
    }
}

struct SectorCoilView: View {
    @Environment(AppModel.self) private var model
    @State private var coil = Loader<SectorCoil>()
    @AppStorage("coil.base") private var base = "YTD"
    @AppStorage("coil.step") private var step = "M"
    @AppStorage("coil.measure") private var measure = "own"
    @State private var picked: CoilRow?

    private let bases = [("YTD", "Since 31 Dec"), ("1Y", "1 year"), ("6M", "6 months"), ("3M", "3 months"), ("1M", "1 month")]
    private let steps = [("M", "Monthly"), ("2W", "Fortnightly"), ("W", "Weekly"), ("TEMPLATE", "One month ago")]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                controls
                if let c = coil.value { chart(c) }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .overlay { LoadState(loader: coil, retry: load) }
        .navigationTitle("Sector coil")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .polling(every: 60, id: "\(model.version)|\(base)|\(step)|\(measure)") { await load() }
        .sheet(item: $picked) { row in
            if let c = coil.value { CoilRowDetail(row: row, days: [c.baseDay ?? ""] + c.points, unit: unit(c)) }
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Menu {
                    Picker("Start", selection: $base) { ForEach(bases, id: \.0) { Text($0.1).tag($0.0) } }
                } label: { chip(bases.first { $0.0 == base }?.1 ?? base, icon: "calendar") }
                Menu {
                    Picker("Turns", selection: $step) { ForEach(steps, id: \.0) { Text($0.1).tag($0.0) } }
                } label: { chip(steps.first { $0.0 == step }?.1 ?? step, icon: "arrow.uturn.down") }
                Spacer()
            }
            Picker("Measure", selection: $measure) {
                Text("Own return").tag("own")
                Text("vs NIFTY 50").tag("relative")
            }
            .pickerStyle(.segmented)
        }
        .padding(.top, 8)
    }

    private func chip(_ text: String, icon: String) -> some View {
        Label(text, systemImage: icon).font(.subheadline.weight(.medium))
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(.secondary.opacity(0.12), in: .capsule)
    }

    private func unit(_ c: SectorCoil) -> String { c.measure == "relative" ? " pts" : "%" }

    @ViewBuilder
    private func chart(_ c: SectorCoil) -> some View {
        let days = [c.baseDay ?? ""] + c.points
        let story = CoilMath.story(c.rows, days: days, relative: c.measure == "relative")
        let bench = c.rows.filter { $0.kind == "benchmark" }
        let others = c.rows.filter { $0.kind != "benchmark" }.sorted { $0.latest > $1.latest }
        let axis = CoilMath.axis(c.rows.flatMap(\.filled))
        let rowH = CoilMath.rowHeight(c.points.count)

        VStack(alignment: .leading, spacing: 6) {
            Text(story.title).font(.title3.weight(.bold))
            if !story.text.isEmpty { Text(story.text).font(.subheadline).foregroundStyle(.secondary) }
            Text("0 = close of \(Fmt.dayYear(c.baseDay)) · turns at \(c.points.count) checkpoints to \(Fmt.dayYear(c.asOf))\(c.live ? " (live)" : "")")
                .font(.caption).foregroundStyle(.secondary)
        }

        VStack(spacing: 0) {
            CoilAxis(axis: axis, unit: unit(c))
            ForEach(bench) { row in
                CoilBlock(row: row, axis: axis, rowH: rowH, unit: unit(c), bold: true).onTapGesture { picked = row }
            }
            if !bench.isEmpty {
                Line().stroke(.secondary.opacity(0.6), style: StrokeStyle(lineWidth: 1, dash: [3, 3])).frame(height: 1).padding(.vertical, 4)
            }
            ForEach(others) { row in
                CoilBlock(row: row, axis: axis, rowH: rowH, unit: unit(c), bold: false).onTapGesture { picked = row }
            }
        }

        HStack(spacing: 16) {
            Label("Up since start", systemImage: "circle.fill").foregroundStyle(Color.coilUp)
            Label("Down since start", systemImage: "circle.fill").foregroundStyle(Color.coilDown)
        }
        .font(.caption)
        .labelStyle(DotLabelStyle())

        if !c.missing.isEmpty {
            Text("No close for \(c.missing.map(Fmt.symbol).joined(separator: ", ")).").font(.caption).foregroundStyle(.secondary)
        }
        Text("Tap a sector for its value at each turn. A view only: sector rotation as a trade signal is not proven.")
            .font(.caption).foregroundStyle(.tertiary)
        StaleNote(error: coil.error, updated: coil.updated)
    }

    private func load() async {
        await coil.load { try await model.api().get("api/sector-coil", query: ["base": base, "step": step, "measure": measure]) }
    }
}

private struct DotLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) { configuration.icon.imageScale(.small); configuration.title.foregroundStyle(.secondary) }
    }
}

private struct Line: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        return p
    }
}

private let labelWidth: CGFloat = 104

private struct CoilAxis: View {
    let axis: CoilMath.Axis
    let unit: String
    var body: some View {
        HStack(spacing: 0) {
            Color.clear.frame(width: labelWidth, height: 1)
            Canvas { ctx, size in
                for t in axis.ticks {
                    let x = CGFloat((t - axis.lo) / (axis.hi - axis.lo)) * size.width
                    let text = Text(t == 0 ? "0" : "\(t > 0 ? "+" : "−")\(abs(t).formatted())")
                        .font(.caption2.monospacedDigit()).foregroundStyle(t == 0 ? .primary : .secondary)
                    // Ticks on the plot's edges keep their gridline but no label: a centred label would
                    // be clipped and an inset one crowds its neighbour.
                    if x < 12 || x > size.width - 12 { continue }
                    ctx.draw(text, at: CGPoint(x: x, y: size.height / 2))
                }
            }
            .frame(height: 18)
        }
        .accessibilityHidden(true)
    }
}

private struct CoilBlock: View {
    let row: CoilRow
    let axis: CoilMath.Axis
    let rowH: CGFloat
    let unit: String
    let bold: Bool

    var body: some View {
        let values = row.filled
        let pad: CGFloat = 7
        let height = pad * 2 + CGFloat(max(0, values.count - 2)) * rowH
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                Text(row.label).font(bold ? .caption.weight(.bold) : .caption.weight(.medium)).lineLimit(1).minimumScaleFactor(0.75)
                Text(Fmt.pct(row.latest).replacingOccurrences(of: "%", with: unit))
                    .font(.caption2.weight(.semibold).monospacedDigit())
                    .foregroundStyle(row.latest >= 0 ? Color.coilUp : Color.coilDown)
            }
            .frame(width: labelWidth, alignment: .leading)
            Canvas { ctx, size in
                let x = { (v: Double) -> CGFloat in CGFloat((v - axis.lo) / (axis.hi - axis.lo)) * size.width }
                for t in axis.ticks {
                    var g = Path()
                    g.move(to: CGPoint(x: x(t), y: 0))
                    g.addLine(to: CGPoint(x: x(t), y: size.height))
                    ctx.stroke(g, with: .color(t == 0 ? .primary.opacity(0.55) : .secondary.opacity(0.16)), lineWidth: t == 0 ? 1 : 0.5)
                }
                let xs = values.map(x)
                let line = CoilMath.path(xs, top: pad, rowH: rowH)
                let x0 = x(0)
                let style = StrokeStyle(lineWidth: bold ? 2 : 1.6, lineCap: .round, lineJoin: .round)
                var up = ctx
                up.clip(to: Path(CGRect(x: x0, y: 0, width: max(0, size.width - x0), height: size.height)))
                up.stroke(line, with: .color(.coilUp), style: style)
                var down = ctx
                down.clip(to: Path(CGRect(x: 0, y: 0, width: x0, height: size.height)))
                down.stroke(line, with: .color(.coilDown), style: style)
                ctx.fill(CoilMath.arrow(xs, top: pad, rowH: rowH), with: .color(row.latest >= 0 ? .coilUp : .coilDown))
            }
            .frame(height: height)
        }
        .contentShape(Rectangle())
        .accessibilityElement()
        .accessibilityLabel("\(row.label), \(Fmt.pct(row.latest).replacingOccurrences(of: "%", with: unit))")
        .accessibilityAddTraits(.isButton)
    }
}

private struct CoilRowDetail: View {
    let row: CoilRow
    let days: [String]
    let unit: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let price = row.price { LabeledContent("Last close", value: Fmt.price(price)) }
                Section("At each turn") {
                    ForEach(Array(zip(days.indices, days)), id: \.0) { i, day in
                        let v = row.values.indices.contains(i) ? row.values[i] : nil
                        let prev = i > 0 && row.values.indices.contains(i - 1) ? row.values[i - 1] : nil
                        HStack {
                            Text(Fmt.dayYear(day))
                            Spacer()
                            if let v, let prev {
                                let d = v - prev
                                Text("\(d >= 0 ? "▲" : "▼") \(String(format: "%.1f", abs(d)))")
                                    .font(.caption.monospacedDigit()).foregroundStyle(d >= 0 ? Color.coilUp : Color.coilDown)
                            }
                            Text(v.map { Fmt.pct($0).replacingOccurrences(of: "%", with: unit) } ?? "—")
                                .font(.body.monospacedDigit()).frame(minWidth: 72, alignment: .trailing)
                        }
                    }
                }
            }
            .navigationTitle(row.label)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}
