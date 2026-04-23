# EconMarket

An iOS app that downloads economic and commodities market data and stores it locally in SQLite via [GRDB.swift](https://github.com/groue/GRDB.swift).

## Data Sources

| Data | Provider | Free tier |
|------|----------|-----------|
| Commodities (Gold, Oil, Wheat, …) | [Alpha Vantage](https://www.alphavantage.co) | 25 req/day |
| Economic indicators (GDP, CPI, FRED series) | [FRED – St. Louis Fed](https://fred.stlouisfed.org) | Unlimited |

## Database Schema

```
commodities
  id, symbol, name, category, unit

commodity_prices
  id, commodityId → commodities.id, date, open, high, low, close, volume, fetchedAt

economic_indicators
  id, code, name, category, unit, frequency, source

economic_data_points
  id, indicatorId → economic_indicators.id, date, value, fetchedAt
```

Commodities tracked: Gold, Silver, Platinum, Palladium, WTI Crude, Brent Crude, Natural Gas, Heating Oil, Corn, Wheat, Soybeans, Cocoa, Coffee, Cotton, Sugar, Live Cattle, Feeder Cattle, Lean Hogs.

FRED series tracked: GDP, Real GDP, CPI, Core CPI, PCE, Unemployment, Payrolls, Jobless Claims, Fed Funds Rate, 10Y/2Y Treasury Yields, M2, Trade Balance, Housing Starts, Case-Shiller Index.

## Setup

### 1. API Keys

Get free keys from:
- https://www.alphavantage.co/support/#api-key
- https://fred.stlouisfed.org/docs/api/api_key.html

Enter them in the app under **Settings → API Keys**.

### 2. Generate the Xcode project

```bash
brew install xcodegen
cd EconMarket
xcodegen generate
open EconMarket.xcodeproj
```

### 3. Run

Select your iPhone and press **⌘R**.

## Usage

- **Commodities tab** — tap the refresh icon to download the latest prices for all 18 commodities.
- **Economy tab** — tap the refresh icon to pull the latest FRED observations for all 15 indicators.
- Data is stored locally in SQLite; the app works fully offline after the first download.
