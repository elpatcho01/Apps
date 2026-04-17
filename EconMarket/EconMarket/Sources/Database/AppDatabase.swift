import Foundation
import GRDB

final class AppDatabase {
    static let shared = makeShared()

    let dbWriter: any DatabaseWriter

    private init(_ dbWriter: any DatabaseWriter) throws {
        self.dbWriter = dbWriter
        try migrator.migrate(dbWriter)
    }

    private var migrator: DatabaseMigrator {
        var m = DatabaseMigrator()

        // Wipe DB on schema changes during development
        #if DEBUG
        m.eraseDatabaseOnSchemaChange = true
        #endif

        m.registerMigration("v1_initial") { db in
            try db.create(table: "commodities") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("symbol", .text).notNull().unique()
                t.column("name", .text).notNull()
                t.column("category", .text).notNull()
                t.column("unit", .text).notNull()
            }

            try db.create(table: "commodity_prices") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("commodityId", .integer).notNull()
                    .indexed()
                    .references("commodities", onDelete: .cascade)
                t.column("date", .datetime).notNull()
                t.column("open", .double)
                t.column("high", .double)
                t.column("low", .double)
                t.column("close", .double).notNull()
                t.column("volume", .double)
                t.column("fetchedAt", .datetime).notNull()
                t.uniqueKey(["commodityId", "date"])
            }

