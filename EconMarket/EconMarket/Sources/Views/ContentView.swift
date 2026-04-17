import SwiftUI
import GRDB

struct ContentView: View {
    var body: some View {
        TabView {
            CommoditiesListView()
                .tabItem { Label("Commodities", systemImage: "chart.bar.fill") }

            IndicatorsListView()
                .tabItem { Label("Economy", systemImage: "chart.line.uptrend.xyaxis") }

            UKPMIView()
                .tabItem { Label("UK PMI", systemImage: "flag") }

            ONSDataView()
                .tabItem { Label("ONS", systemImage: "building.columns.fill") }

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

// MARK: - ONS Data

struct ONSDataView: View {
    @State private var allSeries: [ONSSeries] = []
    @State private var isSyncing = false
    @State private var syncError: String?
    @State private var syncProgress: String?

    var grouped: [String: [ONSSeries]] {
        Dictionary(grouping: allSeries, by: { $0.category.displayName })
    }

    var body: some View {
        NavigationStack {
            List {
                if allSeries.isEmpty {
                    ContentUnavailableView(
                        "No Data",
                        systemImage: "building.columns",
                        description: Text("Tap Sync to download ONS data. No API key needed.")
                    )
                } else {
                    ForEach(grouped.keys.sorted(), id: \.self) { category in
                        Section(category) {
                            ForEach(grouped[category]!, id: \.id) { series in
                                NavigationLink(destination: ONSSeriesDetailView(series: series)) {
                                    ONSSeriesRow(series: series)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("ONS UK Data")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await sync() }
                    } label: {
                        if isSyncing {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(isSyncing)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if let progress = syncProgress {
                    Text(progress)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(8)
                        .frame(maxWidth: .infinity)
                        .background(.ultraThinMaterial)
                }
            }
            .alert("Sync Error", isPresented: .constant(syncError != nil), actions: {
                Button("OK") { syncError = nil }
            }, message: {
                Text(syncError ?? "")
            })
            .task { loadLocal() }
        }
    }

    private func loadLocal() {
        allSeries = (try? AppDatabase.shared.allONSSeries()) ?? []
    }

    private func sync() async {
        isSyncing = true
        syncProgress = "Downloading ONS series…"
        do {
            try await ONSService.shared.syncAllSeries()
            loadLocal()
            syncProgress = nil
        } catch {
            syncError = error.localizedDescription
            syncProgress = nil
        }
        isSyncing = false
    }
}

struct ONSSeriesRow: View {
    let series: ONSSeries
    @State private var latest: ONSDataPoint?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(series.title).font(.subheadline)
                Text("\(series.seriesId) · \(series.frequency.rawValue.capitalized)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let pt = latest {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(pt.value, format: .number.precision(.fractionLength(1)))
                        .font(.subheadline.bold().monospacedDigit())
                    Text(series.unit).font(.caption2).foregroundStyle(.secondary)
                }
            } else {
                Text("—").foregroundStyle(.tertiary)
            }
        }
        .task {
            latest = try? AppDatabase.shared.latestONSDataPoint(seriesId: series.seriesId)
        }
    }
}

struct ONSSeriesDetailView: View {
    let series: ONSSeries
    @State private var points: [ONSDataPoint] = []

    var body: some View {
        List {
            Section {
                LabeledContent("Series ID",  value: series.seriesId)
                LabeledContent("Dataset",    value: series.datasetId)
                LabeledContent("Unit",       value: series.unit)
                LabeledContent("Frequency",  value: series.frequency.rawValue.capitalized)
                if let fetched = series.lastFetchedAt {
                    LabeledContent("Last synced") {
                        Text(fetched, style: .relative)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Section("History (\(series.unit))") {
                if points.isEmpty {
                    Text("No data — tap the sync button on the ONS tab.")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                } else {
                    ForEach(points, id: \.id) { pt in
                        HStack {
                            Text(periodLabel(pt.date, frequency: series.frequency))
                                .foregroundStyle(.secondary)
                                .font(.subheadline)
                            Spacer()
                            Text(pt.value, format: .number.precision(.fractionLength(2)))
                                .font(.subheadline.monospacedDigit())
                        }
                    }
                }
            }
        }
        .navigationTitle(series.title)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            points = (try? AppDatabase.shared.onsDataPoints(seriesId: series.seriesId, limit: 120)) ?? []
        }
    }

    private func periodLabel(_ date: Date, frequency: ONSSeries.Frequency) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let year  = cal.component(.year,  from: date)
        let month = cal.component(.month, from: date)
        switch frequency {
        case .monthly:
            let abbr = ["Jan","Feb","Mar","Apr","May","Jun",
                        "Jul","Aug","Sep","Oct","Nov","Dec"][month - 1]
            return "\(abbr) \(year)"
        case .quarterly:
            let q = (month - 1) / 3 + 1
            return "\(year) Q\(q)"
        case .annual:
            return "\(year)"
        }
    }
}

// MARK: - UK PMI

struct UKPMIView: View {
    @State private var selectedType: UKPMIReading.PMIType = .composite
    @State private var readings: [UKPMIReading] = []
    @State private var isSyncing = false
    @State private var syncError: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Series", selection: $selectedType) {
                    ForEach(UKPMIReading.PMIType.allCases, id: \.self) { type in
                        Text(seriesShortName(type)).tag(type)
                    }
                }
                .pickerStyle(.segmented)
                .padding()

                List {
                    if readings.isEmpty {
                        ContentUnavailableView(
                            "No Data",
                            systemImage: "arrow.clockwise",
                            description: Text("Tap the sync button to download PMI data.")
                        )
                    } else {
                        Section {
                            PMIHeaderRow()
                        }
                        ForEach(readings, id: \.id) { reading in
                            PMIReadingRow(reading: reading)
                        }
                    }
                }
                .listStyle(.insetGrouped)
            }
            .navigationTitle(selectedType.displayName)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await sync() }
                    } label: {
                        if isSyncing {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(isSyncing)
                }
            }
            .alert("Sync Error", isPresented: .constant(syncError != nil), actions: {
                Button("OK") { syncError = nil }
            }, message: {
                Text(syncError ?? "")
            })
            .onChange(of: selectedType) { _, _ in loadLocal() }
            .task { loadLocal() }
        }
    }

