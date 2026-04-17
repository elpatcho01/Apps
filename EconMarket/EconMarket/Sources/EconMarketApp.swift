import SwiftUI

@main
struct EconMarketApp: App {
    init() {
        _ = AppDatabase.shared
        // Push persisted API keys into services before any view loads.
        let defaults = UserDefaults.standard
        DataFetchService.shared.alphaVantageKey = defaults.string(forKey: "alphaVantageKey") ?? ""
        DataFetchService.shared.fredApiKey      = defaults.string(forKey: "fredApiKey") ?? ""
        UKPMIService.shared.finnhubKey          = defaults.string(forKey: "finnhubKey") ?? ""
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
