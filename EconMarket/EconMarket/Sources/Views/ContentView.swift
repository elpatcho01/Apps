import SwiftUI
import GRDB

struct ContentView: View {
    var body: some View {
        TabView {
            CommoditiesListView()
                .tabItem { Label("Commodities", systemImage: "chart.bar.fill") }

            IndicatorsListView()
                .tabItem { Label("Economy", systemImage: "chart.line.uptrend.xyaxis") }

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
    }
}

// MARK: - Commodities

struct CommoditiesListView: View {
    @State private var commodities: [Commodity] = []
    @State private var isRefreshing = false
    @State private var errorMessage: String?

    var grouped: [String: [Commodity]] {
        Dictionary(grouping: commodities, by: { $0.category.rawValue.capitalized })
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(grouped.keys.sorted(), id: \.self) { category in
                    Section(category) {
                        ForEach(grouped[category]!, id: \.id) { commodity in
                            NavigationLink(destination: CommodityDetailView(commodity: commodity)) {
                                CommodityRow(commodity: commodity)
                            }
                        }
                    }
                }
            }
            .navigationTitle("Commodities")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await refresh() }
                    } label: {
                        if isRefreshing {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(isRefreshing)
                }
            }
            .alert("Error", isPresented: .constant(errorMessage != nil), actions: {
                Button("OK") { errorMessage = nil }
            }, message: {
                Text(errorMessage ?? "")
            })
            .task { loadLocal() }
        }
    }

    private func loadLocal() {
        commodities = (try? AppDatabase.shared.allCommodities()) ?? []
    }

    private func refresh() async {
        isRefreshing = true
        await DataFetchService.shared.refreshAllCommodities()
        loadLocal()
        isRefreshing = false
    }
}

struct CommodityRow: View {
    let commodity: Commodity
    @State private var latestPrice: CommodityPrice?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(commodity.name).font(.headline)
                Text(commodity.symbol).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let price = latestPrice {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(price.close, format: .number.precision(.fractionLength(2)))
                        .font(.headline.monospacedDigit())
                    Text(commodity.unit).font(.caption2).foregroundStyle(.secondary)
                }
            } else {
                Text("—").foregroundStyle(.secondary)
            }
        }
        .task {
            if let id = commodity.id {
                latestPrice = try? AppDatabase.shared.latestPrice(for: id)
            }
        }
    }
}

struct CommodityDetailView: View {
    let commodity: Commodity
    @State private var prices: [CommodityPrice] = []

    var body: some View {
        List {
            Section("Recent Prices (\(commodity.unit))") {
                ForEach(prices, id: \.id) { price in
                    HStack {
                        Text(price.date, format: .dateTime.year().month().day())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(price.close, format: .number.precision(.fractionLength(2)))
                            .font(.body.monospacedDigit())
                    }
                }
            }
        }
        .navigationTitle(commodity.name)
        .task {
            if let id = commodity.id {
                prices = (try? AppDatabase.shared.prices(for: id, limit: 60)) ?? []
            }
        }
    }
}

// MARK: - Economic Indicators

struct IndicatorsListView: View {
    @State private var indicators: [EconomicIndicator] = []
    @State private var isRefreshing = false

    var grouped: [String: [EconomicIndicator]] {
        Dictionary(grouping: indicators, by: { $0.category.rawValue.capitalized })
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(grouped.keys.sorted(), id: \.self) { category in
                    Section(category) {
                        ForEach(grouped[category]!, id: \.id) { indicator in
                            NavigationLink(destination: IndicatorDetailView(indicator: indicator)) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(indicator.name).font(.headline)
                                    Text("\(indicator.code) · \(indicator.frequency.rawValue.capitalized)")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Economy")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await refresh() }
                    } label: {
                        if isRefreshing {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(isRefreshing)
                }
            }
            .task { indicators = (try? AppDatabase.shared.allIndicators()) ?? [] }
        }
    }

    private func refresh() async {
        isRefreshing = true
        await DataFetchService.shared.refreshAllIndicators()
        indicators = (try? AppDatabase.shared.allIndicators()) ?? []
        isRefreshing = false
    }
}

struct IndicatorDetailView: View {
    let indicator: EconomicIndicator
    @State private var points: [EconomicDataPoint] = []

    var body: some View {
        List {
            Section {
                LabeledContent("Unit", value: indicator.unit)
                LabeledContent("Frequency", value: indicator.frequency.rawValue.capitalized)
                LabeledContent("Source", value: indicator.source)
            }
            Section("Data (\(indicator.unit))") {
                ForEach(points, id: \.id) { point in
                    HStack {
                        Text(point.date, format: .dateTime.year().month().day())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text(point.value, format: .number.precision(.fractionLength(2)))
                            .font(.body.monospacedDigit())
                    }
                }
            }
        }
        .navigationTitle(indicator.name)
        .task {
            if let id = indicator.id {
                points = (try? AppDatabase.shared.dataPoints(for: id, limit: 60)) ?? []
            }
        }
    }
}

// MARK: - Settings

struct SettingsView: View {
    @AppStorage("alphaVantageKey") private var alphaVantageKey = ""
    @AppStorage("fredApiKey") private var fredApiKey = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Alpha Vantage") {
                        SecureField("API Key", text: $alphaVantageKey)
                            .multilineTextAlignment(.trailing)
                    }
                    LabeledContent("FRED") {
                        SecureField("API Key", text: $fredApiKey)
                            .multilineTextAlignment(.trailing)
                    }
                } header: {
                    Text("API Keys")
                } footer: {
                    Text("Alpha Vantage: alphavantage.co (free, 25 req/day)\nFRED: fred.stlouisfed.org (free)")
                }
            }
            .navigationTitle("Settings")
            .onChange(of: alphaVantageKey) { _, v in DataFetchService.shared.alphaVantageKey = v }
            .onChange(of: fredApiKey)      { _, v in DataFetchService.shared.fredApiKey = v }
            .onAppear {
                DataFetchService.shared.alphaVantageKey = alphaVantageKey
                DataFetchService.shared.fredApiKey = fredApiKey
            }
        }
    }
}
