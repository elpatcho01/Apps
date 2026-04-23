import Foundation

// Data source: Finnhub economic calendar (https://finnhub.io/docs/api/economic-calendar)
// Free tier: 60 calls/minute, no hard historical limit.
// Get a free key at https://finnhub.io
//
// The calendar returns each PMI release with:
//   actual   – the published index value
//   estimate – the economist consensus forecast
//   prev     – the prior month's reading
//   event    – e.g. "S&P Global/CIPS UK Services PMI Flash"
//   time     – the release datetime (UTC)
//   country  – "GB"
//
// Reference-month derivation:
//   Flash releases happen ~21st–24th of the reference month itself.
//   Final releases happen ~1st–5th of the following month.
//   So: isPreliminary → date = release month; final → date = release month − 1.

final class UKPMIService {
    static let shared = UKPMIService()

    var finnhubKey: String = ""

    // How far back to seed if the database is completely empty.
    private let defaultHistoryStart: Date = {
        var c = DateComponents(); c.year = 2018; c.month = 1; c.day = 1
        return Calendar.utc.date(from: c)!
    }()

    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30
        return URLSession(configuration: cfg)
    }()

    // MARK: - Public API

    /// Fetches all PMI readings that are not yet in the database and saves them.
    /// Automatically determines the gap start per series and fetches in 6-month
    /// chunks to stay within free-tier rate limits.
    func syncMissingData() async throws {
        guard !finnhubKey.isEmpty else { throw PMIFetchError.missingApiKey }

        // Find the earliest gap start across all four series.
        let gapStart = try earliestGapStart()
        let today = Date()

        guard gapStart < today else { return }

        // Fetch in 6-month windows so each request covers a manageable range.
        var windowStart = gapStart
        while windowStart < today {
            let windowEnd = min(
                Calendar.utc.date(byAdding: .month, value: 6, to: windowStart)!,
                today
            )
            let events = try await fetchCalendar(from: windowStart, to: windowEnd)
            let readings = parseReadings(from: events)
            if !readings.isEmpty {
                try AppDatabase.shared.saveUKPMIReadings(readings)
            }
            windowStart = windowEnd
        }
    }

    // MARK: - Gap detection

    private func earliestGapStart() throws -> Date {
        // For each series use (latestDate + 1 day) as the fetch start so we
        // don't re-download data we already have.  If a series has no data at
        // all, fall back to the configured history start.
        var earliest = Date() // will be moved backwards
        for type in UKPMIReading.PMIType.allCases {
            let start: Date
            if let latest = try AppDatabase.shared.latestUKPMIDate(type: type) {
                // Start one day after the latest reference month we have.
                start = Calendar.utc.date(byAdding: .day, value: 1, to: latest)!
            } else {
                start = defaultHistoryStart
            }
            if start < earliest { earliest = start }
        }
        return earliest
    }

    // MARK: - Finnhub fetch

    private func fetchCalendar(from start: Date, to end: Date) async throws -> [FinnhubEvent] {
        let fmt = isoDateFormatter()
        var comps = URLComponents(string: "https://finnhub.io/api/v1/calendar/economic")!
        comps.queryItems = [
            .init(name: "from",  value: fmt.string(from: start)),
            .init(name: "to",    value: fmt.string(from: end)),
            .init(name: "token", value: finnhubKey),
        ]
        guard let url = comps.url else { throw PMIFetchError.badURL }

        let (data, response) = try await session.data(from: url)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw PMIFetchError.httpError((response as? HTTPURLResponse)?.statusCode ?? 0)
        }

        let body = try JSONDecoder().decode(FinnhubCalendarResponse.self, from: data)
        // Keep only UK PMI events.
        return body.economicCalendar.filter { isUKPMIEvent($0) }
    }

    // MARK: - Parsing

    private func parseReadings(from events: [FinnhubEvent]) -> [UKPMIReading] {
        let now = Date()
        var readings: [UKPMIReading] = []

        for event in events {
            guard let type = pmiType(for: event.event) else { continue }
            guard let releaseDate = parseDate(event.time) else { continue }

            let isPreliminary = isFlash(event.event)
            let referenceMonth = deriveReferenceMonth(releaseDate: releaseDate, isPreliminary: isPreliminary)

            let reading = UKPMIReading(
                id: nil,
                type: type,
                date: referenceMonth,
                actual: event.actual,
                consensus: event.estimate,
                previous: event.prev,
                isPreliminary: isPreliminary,
                fetchedAt: now
            )
            readings.append(reading)
        }
        return readings
    }

    // MARK: - Reference-month derivation

    /// Flash PMI: released mid-to-late in the reference month → date = that month.
    /// Final PMI: released early in the following month → date = previous month.
    private func deriveReferenceMonth(releaseDate: Date, isPreliminary: Bool) -> Date {
        let cal = Calendar.utc
        let components: Set<Calendar.Component> = [.year, .month]
        var dc = cal.dateComponents(components, from: releaseDate)
        if !isPreliminary {
            // Roll back one month.
            if dc.month! == 1 {
                dc.month = 12
                dc.year! -= 1
            } else {
                dc.month! -= 1
            }
        }
        dc.day = 1
        return cal.date(from: dc)!
    }

    // MARK: - Event classification helpers

    private func isUKPMIEvent(_ event: FinnhubEvent) -> Bool {
        guard event.country == "GB" else { return false }
        let name = event.event.lowercased()
        return name.contains("pmi") && (
            name.contains("services") ||
            name.contains("manufacturing") ||
            name.contains("construction") ||
            name.contains("composite")
        )
    }

    private func isFlash(_ eventName: String) -> Bool {
        let lower = eventName.lowercased()
        return lower.contains("flash") || lower.contains("preliminary")
    }

    private func pmiType(for eventName: String) -> UKPMIReading.PMIType? {
        let lower = eventName.lowercased()
        if lower.contains("composite") { return .composite }
        if lower.contains("services")  { return .services }
        if lower.contains("manufacturing") { return .manufacturing }
        if lower.contains("construction")  { return .construction }
        return nil
    }

    // MARK: - Date parsing

    /// Finnhub time field is either "YYYY-MM-DD" or full ISO-8601 with time.
    private func parseDate(_ raw: String) -> Date? {
        // Try date-only first (cheapest).
        if let d = isoDateFormatter().date(from: String(raw.prefix(10))) { return d }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return iso.date(from: raw)
    }

    private func isoDateFormatter() -> DateFormatter {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }
}

// MARK: - Calendar extension

private extension Calendar {
    static let utc: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()
}

// MARK: - Errors

enum PMIFetchError: LocalizedError {
    case missingApiKey
    case badURL
    case httpError(Int)

    var errorDescription: String? {
        switch self {
        case .missingApiKey:     return "Finnhub API key not set."
        case .badURL:            return "Could not build Finnhub request URL."
        case .httpError(let c):  return "Finnhub HTTP error \(c)."
        }
    }
}

// MARK: - Finnhub JSON shapes

private struct FinnhubCalendarResponse: Decodable {
    let economicCalendar: [FinnhubEvent]
}

private struct FinnhubEvent: Decodable {
    let event:    String
    let country:  String
    let time:     String
    let actual:   Double?
    let estimate: Double?
    let prev:     Double?
}
