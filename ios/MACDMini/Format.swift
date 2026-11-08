import SwiftUI

/// Rupees in lakh/crore, signed percentages, and dates always shown in full IST.
enum Fmt {
    static let ist = TimeZone(identifier: "Asia/Kolkata")!

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let iso = ISO8601DateFormatter()
    private static let dayParser: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = ist
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// Parses the engine's timestamps ("2026-10-26T04:45:44.357131+00:00", "2026-11-08T09:10:06+05:30")
    /// and plain session days ("2026-11-06", read as that day in IST).
    static func date(_ raw: String?) -> Date? {
        guard var s = raw else { return nil }
        if s.count == 10 { return dayParser.date(from: s) }
        if let dot = s.firstIndex(of: ".") {                       // microseconds → milliseconds
            var end = s.index(after: dot)
            while end < s.endIndex, s[end].isNumber { end = s.index(after: end) }
            let digits = String(s[s.index(after: dot)..<end].prefix(3))
            s = String(s[..<dot]) + "." + digits.padding(toLength: 3, withPad: "0", startingAt: 0) + String(s[end...])
        }
        return isoFrac.date(from: s) ?? iso.date(from: s)
    }

    private static func formatter(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_GB")
        f.timeZone = ist
        f.dateFormat = format
        return f
    }
    private static let dateTimeF = formatter("EEE d MMM yyyy, HH:mm 'IST'")
    private static let dayF = formatter("d MMM")
    private static let dayYearF = formatter("EEE d MMM yyyy")
    private static let dayMonthYearF = formatter("d MMM yyyy")

    static func dateTime(_ d: Date?) -> String { d.map(dateTimeF.string) ?? "—" }
    static func dateTime(_ s: String?) -> String { dateTime(date(s)) }
    static func day(_ s: String?) -> String { date(s).map(dayF.string) ?? (s ?? "—") }
    static func dayMonthYear(_ s: String?) -> String { date(s).map(dayMonthYearF.string) ?? (s ?? "—") }
    static func dayYear(_ s: String?) -> String { date(s).map(dayYearF.string) ?? (s ?? "—") }

    private static let indian: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_IN")
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return f
    }()
    private static let indianPrice: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_IN")
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f
    }()

    /// ₹1.74 Cr, ₹12.30 L, ₹45,210. `signed` adds a + to gains.
    static func rupees(_ v: Double?, signed: Bool = false) -> String {
        guard let v, v.isFinite else { return "—" }
        let sign = v < 0 ? "−" : (signed && v > 0 ? "+" : "")
        let a = abs(v)
        let body: String
        if a >= 1e7 { body = String(format: "%.2f Cr", a / 1e7) }
        else if a >= 1e5 { body = String(format: "%.2f L", a / 1e5) }
        else { body = indian.string(from: NSNumber(value: a.rounded())) ?? "\(Int(a))" }
        return sign + "₹" + body
    }

    static func price(_ v: Double?) -> String {
        guard let v, v.isFinite else { return "—" }
        return indianPrice.string(from: NSNumber(value: v)) ?? String(format: "%.2f", v)
    }

    static func pct(_ v: Double?, digits: Int = 1, signed: Bool = true) -> String {
        guard let v, v.isFinite else { return "—" }
        let sign = v < 0 ? "−" : (signed && v > 0 ? "+" : "")
        return sign + String(format: "%.\(digits)f%%", abs(v))
    }

    static func symbol(_ s: String) -> String {
        s.replacingOccurrences(of: "NSE:", with: "")
            .replacingOccurrences(of: "BSE:", with: "")
            .replacingOccurrences(of: "-INDEX", with: "")
    }

    private static let acronyms: Set<String> = ["MACD", "ATR", "EOD", "CE", "PE", "ATM", "OTM", "ITM", "IB", "MP", "HMA",
                                                "KAMA", "RSI", "ROC", "OI", "VWAP", "EMA", "BTST", "CBE", "PCT", "IV"]

    /// "EXTREME_BOOK_CALL_INDEX_1.5ATR_UP_EOD" → "Extreme book call index 1.5ATR up EOD";
    /// "TRAILING_STOP_25_PCT" → "Trailing stop 25%".
    static func words(_ code: String?) -> String {
        guard let code, !code.isEmpty else { return "—" }
        var parts: [String] = []
        for p in code.split(separator: "_") {
            let s = String(p)
            if s == "PCT", let last = parts.last, Double(last) != nil { parts[parts.count - 1] = last + "%"; continue }
            parts.append(acronyms.contains(s) || s.contains(where: \.isNumber) ? s : s.lowercased())
        }
        guard let first = parts.first else { return code }
        return ([first.prefix(1).uppercased() + first.dropFirst()] + parts.dropFirst()).joined(separator: " ")
    }
}

extension Color {
    static let gain = Color(red: 0.11, green: 0.60, blue: 0.35)
    static let loss = Color(red: 0.85, green: 0.24, blue: 0.29)
    static let coilUp = Color(red: 0.30, green: 0.57, blue: 1.00)     // #4d91ff, as on the web Sector coil
    static let coilDown = Color(red: 0.95, green: 0.36, blue: 0.42)   // #f15b6c

    static func pnl(_ v: Double?) -> Color {
        guard let v else { return .secondary }
        return v > 0 ? .gain : v < 0 ? .loss : .secondary
    }
}
