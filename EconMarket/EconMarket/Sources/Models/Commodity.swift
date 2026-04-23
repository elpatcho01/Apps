import Foundation
import GRDB

struct Commodity: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    var symbol: String
    var name: String
    var category: Category
    var unit: String

    enum Category: String, Codable {
        case energy
        case metals
        case agriculture
        case livestock
    }

    static let databaseTableName = "commodities"

    static let prices = hasMany(CommodityPrice.self)
}

struct CommodityPrice: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    var commodityId: Int64
    var date: Date
    var open: Double?
    var high: Double?
    var low: Double?
    var close: Double
    var volume: Double?
    var fetchedAt: Date

    static let databaseTableName = "commodity_prices"

    static let commodity = belongsTo(Commodity.self)
}
