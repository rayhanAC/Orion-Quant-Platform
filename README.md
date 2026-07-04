# Orion Quant Platform

A real-time quant trading dashboard that displays your algo trading activity, candle data, and trade history on a professional-grade chart. Built for **Hyperliquid** perpetual futures.

![Dashboard Preview](https://i.imgur.com/placeholder.png)

## Features

- **Live candle charts** — Fetches 1h/15m candles from Hyperliquid via `candleSnapshot` API (30s server-side cache)
- **Multi-asset dashboard** — Track BTC, ETH, LINK, SUI, XMR, XRP, SOL (configurable)
- **Trade history** — View entry/exit prices, PnL, duration, and reason for every trade
- **Trade markers on chart** — Green/red arrows for winning/losing exits; blue circles for entries
- **Config display** — Show your algo's current parameters per asset
- **Global stats** — Aggregate PnL, win rate, and active counts
- **Dark terminal UI** — Full-width, zero clutter, monospace-finance aesthetic

## Quick Start

```bash
# Clone the repo
git clone https://github.com/rayhanAC/Orion-Quant-Platform.git
cd Orion-Quant-Platform

# Install dependencies
pip install flask requests

# Create state/config directories (your algo writes here)
mkdir states configs

# (Optional) Customize assets via environment
export ASSETS="BTC,ETH,SOL"

# Run the server
python3 dashboard_server.py

# Open in browser
open http://localhost:5000
```

The dashboard is now running at `http://localhost:5000`. It will show candle data from Hyperliquid, but your asset cards and trade history will be empty until you connect your algo.

## How to Connect Your Algo

The dashboard reads **state files** from the `states/` directory. Your algo writes a JSON file per asset, and the dashboard picks it up on every 3-second refresh.

### Step 1: Write State Files

Your algo should write a JSON file to `states/<ASSET>_state.json` (e.g., `states/BTC_state.json`).

**File format:**

```json
{
  "direction": "LONG",
  "entryPrice": 64250.0,
  "entryTime": "2026-07-04T12:00:00Z",
  "tp": 65000.0,
  "sl": 63800.0,
  "trailSlPrice": 64300.0,
  "stats": {
    "totalTrades": 42,
    "wins": 28,
    "losses": 14,
    "totalPnlUsd": 1250.42,
    "totalPnlPts": 195.5
  },
  "trade_history": [
    {
      "entry_price": 63500.0,
      "exit_price": 64100.0,
      "entry_time": "2026-07-03T08:00:00Z",
      "exit_time": "2026-07-03T14:30:00Z",
      "direction": "LONG",
      "pnl_pts": 600.0,
      "pnl_usd": 180.0,
      "duration_min": 390,
      "reason": "take_profit"
    }
  ],
  "lastCheck": "2026-07-04T15:30:00Z"
}
```

#### State File Fields

| Field | Type | Description |
|-------|------|-------------|
| `direction` | string | `"LONG"`, `"SHORT"`, or `"FLAT"` |
| `entryPrice` | number | Current position entry price (null if flat) |
| `entryTime` | string (ISO) | Position open timestamp |
| `tp` | number or null | Take-profit price |
| `sl` | number or null | Stop-loss price |
| `trailSlPrice` | number or null | Trailing stop price |
| `stats.totalTrades` | number | Total trades taken |
| `stats.wins` | number | Winning trades |
| `stats.losses` | number | Losing trades |
| `stats.totalPnlUsd` | number | Aggregate PnL in USD |
| `stats.totalPnlPts` | number | Aggregate PnL in points |
| `trade_history[]` | array | Array of trade objects |
| `lastCheck` | string (ISO) | Last time your algo updated this file |

#### Trade Entry Fields

| Field | Type | Description |
|-------|------|-------------|
| `entry_price` | number | Entry price |
| `exit_price` | number | Exit price |
| `entry_time` | string (ISO) | Trade open time |
| `exit_time` | string (ISO) | Trade close time |
| `direction` | string | `"LONG"` or `"SHORT"` |
| `pnl_pts` | number | PnL in price points |
| `pnl_usd` | number | PnL in USD |
| `duration_min` | number | Duration in minutes |
| `reason` | string | `"take_profit"`, `"stop_loss"`, `"signal"`, etc. |

### Step 2: (Optional) Write Config Files

Your algo can also write a config file to `configs/<ASSET>_config.json` to display its parameters on the dashboard. Any JSON object works:

```json
{
  "maxPositions": 1,
  "positionSizeUsd": 100,
  "leverage": 3,
  "takeProfitPct": 1.5,
  "stopLossPct": 1.0
}
```

### Step 3: Python Example

Here's a minimal Python script your algo can use to write state:

```python
#!/usr/bin/env python3
import json, os, time

STATE_DIR = "states"   # matches STATE_DIR default in dashboard_server.py
ASSET = "BTC"

def write_state(direction, entry_price, entry_time, pnl, trades):
    state = {
        "direction": direction,
        "entryPrice": entry_price,
        "entryTime": entry_time,
        "stats": {
            "totalTrades": len(trades),
            "wins": sum(1 for t in trades if t["pnl_usd"] > 0),
            "losses": sum(1 for t in trades if t["pnl_usd"] <= 0),
            "totalPnlUsd": pnl,
            "totalPnlPts": 0.0,
        },
        "trade_history": trades,
        "lastCheck": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(f"{STATE_DIR}/{ASSET}_state.json", "w") as f:
        json.dump(state, f, indent=2)

# Your algo logic here...
write_state("LONG", 64000.0, "2026-07-04T12:00:00Z", 800.0, [
    {"entry_price": 63500, "exit_price": 64100, "entry_time": "2026-07-03T08:00:00Z",
     "exit_time": "2026-07-03T14:30:00Z", "direction": "LONG", "pnl_pts": 600,
     "pnl_usd": 180, "duration_min": 390, "reason": "take_profit"},
])
```

### Step 4: Verify

1. Restart the dashboard server
2. Open `http://localhost:5000`
3. Your asset will show P&L, direction, trade history, and chart markers

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Returns all asset states, configs, and running status |
| `/api/candles/<asset>` | GET | Returns 1h candles (300 bars) from Hyperliquid |
| `/api/configs/<asset>` | GET | Returns config as JSON |
| `/` | GET | Dashboard frontend |

### Status Response Format

```json
{
  "BTC": {
    "running": null,
    "config": { "maxPositions": 1, "positionSizeUsd": 100 },
    "state": { "direction": "LONG", "entryPrice": 64250, ... }
  },
  "ETH": { ... }
}
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSETS` | `BTC,ETH,LINK,SUI,XMR,XRP,SOL` | Comma-separated asset list |
| `STATE_DIR` | `states` | Directory for state JSON files |
| `CONFIG_DIR` | `configs` | Directory for config JSON files |
| `PORT` | `5000` | HTTP server port |

### Per-Asset Interval

Set custom candle intervals via environment:
```bash
export INTERVAL_SOL="15m"
export INTERVAL_BTC="1h"
```

## TradingView Advanced Charts (Optional)

The dashboard uses **Lightweight Charts** (free, open-source) by default. If you have a **TradingView Charting Library license**, you can upgrade:

1. Download the charting library from TradingView
2. Extract it to `static/charting_library/`
3. Update `templates/index.html`:
   - Replace the lightweight-charts CDN script with `<script src="/static/charting_library/charting_library.standalone.js"></script>`
   - Swap the chart initialization to use `new TradingView.widget({...})`

## Project Structure

```
├── dashboard_server.py    # Flask server (API + frontend)
├── templates/
│   └── index.html         # Dashboard frontend (lightweight-charts)
├── static/                # Static assets (charting_library/ is gitignored)
├── examples/
│   ├── EXAMPLE_BTC_state.json   # Example state file format
│   └── EXAMPLE_BTC_config.json  # Example config file format
├── states/                # Your algo writes state JSON here (gitignored, created at runtime)
├── configs/               # Your algo writes config JSON here (gitignored, created at runtime)
├── start_all.sh           # Generic launcher template — edit for your bots
├── .gitignore
└── README.md
```

## License

MIT — do whatever you want. The TradingView Charting Library is commercial and not included.