            try db.create(table: "economic_indicators") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("code", .text).notNull().unique()
                t.column("name", .text).notNull()
                t.column("category", .text).notNull()
                t.column("unit", .text).notNull()
                t.column("frequency", .text).notNull()
                t.column("source", .text).notNull()
            }

            try db.create(table: "economic_data_points") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("indicatorId", .integer).notNull()
                    .indexed()
                    .references("economic_indicators", onDelete: .cascade)
                t.column("date", .datetime).notNull()
                t.column("value", .double).notNull()
                t.column("fetchedAt", .datetime).notNull()
                t.uniqueKey(["indicatorId", "date"])
            }
        }

        m.registerMigration("v1_seed_commodities") { db in
            let commodities: [(String, String, String, String)] = [
                ("GOLD",  "Gold",          "metals",      "Troy Ounce"),
                ("SILVER","Silver",         "metals",      "Troy Ounce"),
                ("PLAT",  "Platinum",       "metals",      "Troy Ounce"),
                ("PALL",  "Palladium",      "metals",      "Troy Ounce"),
                ("CL",    "Crude Oil (WTI)","energy",      "Barrel"),
                ("NG",    "Natural Gas",    "energy",      "MMBtu"),
                ("BRENT", "Brent Crude",    "energy",      "Barrel"),
                ("HO",    "Heating Oil",    "energy",      "Gallon"),
                ("ZC",    "Corn",           "agriculture", "Bushel"),
                ("ZW",    "Wheat",          "agriculture", "Bushel"),
                ("ZS",    "Soybeans",       "agriculture", "Bushel"),
                ("CC",    "Cocoa",          "agriculture", "Metric Ton"),
                ("KC",    "Coffee",         "agriculture", "Pound"),
                ("CT",    "Cotton",         "agriculture", "Pound"),
                ("SB",    "Sugar",          "agriculture", "Pound"),
                ("LE",    "Live Cattle",    "livestock",   "Pound"),
                ("GF",    "Feeder Cattle",  "livestock",   "Pound"),
                ("HE",    "Lean Hogs",      "livestock",   "Pound"),
            ]
            for (symbol, name, category, unit) in commodities {
                try db.execute(
                    sql: """
                        INSERT OR IGNORE INTO commodities (symbol, name, category, unit)
                        VALUES (?, ?, ?, ?)
                    """,
                    arguments: [symbol, name, category, unit]
                )
            }
        }

        m.registerMigration("v2_uk_pmi") { db in
            try db.create(table: "uk_pmi_readings") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("type", .text).notNull()
                t.column("date", .date).notNull()
                t.column("actual", .double)
                t.column("consensus", .double)
                t.column("previous", .double)
                t.column("isPreliminary", .boolean).notNull().defaults(to: false)
                t.column("fetchedAt", .datetime).notNull()
                // One row per (series, reference-month, flash/final)
                t.uniqueKey(["type", "date", "isPreliminary"])
            }
        }

        m.registerMigration("v1_seed_indicators") { db in
            let indicators: [(String, String, String, String, String, String)] = [
                ("GDP",         "Gross Domestic Product",          "gdp",        "Billions USD", "quarterly", "FRED"),
                ("GDPC1",       "Real GDP",                        "gdp",        "Billions 2017 USD","quarterly","FRED"),
                ("CPIAUCSL",    "CPI All Urban Consumers",         "inflation",  "Index",        "monthly",   "FRED"),
                ("CPILFESL",    "Core CPI (ex Food & Energy)",     "inflation",  "Index",        "monthly",   "FRED"),
                ("PCEPI",       "PCE Price Index",                 "inflation",  "Index",        "monthly",   "FRED"),
                ("UNRATE",      "Unemployment Rate",               "employment", "Percent",      "monthly",   "FRED"),
                ("PAYEMS",      "Nonfarm Payroll Employment",      "employment", "Thousands",    "monthly",   "FRED"),
                ("ICSA",        "Initial Jobless Claims",          "employment", "Number",       "weekly",    "FRED"),
                ("FEDFUNDS",    "Federal Funds Rate",              "monetary",   "Percent",      "monthly",   "FRED"),
                ("DGS10",       "10-Year Treasury Yield",          "monetary",   "Percent",      "daily",     "FRED"),
                ("DGS2",        "2-Year Treasury Yield",           "monetary",   "Percent",      "daily",     "FRED"),
                ("M2SL",        "M2 Money Supply",                 "monetary",   "Billions USD", "monthly",   "FRED"),
                ("BOPGSTB",     "Trade Balance",                   "trade",      "Millions USD", "monthly",   "FRED"),
                ("HOUST",       "Housing Starts",                  "housing",    "Thousands",    "monthly",   "FRED"),
                ("CSUSHPINSA",  "Case-Shiller Home Price Index",   "housing",    "Index",        "monthly",   "FRED"),
            ]
            for (code, name, category, unit, frequency, source) in indicators {
                try db.execute(
                    sql: """
                        INSERT OR IGNORE INTO economic_indicators
                        (code, name, category, unit, frequency, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    arguments: [code, name, category, unit, frequency, source]
                )
            }
        }

        return m
    }

    private static func makeShared() -> AppDatabase {
        do {
            let url = try FileManager.default
                .url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("EconMarket.sqlite")
            let pool = try DatabasePool(path: url.path)
            return try AppDatabase(pool)
        } catch {
            fatalError("Database setup failed: \(error)")
        }
    }

    // MARK: - Queries

    func allCommodities() throws -> [Commodity] {
        try dbWriter.read { db in
            try Commodity.order(Column("category"), Column("name")).fetchAll(db)
        }
    }

    func latestPrice(for commodityId: Int64) throws -> CommodityPrice? {
        try dbWriter.read { db in
            try CommodityPrice
                .filter(Column("commodityId") == commodityId)
                .order(Column("date").desc)
                .fetchOne(db)
        }
    }

    func prices(for commodityId: Int64, limit: Int = 365) throws -> [CommodityPrice] {
        try dbWriter.read { db in
            try CommodityPrice
                .filter(Column("commodityId") == commodityId)
                .order(Column("date").desc)
                .limit(limit)
                .fetchAll(db)
        }
    }

    func allIndicators() throws -> [EconomicIndicator] {
        try dbWriter.read { db in
            try EconomicIndicator.order(Column("category"), Column("name")).fetchAll(db)
        }
    }

    func dataPoints(for indicatorId: Int64, limit: Int = 60) throws -> [EconomicDataPoint] {
        try dbWriter.read { db in
            try EconomicDataPoint
                .filter(Column("indicatorId") == indicatorId)
                .order(Column("date").desc)
                .limit(limit)
                .fetchAll(db)
        }
    }

    func savePrices(_ prices: [CommodityPrice]) throws {
        try dbWriter.write { db in
            for var price in prices {
                try price.upsert(db)
            }
        }
    }

    func saveDataPoints(_ points: [EconomicDataPoint]) throws {
        try dbWriter.write { db in
            for var point in points {
                try point.upsert(db)
            }
        }
    }

    // MARK: - UK PMI

    /// All readings for one series, newest first.
    func ukPMIReadings(type: UKPMIReading.PMIType, limit: Int = 60) throws -> [UKPMIReading] {
        try dbWriter.read { db in
            try UKPMIReading
                .filter(Column("type") == type.rawValue)
                .order(Column("date").desc, Column("isPreliminary"))
                .limit(limit)
                .fetchAll(db)
        }
    }

    /// The most recent reference-month date stored for each PMI type.
    /// Returns nil for types that have no rows yet.
    func latestUKPMIDate(type: UKPMIReading.PMIType) throws -> Date? {
        try dbWriter.read { db in
            try UKPMIReading
                .filter(Column("type") == type.rawValue)
                .order(Column("date").desc)
                .fetchOne(db)?
                .date
        }
    }

    func saveUKPMIReadings(_ readings: [UKPMIReading]) throws {
        try dbWriter.write { db in
            for var reading in readings {
                try reading.upsert(db)
            }
        }
    }
}
