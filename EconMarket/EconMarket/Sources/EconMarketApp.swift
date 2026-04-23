import SwiftUI

@main
struct EconMarketApp: App {
    init() {
        _ = AppDatabase.shared
        DataFetchService.shared.alphaVantageKey = Secrets.alphaVantageKey
        DataFetchService.shared.fredApiKey      = Secrets.fredApiKey
        UKPMIService.shared.finnhubKey          = Secrets.finnhubKey
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
