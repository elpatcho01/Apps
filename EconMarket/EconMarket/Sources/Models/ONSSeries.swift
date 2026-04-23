import Foundation
import GRDB

// Catalog row — one entry per ONS time series we track.
// Series IDs and dataset IDs can be verified at: https://www.ons.gov.uk/timeseries/{seriesId}
struct ONSSeries: Codable, FetchableRecord, MutablePersistableRecord {
    var id: Int64?
    var seriesId: String     // e.g. "D7G7"
    var datasetId: String    // e.g. "MM23"
    var title: String
    var unit: String
    var category: Category
    var frequency: Frequency
    var lastFetchedAt: Date? // nil = never fetched

    static let databaseTableName = "ons_series"

    mutating func didInsert(_ inserted: InsertionSuccess) {
        id = inserted.rowID
    }

    enum Category: String, Codable, CaseIterable {
        case gdp        = "gdp"
        case inflation  = "inflation"
        case employment = "employment"
        case trade      = "trade"
        case retail     = "retail"
        case production = "production"
        case housing    = "housing"

        var displayName: String {
            switch self {
            case .gdp:        return "GDP"
            case .inflation:  return "Inflation"
            case .employment: return "Employment"
            case .trade:      return "Trade"
            case .retail:     return "Retail"
            case .production: return "Production"
            case .housing:    return "Housing"
            }
        }
    }

    enum Frequency: String, Codable {
        case monthly   = "monthly"
        case quarterly = "quarterly"
        case annual    = "annual"
    }
}

// One value observation for a given series and date.
struct ONSDataPoint: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    var seriesId: String    // matches ONSSeries.seriesId
    var date: Date          // normalised to first day of period
    var value: Double
    var fetchedAt: Date

    static let databaseTableName = "ons_data_points"
}
