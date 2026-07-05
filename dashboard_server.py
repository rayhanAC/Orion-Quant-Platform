#!/usr/bin/env python3
import os
import json
import time
import requests
import statistics
from flask import Flask, jsonify, request, render_template

ASSETS = os.environ.get("ASSETS", "BTC,ETH,LINK,SUI,XMR,XRP,SOL").split(",")
STATE_DIR = os.environ.get("STATE_DIR", os.path.expanduser("~/.openclaw/workspace"))
CONFIG_DIR = os.environ.get("CONFIG_DIR", "configs")
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.0"))
TRADING_DAYS = int(os.environ.get("TRADING_DAYS", "252"))
MIN_TRADES = int(os.environ.get("MIN_TRADES", "5"))
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

app = Flask(__name__, template_folder="templates", static_folder="static")
_candle_cache = {}
CACHE_TTL = 30

# ─── helpers ────────────────────────────────────────────────────────────

def state_path(asset):
    return os.path.join(STATE_DIR, f"orion_{asset.lower()}_state.json")

def config_path(asset):
    return os.path.join(CONFIG_DIR, f"{asset.lower()}_config.json")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {path}: {e}")
    return default

def load_state(asset):
    return load_json(state_path(asset), {
        "direction": "FLAT",
        "entryPrice": None,
        "entryTime": None,
        "stats": {"totalTrades": 0, "wins": 0, "losses": 0, "totalPnlUsd": 0.0, "totalPnlPts": 0.0},
        "trade_history": [],
        "lastCheck": None
    })

def load_config_dict(asset):
    return load_json(config_path(asset), {})

def compute_metrics(trades, risk_free_rate=RISK_FREE_RATE, trading_days=TRADING_DAYS, min_trades=MIN_TRADES):
    if not trades or len(trades) < min_trades:
        return {"sharpe": None, "profit_factor": None, "win_rate": None, "trades": len(trades)}

    returns = [t["pnl_usd"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [abs(r) for r in returns if r < 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else None

    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns) if len(returns) > 1 else 0
    daily_rf = risk_free_rate / trading_days if risk_free_rate > 0 else 0
    excess_return = mean_r - daily_rf
    sharpe = (excess_return / std_r * (trading_days ** 0.5)) if std_r > 0 else 0

    wr = len(wins) / len(returns) * 100

    return {
        "sharpe": round(sharpe, 2),
        "profit_factor": round(pf, 2) if pf is not None else None,
        "win_rate": round(wr, 1),
        "trades": len(returns),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }

# ─── candle cache ───────────────────────────────────────────────────────

def fetch_candles(asset):
    now = time.time()
    cached = _candle_cache.get(asset)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["data"]

    interval = os.environ.get(f"INTERVAL_{asset}", "15m" if asset == "SOL" else "1h")
    try:
        now_ms = int(now * 1000)
        mult = 15 * 60 if asset == "SOL" else 3600
        duration = 300 * mult * 1000
        resp = requests.post(HL_INFO_URL, json={
            "type": "candleSnapshot",
            "req": {"coin": asset, "interval": interval, "startTime": now_ms - duration, "endTime": now_ms}
        }, timeout=10)
        resp.raise_for_status()
        candles = resp.json()
        formatted = [{"time": int(c["t"]) // 1000, "open": float(c["o"]), "high": float(c["h"]),
                       "low": float(c["l"]), "close": float(c["c"]), "volume": float(c["v"])} for c in candles]
        formatted.sort(key=lambda x: x["time"])
        _candle_cache[asset] = {"ts": now, "data": formatted}
        return formatted
    except Exception as e:
        print(f"Error fetching candles for {asset}: {e}")
        return None

# ─── routes ─────────────────────────────────────────────────────────────

@app.after_request
def add_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response

@app.route("/favicon.ico")
def favicon():
    return "", 204

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status", methods=["GET"])
def get_status():
    status_data = {}
    all_trades = []
    for asset in ASSETS:
        state = load_state(asset)
        trades = state.get("trade_history", [])
        all_trades.extend(trades)
        status_data[asset] = {
            "running": None,  # user-defined: check / write your own running flag
            "config": load_config_dict(asset),
            "state": state,
            "metrics": compute_metrics(trades),
        }
    status_data["global_metrics"] = compute_metrics(all_trades)
    return jsonify(status_data)

@app.route("/api/candles/<asset>", methods=["GET"])
def get_candles(asset):
    if asset not in ASSETS:
        return jsonify({"error": "Invalid asset"}), 400
    data = fetch_candles(asset)
    if data is None:
        return jsonify({"error": "Failed to fetch candles"}), 500
    return jsonify(data)

@app.route("/api/configs/<asset>", methods=["GET"])
def get_config(asset):
    if asset not in ASSETS:
        return jsonify({"error": "Invalid asset"}), 400
    return jsonify(load_config_dict(asset))

@app.route("/api/log", methods=["POST"])
def client_log():
    try:
        payload = request.get_json(force=True, silent=True) or {}
        msg = payload.get("msg", "")
        print(f"[CLIENT] {msg}")
        with open("client.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception as e:
        print(f"[CLIENT] log error: {e}")
    return jsonify({"ok": True})

if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
