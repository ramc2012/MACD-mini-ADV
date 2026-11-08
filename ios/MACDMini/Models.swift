// Shapes of the engine's JSON (engine/src/macd_trader/app.py), decoded with snake_case → camelCase.
// Every field the engine may omit or send as null is optional, so a new or missing field never breaks a screen.
import Foundation

struct Health: Decodable {
    struct Calendar: Decodable {
        let tradingDay: Bool?
        let holiday: String?
        let nextTradingDay: String?
    }
    let status: String?
    let error: String?
    let uptimeSeconds: Double?
    let tickRatePerSecond: Double?
    let lastTickAgeSeconds: Double?
    let signalsEmitted: Int?
    let symbols: Int?
    let sessionOpen: Bool?
    let feedAlive: Bool?
    let tokenExpired: Bool?
    let socketAuthFailed: Bool?
    let marketCalendar: Calendar?
    let observedAt: String?
}

struct Scoreboard: Decodable {
    let asOf: String?
    let lanes: [LaneScore]
}

struct LaneScore: Decodable, Identifiable, Hashable {
    static func == (a: LaneScore, b: LaneScore) -> Bool { a.lane == b.lane }
    func hash(into h: inout Hasher) { h.combine(lane) }

    let lane: String
    let label: String
    let capital: Double?
    let equity: Double?
    let cash: Double?
    let returnPct: Double?
    let realized: Double?
    let unrealized: Double?
    let openPositions: Int?
    let maxDrawdown: Double?
    let maxDrawdownPct: Double?
    let closed: ClosedStats?
    let weekly: [WeekNet]?
    let bestWeek: WeekNet?
    let netWithoutBestWeek: Double?
    let exits: [ExitStat]?
    let curve: [CurvePoint]?
    let since: String?
    let note: String?
    let slots: Int?
    let bookFull: Bool?

    var id: String { lane }
}

struct ClosedStats: Decodable {
    let trades: Int?
    let wins: Int?
    let winRate: Double?
    let net: Double?
    let profitFactor: Double?
    let avgWin: Double?
    let avgLoss: Double?
}

struct WeekNet: Decodable, Identifiable {
    let week: String
    let net: Double
    var id: String { week }
    var date: Date { Fmt.date(week) ?? .distantPast }
}

struct ExitStat: Decodable, Identifiable {
    let reason: String
    let hold: String?
    let trades: Int?
    let wins: Int?
    let winRate: Double?
    let net: Double?
    let profitFactor: Double?
    var id: String { reason + "|" + (hold ?? "") }
}

struct CurvePoint: Decodable, Identifiable {
    let date: Date
    let equity: Double
    var id: Date { date }

    private enum CodingKeys: String, CodingKey { case at, equity }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = Fmt.date(try c.decode(String.self, forKey: .at)) ?? .distantPast
        equity = try c.decode(Double.self, forKey: .equity)
    }
}

struct Portfolio: Decodable {
    let initialCapital: Double?
    let cash: Double?
    let marketValue: Double?
    let equity: Double?
    let realizedPnl: Double?
    let unrealizedPnl: Double?
    let positions: [Position]
}

struct Position: Decodable, Identifiable {
    let symbol: String
    let quantity: Double
    let averagePrice: Double
    let lastPrice: Double?
    let openedAt: String?
    let hardStop: Double?
    let trailingStop: Double?
    let lots: Double?
    let unrealizedPnl: Double?
    let returnPct: Double?
    let positionId: String?
    var id: String { positionId ?? symbol }
}

struct Signal: Decodable, Identifiable {
    let symbol: String
    let side: String
    let kind: String?
    let price: Double?
    let timestamp: String
    let signalId: String?
    let sizeFraction: Double?
    var id: String { signalId ?? symbol + timestamp }
}

struct SectorCoil: Decodable {
    let asOf: String
    let live: Bool
    let base: String
    let baseDay: String?
    let step: String
    let measure: String
    let benchmark: String
    let points: [String]
    let rows: [CoilRow]
    let missing: [String]
}

struct CoilRow: Decodable, Identifiable {
    let symbol: String
    let label: String
    let kind: String
    let values: [Double?]
    let price: Double?
    var id: String { symbol }
    /// Nulls (a missing close) take the previous value so the coil never breaks.
    var filled: [Double] {
        var last = 0.0
        return values.map { v in if let v { last = v }; return last }
    }
    var latest: Double { filled.last ?? 0 }
}