    private func seriesShortName(_ type: UKPMIReading.PMIType) -> String {
        switch type {
        case .composite:    return "Composite"
        case .services:     return "Services"
        case .manufacturing: return "Manuf."
        case .construction: return "Constr."
        }
    }

    private func loadLocal() {
        readings = (try? AppDatabase.shared.ukPMIReadings(type: selectedType, limit: 60)) ?? []
    }

    private func sync() async {
        isSyncing = true
        do {
            try await UKPMIService.shared.syncMissingData()
            loadLocal()
        } catch {
            syncError = error.localizedDescription
        }
        isSyncing = false
    }
}

struct PMIHeaderRow: View {
    var body: some View {
        HStack {
            Text("Month").font(.caption).foregroundStyle(.secondary).frame(width: 80, alignment: .leading)
            Spacer()
            Text("Consensus").font(.caption).foregroundStyle(.secondary).frame(width: 72, alignment: .trailing)
            Text("Actual").font(.caption).foregroundStyle(.secondary).frame(width: 60, alignment: .trailing)
            Text("Type").font(.caption).foregroundStyle(.secondary).frame(width: 40, alignment: .trailing)
        }
        .padding(.vertical, 2)
    }
}

struct PMIReadingRow: View {
    let reading: UKPMIReading

    private var monthLabel: String {
        let f = DateFormatter()
        f.dateFormat = "MMM yyyy"
        f.locale = Locale(identifier: "en_GB")
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: reading.date)
    }

    private var beatConsensus: Bool? {
        guard let actual = reading.actual, let consensus = reading.consensus else { return nil }
        return actual > consensus
    }

    var body: some View {
        HStack {
            Text(monthLabel)
                .font(.subheadline)
                .frame(width: 80, alignment: .leading)

            Spacer()

            // Consensus
            Group {
                if let c = reading.consensus {
                    Text(c, format: .number.precision(.fractionLength(1)))
                } else {
                    Text("—")
                }
            }
            .font(.subheadline.monospacedDigit())
            .foregroundStyle(.secondary)
            .frame(width: 72, alignment: .trailing)

            // Actual — coloured by beat/miss vs consensus
            Group {
                if let a = reading.actual {
                    Text(a, format: .number.precision(.fractionLength(1)))
                        .foregroundStyle(beatColor)
                } else {
                    Text("—").foregroundStyle(.secondary)
                }
            }
            .font(.subheadline.bold().monospacedDigit())
            .frame(width: 60, alignment: .trailing)

            // Flash / Final badge
            Text(reading.isPreliminary ? "Flash" : "Final")
                .font(.caption2)
                .foregroundStyle(reading.isPreliminary ? Color.orange : Color.secondary)
                .frame(width: 40, alignment: .trailing)
        }
    }

    private var beatColor: Color {
        guard let beat = beatConsensus else { return .primary }
        return beat ? .green : .red
    }
}

// MARK: - Settings

struct SettingsView: View {
    @AppStorage("alphaVantageKey") private var alphaVantageKey = ""
    @AppStorage("fredApiKey")      private var fredApiKey = ""
    @AppStorage("finnhubKey")      private var finnhubKey = ""

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
                    LabeledContent("Finnhub") {
                        SecureField("API Key", text: $finnhubKey)
                            .multilineTextAlignment(.trailing)
                    }
                } header: {
                    Text("API Keys")
                } footer: {
                    Text(
                        "Alpha Vantage: alphavantage.co — free, 25 req/day\n" +
                        "FRED: fred.stlouisfed.org — free\n" +
                        "Finnhub: finnhub.io — free, 60 req/min (used for UK PMI)"
                    )
                }
            }
            .navigationTitle("Settings")
            .onChange(of: alphaVantageKey) { _, v in DataFetchService.shared.alphaVantageKey = v }
            .onChange(of: fredApiKey)      { _, v in DataFetchService.shared.fredApiKey = v }
            .onChange(of: finnhubKey)      { _, v in UKPMIService.shared.finnhubKey = v }
            .onAppear {
                DataFetchService.shared.alphaVantageKey = alphaVantageKey
                DataFetchService.shared.fredApiKey      = fredApiKey
                UKPMIService.shared.finnhubKey          = finnhubKey
            }
        }
    }
}
