import Foundation
import GRDB

struct EconomicIndicator: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    var code: String
    var name: String
    var category: Category
    var unit: String
    var frequency: Frequency
    var source: String

    enum Category: String, Codable {
        case gdp
        case inflation
        case employment
        case trade
        case monetary
        case housing
    }

    enum Frequency: String, Codable {
        case daily
        case weekly
        case monthly
        case quarterly
        case annual
    }

    static let databaseTableName = "economic_indicators"

    static let dataPoints = hasMany(EconomicDataPoint.self, using: ForeignKey(["indicatorId"]))
}

struct EconomicDataPoint: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    var indicatorId: Int64
    var date: Date
    var value: Double
    var fetchedAt: Date

    static let databaseTableName = "economic_data_points"

    static let indicator = belongsTo(EconomicIndicator.self, using: ForeignKey(["indicatorId"]))
}
