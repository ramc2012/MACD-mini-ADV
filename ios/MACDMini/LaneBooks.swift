// Every lane's own book (open positions and recent exits), read from whichever endpoint the web app uses for it,
// normalised to one shape. Read-only: no close or order calls.
import Foundation

struct BookPosition: Identifiable {
    let id: String
    let symbol: String
    let quantity: Double          // negative = short (Spot MACD futures)
    let entry: Double?
    let last: Double?
    let openedAt: String?
    let stop: Double?
    let stopLabel: String
    let lots: Double?
    let pnl: Double?
    let returnPct: Double?
    var note: String? = nil
}

struct BookExit: Identifiable {
    let id: String
    let symbol: String
    let side: String?
    let quantity: Double?
    let entry: Double?
    let exit: Double?
    let entryTime: String?
    let exitTime: String?
    let pnl: Double?
    let returnPct: Double?
    let reason: String?
}

struct LaneBook {
    let cash: Double?
    let equity: Double?
    let realized: Double?
    let unrealized: Double?
    let positions: [BookPosition]
    /// Newest first.
    let exits: [BookExit]
    var exitsNote: String? = nil
}

// MARK: - Wire shapes

/// The MACD/smart/blast/agentic/MP portfolio shape (execution.portfolio_snapshot()).
private struct WirePortfolio: Decodable {
    let cash: Double?
    let equity: Double?
    let realizedPnl: Double?
    let unrealizedPnl: Double?
    let positions: [Position]
    let closedPositions: [WireClosed]?
}

private struct WireClosed: Decodable {
    let positionId: String?
    let closedId: String?
    let symbol: String
    let side: String?
    let quantity: Double?
    let entryTime: String?
    let entryPrice: Double?
    let exitTime: String?
    let exitPrice: Double?
    let realizedPnl: Double?
    let returnPct: Double?
    let exitReason: String?
}

/// Lane endpoints that wrap the portfolio: /api/lanes/{name}/snapshot, /api/blast/snapshot, /api/agentic, /api/mp/book.
private struct WireEnvelope: Decodable {
    let portfolio: WirePortfolio
    let closedPositions: [WireClosed]?
    let closed: [WireClosed]?
}

/// Vanguard/CBE paper lanes (/api/paper-lanes/{lane}): prices in rupees, fees and P&L in paise.
private struct WirePaperLane: Decodable {
    let cash: Double?
    let equity: Double?
    let realizedPnl: Double?
    let unrealizedPnl: Double?
    let positions: [WirePaperRow]
    let closed: [WirePaperRow]?
}

private struct WirePaperRow: Decodable {
    let id: String
    let symbol: String
    let quantity: Double?
    let entry: Double?
    let lastBid: Double?
    let openedAt: String?
    let stop: Double?
    let exit: Double?
    let closedAt: String?
    let exitReason: String?
    let pnlPaise: Double?
    let entryFee: Double?
    let exitFee: Double?
    let expiry: String?
    let payload: String?

    /// The contract traded: Vanguard's payload names it ("NSE:LTF26NOV265CE"); CBE trades the stock itself.
    var contract: (symbol: String, side: String?) {
        guard let data = payload?.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return (symbol, nil) }
        if let canonical = obj["canonical"] as? String, !canonical.isEmpty { return (canonical, nil) }
        return (symbol, obj["direction"] as? String)
    }
}

// MARK: - Loading

enum LaneBooks {
    /// How often a lane's book is re-read while its page is open (the paper lanes' reply is ~1–2 MB).
    static func refreshSeconds(_ lane: String) -> Double { ["vanguard", "cbe", "mp", "blast"].contains(lane) ? 60 : 30 }

    static func load(_ lane: String, api: EngineAPI) async throws -> LaneBook {
        switch lane {
        case "macd":
            return book(try await api.get("api/portfolio") as WirePortfolio, extra: nil)
        case "blast":
            let e: WireEnvelope = try await api.get("api/blast/snapshot")
            return book(e.portfolio, extra: e.closedPositions)
        case "agentic":
            let e: WireEnvelope = try await api.get("api/agentic")
            return book(e.portfolio, extra: e.closed)
        case "mp":
            let e: WireEnvelope = try await api.get("api/mp/book")
            return book(e.portfolio, extra: e.closedPositions)
        case "vanguard", "cbe":
            return paper(try await api.get("api/paper-lanes/\(lane)"))
        case "smart_1l", "smart_50l", "spot_macd":
            let e: WireEnvelope = try await api.get("api/lanes/\(lane)/snapshot")
            return book(e.portfolio, extra: nil)
        default:
            if lane.hasPrefix("smart") {
                let e: WireEnvelope = try await api.get("api/lanes/\(lane)/snapshot")
                return book(e.portfolio, extra: nil)
            }
            throw APIError("This app doesn't know where the \(lane) lane keeps its book yet.")
        }
    }

