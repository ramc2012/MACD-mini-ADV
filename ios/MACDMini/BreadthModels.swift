// Market breadth JSON (engine app.py): /api/day-highs, /api/sector-wheel, /api/heatmap/atm, /api/rrg.
// Dictionary keys such as "SPOT", "1D", "1H" have no underscore, so snake_case decoding leaves them alone.
import Foundation

struct DayHighs: Decodable {
    let session: String?
    let timeframeSeconds: Int?
    let generatedAt: String?
    let evaluated: Int?
    let atHigh: [String: Int]?
    let atLow: [String: Int]?
    let listed: [String: Int]?
    let rows: [DayHighRow]
}

struct DayHighRow: Decodable, Identifiable {
    let symbol: String
    let kind: String              // SPOT, CE, PE
    let label: String?
    let group: String?
    let index: Bool?
    let stale: Bool?
    let close: Double?
    let dayHigh: Double?
    let dayLow: Double?
    let atHigh: Bool?
    let atLow: Bool?
    let highStreak: Int?
    let lowStreak: Int?
    let fromHighPct: Double?
    let fromLowPct: Double?
    let changePct: Double?
    var id: String { kind + "|" + symbol }
    var name: String { label ?? Fmt.symbol(symbol) }
}

struct SectorWheel: Decodable {
    struct MonthStart: Decodable {
        let formedOn: String?
        let sessionsSince: Int?
        let typicalRun: Int?
        let leaders: [String]?
        let laggards: [String]?
    }
    let asOf: String?
    let live: Bool?
    let windows: [String]
    let benchmark: String?
    let benchmarkChange: [String: Double?]?
    let monthStart: MonthStart?
    let rows: [WheelRow]
}

struct WheelRow: Decodable, Identifiable {
    struct Rank: Decodable { let rank: Int?; let of: Int?; let relative: Double? }
    let symbol: String
    let label: String?
    let kind: String              // benchmark, sector, broad, stock
    let group: String?
    let price: Double?
    let stale: Bool?
    let own: [String: Double?]?
    let relative: [String: Double?]?
    let monthStart: Rank?
    var id: String { symbol }
    var name: String { label ?? Fmt.symbol(symbol) }
    func value(_ window: String, relative rel: Bool) -> Double? { ((rel ? relative : own)?[window]) ?? nil }
}

struct AtmHeatmap: Decodable {
    let asOf: String?
    let periods: [String]
    let basis: String?
    let references: [String: String]?
    let rows: [AtmRow]
}

struct AtmRow: Decodable, Identifiable {
    struct Leg: Decodable {
        let symbol: String?
        let strike: Double?
        let price: Double?
        let change: [String: Double?]?
        let partial: [String: String]?
    }
    let kind: String              // index, sector, stock
    let underlying: String
    let spotSymbol: String?
    let strike: Double?
    let expiry: String?
    let ce: Leg?
    let pe: Leg?
    let spot: Double?
    let spotChange: [String: Double?]?
    var id: String { kind + "|" + underlying }
}

struct RRGPoint: Decodable, Hashable { let x: Double; let y: Double }

struct RRG: Decodable {
    let benchmark: String?
    let timeframeSeconds: Int?
    let window: Int?
    let tail: Int?
    let generatedAt: String?
    let sectors: [RRGSector]
    let symbols: [RRGSymbol]
}

struct RRGSector: Decodable, Identifiable {
    let sector: String
    let members: Int?
    let tail: [RRGPoint]?
    let x: Double
    let y: Double
    let quadrant: String?
    var id: String { sector }
}

struct RRGSymbol: Decodable, Identifiable {
    let symbol: String
    let sector: String?
    let x: Double
    let y: Double
    let quadrant: String?
    var id: String { symbol }
}
