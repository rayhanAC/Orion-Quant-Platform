#!/usr/bin/env python3
import os
import json
import time
import csv
import io
import statistics
import requests
import traceback
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template, make_response

ASSETS = os.environ.get("ASSETS", "BTC,ETH,LINK,SUI,XMR,XRP,SOL").split(",")
STATE_DIR = os.environ.get("STATE_DIR", os.path.expanduser("~/.openclaw/workspace"))
CONFIG_DIR = os.environ.get("CONFIG_DIR", "configs")
RISK_FREE_RATE = float(os.environ.get("RISK_FREE_RATE", "0.0"))
TRADING_DAYS = int(os.environ.get("TRADING_DAYS", "252"))
MIN_TRADES = int(os.environ.get("MIN_TRADES", "5"))
DAILY_LOSS_LIMIT = float(os.environ.get("DAILY_LOSS_LIMIT", "-5000"))
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

def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

DEFAULT_NOTIONAL_USD = 50000.0

def estimate_notional(entry_price, trades, config, state):
    """Estimate position notional in USD from state, config, or realized trades."""
    if state and state.get("position_size_usd"):
        return float(state["position_size_usd"])
    if config.get("positionSizeUsd"):
        return float(config["positionSizeUsd"])
    notionals = []
    for t in trades:
        ep = t.get("entry_price")
        xp = t.get("exit_price")
        pnl = t.get("pnl_usd") or 0
        if ep and xp and abs(xp - ep) > 1e-12:
            notionals.append(abs(pnl) / abs(xp - ep) * ep)
    if notionals:
        return statistics.median(notionals)
    return DEFAULT_NOTIONAL_USD

def compute_position_risk(state, config):
    """Return (open_exposure_usd, open_risk_usd) for the current position."""
    direction = state.get("direction")
    entry = state.get("entryPrice")
    sl = state.get("sl") or state.get("trailSlPrice")
    if direction == "FLAT" or not entry:
        return 0.0, 0.0
    trades = state.get("trade_history", [])
    notional = estimate_notional(entry, trades, config, state)
    risk = abs(entry - sl) / entry * notional if sl else 0.0
    return notional, risk

def compute_today_pnl(trades):
    today = datetime.now(timezone.utc).date()
    total = 0.0
    for t in trades:
        dt = _parse_iso(t.get("exit_time"))
        if dt and dt.date() == today:
            total += t.get("pnl_usd", 0) or 0
    return total

def compute_bot_health(state):
    last_check = state.get("lastCheck")
    if not last_check:
        return {"last_check_seconds_ago": None, "healthy": False}
    dt = _parse_iso(last_check)
    if not dt:
        return {"last_check_seconds_ago": None, "healthy": False}
    seconds_ago = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    return {"last_check_seconds_ago": seconds_ago, "healthy": seconds_ago <= 300}

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
        traceback.print_exc()
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
    total_open_exposure = 0.0
    total_open_risk = 0.0
    total_today_pnl = 0.0

    for asset in ASSETS:
        state = load_state(asset)
        config = load_config_dict(asset)
        trades = state.get("trade_history", [])
        all_trades.extend(trades)

        open_exposure, open_risk = compute_position_risk(state, config)
        today_pnl = compute_today_pnl(trades)
        total_open_exposure += open_exposure
        total_open_risk += open_risk
        total_today_pnl += today_pnl

        status_data[asset] = {
            "running": None,
            "config": config,
            "state": state,
            "metrics": compute_metrics(trades),
            "open_exposure_usd": round(open_exposure, 2),
            "open_risk_usd": round(open_risk, 2),
            "today_pnl_usd": round(today_pnl, 2),
            "bot_health": compute_bot_health(state),
        }

    status_data["total_open_exposure_usd"] = round(total_open_exposure, 2)
    status_data["total_open_risk_usd"] = round(total_open_risk, 2)
    status_data["today_pnl_usd"] = round(total_today_pnl, 2)
    status_data["daily_loss_limit"] = DAILY_LOSS_LIMIT
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

@app.route("/api/export_trades", methods=["GET"])
def export_trades():
    fmt = request.args.get("format", "json").lower()
    trades = []
    for asset in ASSETS:
        state = load_state(asset)
        for t in state.get("trade_history", []):
            trade = dict(t)
            trade["asset"] = asset
            trades.append(trade)
    trades.sort(key=lambda x: x.get("exit_time") or x.get("entry_time") or "")

    if fmt == "csv":
        output = io.StringIO()
        if trades:
            fieldnames = list(trades[0].keys())
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(trades)
        else:
            output.write("asset,direction,entry_price,exit_price,entry_time,exit_time,pnl_usd,pnl_pts,duration_min,reason\n")
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers["Content-Disposition"] = "attachment; filename=trades.csv"
        return response

    return jsonify(trades)

if __name__ == "__main__":
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
