import requests
from datetime import datetime, timezone, timedelta

PAIRS = ["BTCUSDT", "ETHUSDT"]
BINANCE_BASE = "https://api.binance.com/api/v3"
SWEEP_MIN_PERCENT = 0.2

def get_klines(symbol, interval, limit=100):
    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return []
    return r.json()

def klines_to_ohlc(klines):
    data = []
    for k in klines:
        data.append({
            "time": datetime.fromtimestamp(k[0]/1000, tz=timezone.utc),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        })
    return data

def get_daily_levels(one_hour_data):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start - timedelta(seconds=1)

    yh, yl = None, None
    for c in one_hour_data:
        if yesterday_start <= c["time"] <= yesterday_end:
            if yh is None or c["high"] > yh: yh = c["high"]
            if yl is None or c["low"] < yl: yl = c["low"]

    today_open = None
    for c in one_hour_data:
        if c["time"] >= today_start:
            today_open = c["open"]
            break

    y_open = None
    for c in one_hour_data:
        if yesterday_start <= c["time"] < today_start:
            y_open = c["open"]
            break

    return {"YH": yh, "YL": yl, "today_open": today_open, "y_open": y_open}

def find_sweep(one_hour_data, yh, yl):
    for c in reversed(one_hour_data[-10:]):
        if yl and c["low"] < yl * (1 - SWEEP_MIN_PERCENT/100) and c["close"] > yl:
            return "long", c
        if yh and c["high"] > yh * (1 + SWEEP_MIN_PERCENT/100) and c["close"] < yh:
            return "short", c
    return None, None

def find_order_block(five_min_data, sweep_candle, direction):
    sweep_start = sweep_candle["time"]
    for c in reversed(five_min_data):
        if c["time"] >= sweep_start:
            continue
        if direction == "long" and c["close"] < c["open"]:
            return c
        if direction == "short" and c["close"] > c["open"]:
            return c
    return None

def generate_signal(pair):
    one_hour = klines_to_ohlc(get_klines(pair, "1h", 100))
    five_min = klines_to_ohlc(get_klines(pair, "5m", 500))
    if not one_hour or not five_min:
        return None
    levels = get_daily_levels(one_hour)
    if not levels["YH"] or not levels["YL"]:
        return None
    sweep_dir, sweep_candle = find_sweep(one_hour, levels["YH"], levels["YL"])
    if not sweep_dir:
        return None
    ob = find_order_block(five_min, sweep_candle, sweep_dir)
    if not ob:
        return None

    if sweep_dir == "long":
        entry = ob["high"]
        stop = round(sweep_candle["low"] * 0.999, 4)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        highs = [c["high"] for c in one_hour if c["time"] >= today_start]
        tp1 = max(highs) if highs else entry * 1.01
        tp2 = levels["y_open"] if levels["y_open"] and levels["y_open"] > entry else levels["YH"]
    else:
        entry = ob["low"]
        stop = round(sweep_candle["high"] * 1.001, 4)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        lows = [c["low"] for c in one_hour if c["time"] >= today_start]
        tp1 = min(lows) if lows else entry * 0.99
        tp2 = levels["y_open"] if levels["y_open"] and levels["y_open"] < entry else levels["YL"]

    risk = abs(entry - stop)
    if risk <= 0 or abs(tp1 - entry) / risk < 1.5:
        return None
    return {
        "pair": pair,
        "direction": sweep_dir.upper(),
        "entry": round(entry, 4),
        "stop_loss": stop,
        "take_profit_1": round(tp1, 4),
        "take_profit_2": round(tp2, 4),
    }

def main():
    signals = []
    for pair in PAIRS:
        sig = generate_signal(pair)
        if sig:
            signals.append(sig)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open("signal.md", "w") as f:
        f.write(f"# Daily Crypto Signal ({now_utc})\n\n")
        if not signals:
            f.write("**No valid setup today.** Waiting for a liquidity sweep with strong R:R.\n")
        else:
            f.write("| Pair | Direction | Entry | Stop Loss | Take Profit 1 | Take Profit 2 |\n")
            f.write("|------|-----------|-------|-----------|----------------|----------------|\n")
            for s in signals:
                f.write(f"| {s['pair']} | {s['direction']} | {s['entry']} | {s['stop_loss']} | {s['take_profit_1']} | {s['take_profit_2']} |\n")
            f.write("\n---\n")
            f.write("*Not financial advice.*\n")

if __name__ == "__main__":
    main()
