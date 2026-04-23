import Foundation

// Data sources:
//   Commodities  — Alpha Vantage (free tier: 25 req/day)
//     https://www.alphavantage.co/documentation/#commodities
//   Economic indicators — FRED (Federal Reserve, free, no rate limit for basic use)
//     https://fred.stlouisfed.org/docs/api/fred/

final class DataFetchService {
    static let shared = DataFetchService()

    // Set your keys in Settings → API Keys, or via environment variables for testing.
    var alphaVantageKey: String = ""
    var fredApiKey: String = ""

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        return URLSession(configuration: config)
    }()

    // MARK: - Commodities (Alpha Vantage)

    /// Downloads daily price series for a commodity symbol and saves to the database.
    /// Alpha Vantage commodity endpoint returns monthly data for most commodities;
    /// use the `interval` parameter to request "monthly" or "daily".
    func fetchCommodityPrices(symbol: String, interval: String = "monthly") async throws {
        guard !alphaVantageKey.isEmpty else {
            throw FetchError.missingApiKey("Alpha Vantage")
        }

        let commodity = try AppDatabase.shared.dbWriter.read { db in
            try Commodity.filter(Column("symbol") == symbol).fetchOne(db)
        }
        guard let commodity, let commodityId = commodity.id else {
            throw FetchError.unknownSymbol(symbol)
        }

        // Map internal symbol to Alpha Vantage function name
        let function = alphaVantageFunction(for: symbol)
        var components = URLComponents(string: "https://www.alphavantage.co/query")!
        components.queryItems = [
            .init(name: "function",   value: function),
            .init(name: "interval",   value: interval),
            .init(name: "apikey",     value: alphaVantageKey),
            .init(name: "datatype",   value: "json"),
        ]
        guard let url = components.url else { throw FetchError.badURL }

        let (data, response) = try await session.data(from: url)
        try validateHTTP(response)

        let raw = try JSONDecoder().decode(AlphaVantageCommodityResponse.self, from: data)
        let now = Date()
        let iso = isoDateFormatter()

        let prices: [CommodityPrice] = raw.data.compactMap { entry in
            guard let date = iso.date(from: entry.date),
                  let close = Double(entry.value) else { return nil }
            return CommodityPrice(
                id: nil,
                commodityId: commodityId,
                date: date,
                open: nil, high: nil, low: nil,
                close: close,
                volume: nil,
                fetchedAt: now
            )
        }

        try AppDatabase.shared.savePrices(prices)
    }

    // MARK: - Economic Indicators (FRED)

    /// Downloads an observation series from FRED and saves to the database.
    func fetchFredSeries(code: String, limit: Int = 120) async throws {
        guard !fredApiKey.isEmpty else {
            throw FetchError.missingApiKey("FRED")
        }

        let indicator = try AppDatabase.shared.dbWriter.read { db in
            try EconomicIndicator.filter(Column("code") == code).fetchOne(db)
        }
        guard let indicator, let indicatorId = indicator.id else {
            throw FetchError.unknownSymbol(code)
        }

        var components = URLComponents(string: "https://api.stlouisfed.org/fred/series/observations")!
        components.queryItems = [
            .init(name: "series_id",    value: code),
            .init(name: "api_key",      value: fredApiKey),
            .init(name: "file_type",    value: "json"),
            .init(name: "sort_order",   value: "desc"),
            .init(name: "limit",        value: "\(limit)"),
            .init(name: "observation_end", value: "9999-12-31"),
        ]
        guard let url = components.url else { throw FetchError.badURL }

        let (data, response) = try await session.data(from: url)
        try validateHTTP(response)

        let raw = try JSONDecoder().decode(FredObservationsResponse.self, from: data)
        let now = Date()
        let iso = isoDateFormatter()

        let points: [EconomicDataPoint] = raw.observations.compactMap { obs in
            guard let date = iso.date(from: obs.date),
                  let value = Double(obs.value) else { return nil }
            return EconomicDataPoint(
                id: nil,
                indicatorId: indicatorId,
                date: date,
                value: value,
                fetchedAt: now
            )
        }

        try AppDatabase.shared.saveDataPoints(points)
    }

    // MARK: - Batch refresh

    func refreshAllCommodities() async {
        let symbols = (try? AppDatabase.shared.allCommodities().map(\.symbol)) ?? []
        await withTaskGroup(of: Void.self) { group in
            for symbol in symbols {
                group.addTask { [weak self] in
                    try? await self?.fetchCommodityPrices(symbol: symbol)
                }
            }
        }
    }

    func refreshAllIndicators() async {
        let codes = (try? AppDatabase.shared.allIndicators().map(\.code)) ?? []
        await withTaskGroup(of: Void.self) { group in
            for code in codes {
                group.addTask { [weak self] in
                    try? await self?.fetchFredSeries(code: code)
                }
            }
        }
    }

    // MARK: - Helpers

    private func alphaVantageFunction(for symbol: String) -> String {
        switch symbol {
        case "GOLD":   return "COPPER"   // Alpha Vantage uses GOLD, SILVER, etc.
        case "SILVER": return "SILVER"
        case "CL":     return "WTI"
        case "BRENT":  return "BRENT"
        case "NG":     return "NATURAL_GAS"
        case "ZC":     return "CORN"
        case "ZW":     return "WHEAT"
        case "ZS":     return "SOYBEAN"
        case "CC":     return "COCOA"
        case "KC":     return "COFFEE"
        case "CT":     return "COTTON"
        case "SB":     return "SUGAR"
        default:       return symbol
        }
    }

    private func isoDateFormatter() -> DateFormatter {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }

    private func validateHTTP(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            throw FetchError.httpError(http.statusCode)
        }
    }
}

// MARK: - Error

enum FetchError: LocalizedError {
    case missingApiKey(String)
    case unknownSymbol(String)
    case badURL
    case httpError(Int)

    var errorDescription: String? {
        switch self {
        case .missingApiKey(let provider): return "\(provider) API key not set."
        case .unknownSymbol(let s):        return "Symbol '\(s)' not found in database."
        case .badURL:                      return "Could not build request URL."
        case .httpError(let code):         return "HTTP error \(code)."
        }
    }
}

// MARK: - JSON response shapes

private struct AlphaVantageCommodityResponse: Decodable {
    struct Entry: Decodable {
        let date: String
        let value: String
    }
    let data: [Entry]
}

private struct FredObservationsResponse: Decodable {
    struct Observation: Decodable {
        let date: String
        let value: String
    }
    let observations: [Observation]
}
