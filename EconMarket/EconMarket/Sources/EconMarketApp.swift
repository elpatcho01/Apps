import SwiftUI

@main
struct EconMarketApp: App {
    init() {
        // Trigger DB creation at launch (runs migrations & seeds lookup tables)
        _ = AppDatabase.shared
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
