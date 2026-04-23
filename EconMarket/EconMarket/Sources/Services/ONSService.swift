import Foundation

// ONS Timeseries API — no API key required, no registration needed.
// Docs: https://developer.ons.gov.uk
// Each call returns the complete history for one series in a single response.
//
// Endpoint: GET https://api.ons.gov.uk/v1/dataset/{datasetId}/timeseries/{seriesId}/data
//
// Response shape:
//   {
//     "description": { "title": "...", "unit": "..." },
//     "months":   [ { "date": "2024 JAN", "value": "123.4" }, ... ],
//     "quarters": [ { "date": "2024 Q1",  "value": "456.7" }, ... ],
//     "years":    [ { "date": "2023",      "value": "789.0" }, ... ]
//   }
//
// "value" may be "N/A" — those observations are silently skipped.

final class ONSService {
    static let shared = ONSService()

    private let baseURL = "https://api.ons.gov.uk/v1"

    // Re-fetch a series if it hasn't been updated in this interval.
    private let stalenessInterval: TimeInterval = 6 * 60 * 60  // 6 hours

    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 30
        cfg.httpAdditionalHeaders = ["User-Agent": "EconMarket iOS App"]
        return URLSession(configuration: cfg)
    }()

    // MARK: - Public

    /// Syncs all registered ONS series, skipping any fetched within the last 6 hours.
    /// Runs up to 4 fetches concurrently to respect the ONS rate limit of 120 req/10 s.
    func syncAllSeries() async throws {
        let allSeries = try AppDatabase.shared.allONSSeries()

        try await withThrowingTaskGroup(of: Void.self) { group in
            var inFlight = 0
            for series in allSeries {
                if inFlight >= 4 {
                    try await group.next()
                    inFlight -= 1
                }
                group.addTask { try await self.syncSeries(series) }
                inFlight += 1
            }
            try await group.waitForAll()
        }
    }

    // MARK: - Per-series sync

    private func syncSeries(_ series: ONSSeries) async throws {
        if let last = series.lastFetchedAt,
           Date().timeIntervalSince(last) < stalenessInterval {
            return
        }

        let urlString = "\(baseURL)/dataset/\(series.datasetId)/timeseries/\(series.seriesId)/data"
        guard let url = URL(string: urlString) else { throw ONSError.badURL(urlString) }

        let (data, response) = try await session.data(from: url)

        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw ONSError.httpError(http.statusCode, series.seriesId)
        }

        let raw = try JSONDecoder().decode(ONSTimeseriesResponse.self, from: data)
        let points = parseDataPoints(from: raw, seriesId: series.seriesId)

        if !points.isEmpty {
            try AppDatabase.shared.saveONSDataPoints(points)
        }
        try AppDatabase.shared.markONSSeriesFetched(seriesId: series.seriesId, datasetId: series.datasetId)
    }

    // MARK: - Parsing

    private func parseDataPoints(from response: ONSTimeseriesResponse, seriesId: String) -> [ONSDataPoint] {
        let now = Date()
        var points: [ONSDataPoint] = []

        for entry in response.months ?? [] {
            if let date = parseMonthDate(entry.date), let value = parseValue(entry.value) {
                points.append(ONSDataPoint(id: nil, seriesId: seriesId, date: date, value: value, fetchedAt: now))
            }
        }

        for entry in response.quarters ?? [] {
            if let date = parseQuarterDate(entry.date), let value = parseValue(entry.value) {
                points.append(ONSDataPoint(id: nil, seriesId: seriesId, date: date, value: value, fetchedAt: now))
            }
        }

        for entry in response.years ?? [] {
            if let date = parseYearDate(entry.date), let value = parseValue(entry.value) {
                points.append(ONSDataPoint(id: nil, seriesId: seriesId, date: date, value: value, fetchedAt: now))
            }
        }

        return points
    }

    // MARK: - Date parsing

    // ONS monthly format: "2024 JAN"
    private func parseMonthDate(_ raw: String) -> Date? {
        let parts = raw.split(separator: " ")
        guard parts.count == 2,
              let year = Int(parts[0]),
              let month = monthNumber(String(parts[1])) else { return nil }
        return firstOfMonth(year: year, month: month)
    }

    // ONS quarterly format: "2024 Q1"
    private func parseQuarterDate(_ raw: String) -> Date? {
        let parts = raw.split(separator: " ")
        guard parts.count == 2,
              let year = Int(parts[0]) else { return nil }
        let quarter = String(parts[1])
        let month: Int
        switch quarter {
        case "Q1": month = 1
        case "Q2": month = 4
        case "Q3": month = 7
        case "Q4": month = 10
        default:   return nil
        }
        return firstOfMonth(year: year, month: month)
    }

    // ONS annual format: "2023"
    private func parseYearDate(_ raw: String) -> Date? {
        guard let year = Int(raw.trimmingCharacters(in: .whitespaces)) else { return nil }
        return firstOfMonth(year: year, month: 1)
    }

    private func firstOfMonth(year: Int, month: Int) -> Date? {
        var dc = DateComponents()
        dc.year = year; dc.month = month; dc.day = 1
        dc.timeZone = TimeZone(identifier: "UTC")
        return Calendar.ons.date(from: dc)
    }

    private func monthNumber(_ abbr: String) -> Int? {
        switch abbr.uppercased() {
        case "JAN": return 1;  case "FEB": return 2;  case "MAR": return 3
        case "APR": return 4;  case "MAY": return 5;  case "JUN": return 6
        case "JUL": return 7;  case "AUG": return 8;  case "SEP": return 9
        case "OCT": return 10; case "NOV": return 11; case "DEC": return 12
        default:    return nil
        }
    }

    // ONS uses "N/A" or empty string when data is not available.
    private func parseValue(_ raw: String) -> Double? {
        let trimmed = raw.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != "N/A" else { return nil }
        return Double(trimmed)
    }
}

// MARK: - Calendar helper

private extension Calendar {
    static let ons: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()
}

// MARK: - Errors

enum ONSError: LocalizedError {
    case badURL(String)
    case httpError(Int, String)

    var errorDescription: String? {
        switch self {
        case .badURL(let u):           return "Could not build URL: \(u)"
        case .httpError(let c, let s): return "ONS returned HTTP \(c) for series \(s)."
        }
    }
}

// MARK: - JSON shapes

private struct ONSTimeseriesResponse: Decodable {
    struct Entry: Decodable {
        let date:  String
        let value: String
    }
    let months:   [Entry]?
    let quarters: [Entry]?
    let years:    [Entry]?
}
