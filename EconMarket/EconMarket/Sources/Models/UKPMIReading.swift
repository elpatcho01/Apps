import Foundation
import GRDB

struct UKPMIReading: Codable, FetchableRecord, PersistableRecord {
    var id: Int64?

    /// Which PMI series this reading belongs to.
    var type: PMIType

    /// First day of the calendar month the reading covers (not the release date).
    var date: Date

    /// The published PMI index value. Nil until officially released.
    var actual: Double?

    /// Economist consensus forecast published before the release.
    var consensus: Double?

    /// The prior month's final reading (as reported alongside this release).
    var previous: Double?

    /// True = flash/preliminary estimate; false = final reading.
    var isPreliminary: Bool

    /// Wall-clock time this row was written to the database.
    var fetchedAt: Date

    static let databaseTableName = "uk_pmi_readings"

    enum PMIType: String, Codable, CaseIterable {
        case services      = "services"
        case manufacturing = "manufacturing"
        case construction  = "construction"
        case composite     = "composite"

        var displayName: String {
            switch self {
            case .services:      return "UK Services PMI"
            case .manufacturing: return "UK Manufacturing PMI"
            case .construction:  return "UK Construction PMI"
            case .composite:     return "UK Composite PMI"
            }
        }
    }
}