    private static func book(_ p: WirePortfolio, extra: [WireClosed]?) -> LaneBook {
        let positions = p.positions.enumerated().map { i, x in
            BookPosition(id: "\(i)|\(x.positionId ?? x.symbol)", symbol: x.symbol, quantity: x.quantity, entry: x.averagePrice,
                         last: x.lastPrice, openedAt: x.openedAt, stop: x.trailingStop ?? x.hardStop,
                         stopLabel: x.trailingStop != nil ? "trail" : "stop", lots: x.lots, pnl: x.unrealizedPnl,
                         returnPct: x.returnPct)
        }
        // The desk keeps closed slices until 08:00 IST the next morning; a lane may also list older ones separately.
        var seen = Set<String>()
        var exits: [BookExit] = []
        for (i, c) in ((p.closedPositions ?? []) + (extra ?? [])).enumerated() {
            let key = c.closedId ?? "\(c.positionId ?? c.symbol)|\(c.exitTime ?? "")|\(c.quantity ?? 0)"
            guard seen.insert(key).inserted else { continue }
            exits.append(BookExit(id: "\(i)|\(key)", symbol: c.symbol, side: c.side, quantity: c.quantity, entry: c.entryPrice,
                                  exit: c.exitPrice, entryTime: c.entryTime, exitTime: c.exitTime, pnl: c.realizedPnl,
                                  returnPct: c.returnPct, reason: c.exitReason))
        }
        return LaneBook(cash: p.cash, equity: p.equity, realized: p.realizedPnl, unrealized: p.unrealizedPnl,
                        positions: positions, exits: newestFirst(exits),
                        exitsNote: extra == nil ? "Exits stay on the desk until 08:00 IST the next morning." : nil)
    }

    private static func paper(_ l: WirePaperLane) -> LaneBook {
        let positions = l.positions.enumerated().map { i, r -> BookPosition in
            let c = r.contract
            let qty = r.quantity ?? 0
            let fees = (r.entryFee ?? 0) / 100
            let pnl = r.entry.flatMap { e in r.lastBid.map { ($0 - e) * qty - fees } }
            let ret = r.entry.flatMap { e in r.lastBid.map { e > 0 ? ($0 - e) / e * 100 : 0 } }
            return BookPosition(id: "\(i)|\(r.id)", symbol: c.symbol, quantity: qty, entry: r.entry, last: r.lastBid,
                                openedAt: r.openedAt, stop: r.stop, stopLabel: "stop", lots: nil, pnl: pnl, returnPct: ret,
                                note: c.side.map { $0.capitalized })
        }
        let exits = (l.closed ?? []).enumerated().map { i, r -> BookExit in
            let c = r.contract
            let ret = r.entry.flatMap { e in r.exit.map { e > 0 ? ($0 - e) / e * 100 : 0 } }
            return BookExit(id: "\(i)|\(r.id)", symbol: c.symbol, side: c.side, quantity: r.quantity, entry: r.entry, exit: r.exit,
                            entryTime: r.openedAt, exitTime: r.closedAt, pnl: r.pnlPaise.map { $0 / 100 }, returnPct: ret,
                            reason: r.exitReason)
        }
        return LaneBook(cash: l.cash, equity: l.equity, realized: l.realizedPnl, unrealized: l.unrealizedPnl,
                        positions: positions, exits: newestFirst(exits),
                        exitsNote: "Open P&L is marked at the last bid, after the entry fee.")
    }

    private static func newestFirst(_ exits: [BookExit]) -> [BookExit] {
        exits.sorted { (Fmt.date($0.exitTime) ?? .distantPast) > (Fmt.date($1.exitTime) ?? .distantPast) }
    }
}
