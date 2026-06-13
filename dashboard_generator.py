import requests
import json
from datetime import datetime, timezone, timedelta

BINANCE_BASE = "https://api.binance.com/api/v3"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
PAIRS = ["BTCUSDT", "ETHUSDT"]
SWEEP_MIN_PERCENT = 0.2
VOLUME_SPIKE_THRESHOLD = 2.0
TOP_N_MOMENTUM = 15
OUTPUT_HTML = "dashboard.html"

# ---------- Binance Helpers (same as generate_signal.py) ----------
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
        "stop": stop,
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4)
    }

# ---------- CoinGecko Helpers (same as whale_scanner.py) ----------
def get_top_100():
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {"vs_currency":"usd","order":"market_cap_desc","per_page":100,"page":1,
              "sparkline":"false","price_change_percentage":"7d,30d"}
    r = requests.get(url, params=params)
    return r.json() if r.status_code == 200 else []

def get_volume_anomaly(coin):
    coin_id = coin["id"]
    vol_now = coin.get("total_volume", 0)
    if not vol_now:
        return 0, 0
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart?vs_currency=usd&days=7"
    r = requests.get(url)
    if r.status_code != 200:
        return 0, 0
    vols = r.json().get("total_volumes", [])
    if len(vols) < 2:
        return 0, 0
    prev_vols = [v[1] for v in vols[:-1]]
    avg = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    if avg == 0:
        return 0, 0
    return vol_now / avg, avg

def get_trending():
    r = requests.get(f"{COINGECKO_BASE}/search/trending")
    if r.status_code != 200:
        return []
    return [{"name": c["item"]["name"], "symbol": c["item"]["symbol"].upper(),
             "rank": c["item"].get("market_cap_rank"), "score": c["item"].get("score")}
            for c in r.json().get("coins", [])]

def get_doublers(coins):
    doubled = []
    for coin in coins:
        cid = coin["id"]
        cur = coin["current_price"]
        if not cur:
            continue
        d = (datetime.utcnow() - timedelta(days=21)).strftime("%d-%m-%Y")
        try:
            r = requests.get(f"{COINGECKO_BASE}/coins/{cid}/history", params={"date": d, "localization": "false"})
            if r.status_code == 200:
                old = r.json().get("market_data", {}).get("current_price", {}).get("usd")
                if old and old > 0 and (cur/old) >= 2.0:
                    coin["change_21d"] = round((cur/old - 1)*100, 1)
                    coin["price_21d_ago"] = old
                    doubled.append(coin)
        except:
            continue
    return doubled

# ---------- HTML Generation ----------
def generate_html(signals, momentum, trending, anomalies, doublers):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    mom_names = [c["name"] for c in momentum[:15]]
    mom_changes = [c.get("price_change_percentage_7d_in_currency") or 0 for c in momentum[:15]]
    anom_names = [a["name"] for a in anomalies[:10]]
    anom_ratios = [a["ratio"] for a in anomalies[:10]]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Daily Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0b0e11; color:#eaecef; font-family:'Segoe UI', system-ui, sans-serif; padding:20px; }}
  .container {{ max-width:1200px; margin:auto; }}
  h1 {{ text-align:center; font-size:2rem; margin-bottom:5px; }}
  .date {{ text-align:center; color:#848e9c; margin-bottom:30px; }}
  .card {{ background:#1e2329; border-radius:12px; padding:20px; margin-bottom:24px; box-shadow:0 4px 20px rgba(0,0,0,0.5); }}
  .card h2 {{ font-size:1.3rem; margin-bottom:15px; display:flex; align-items:center; gap:8px; }}
  .grid-2 {{ display:grid; grid-template-columns: 1fr 1fr; gap:20px; }}
  .signal-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap:12px; }}
  .signal-item {{ background:#2b3139; padding:12px; border-radius:8px; text-align:center; }}
  .signal-item .label {{ font-size:0.8rem; color:#848e9c; }}
  .signal-item .value {{ font-size:1.4rem; font-weight:bold; }}
  .long {{ color:#0ecb81; }}
  .short {{ color:#f6465d; }}
  table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
  th, td {{ padding:10px 8px; text-align:left; border-bottom:1px solid #2b3139; }}
  th {{ color:#848e9c; font-weight:500; font-size:0.9rem; }}
  td {{ font-size:0.95rem; }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.75rem; font-weight:bold; }}
  .tag-bullish {{ background:#0ecb8120; color:#0ecb81; }}
  .tag-bearish {{ background:#f6465d20; color:#f6465d; }}
  canvas {{ max-height:300px; }}
  .no-data {{ text-align:center; color:#5e6673; padding:30px; }}
  .footer {{ text-align:center; color:#5e6673; font-size:0.8rem; margin-top:20px; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔮 Crypto Intelligence Dashboard</h1>
  <div class="date">📅 {now_utc}</div>

  <div class="card">
    <h2>🚦 Liquidity Sweep Trade Signals</h2>
    {generate_signal_html(signals)}
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>🐋 Whale Volume Anomalies</h2>
      {generate_anomaly_html(anomalies, anom_names, anom_ratios)}
    </div>
    <div class="card">
      <h2>🔥 Trending Coins (Search Surge)</h2>
      {generate_trending_html(trending)}
    </div>
  </div>

  <div class="card">
    <h2>🚀 Top Momentum Coins (7d)</h2>
    {generate_momentum_html(momentum, mom_names, mom_changes)}
  </div>

  <div class="card">
    <h2>💎 Coins That Doubled (21 days)</h2>
    {generate_doublers_html(doublers)}
  </div>

  <div class="footer">⚠️ Not financial advice. For educational purposes only.</div>
</div>
<script>
{generate_chart_js(anom_names, anom_ratios, mom_names, mom_changes)}
</script>
</body>
</html>"""
    return html

def generate_signal_html(signals):
    if not signals:
        return '<div class="no-data">🛑 No valid setup today. Waiting for sweep + strong R:R.</div>'
    html = '<div class="signal-grid">'
    for s in signals:
        dir_class = "long" if s["direction"]=="LONG" else "short"
        emoji = "🟢" if s["direction"]=="LONG" else "🔴"
        html += f'''<div class="signal-item">
          <div class="label">{emoji} {s["pair"]} {s["direction"]}</div>
          <div class="value {dir_class}">Entry: {s["entry"]}</div>
          <div class="label">🛑 Stop: {s["stop"]}</div>
          <div class="label">🎯 TP1: {s["tp1"]}</div>
          <div class="label">🎯 TP2: {s["tp2"]}</div>
        </div>'''
    html += '</div>'
    return html

def generate_anomaly_html(anomalies, names, ratios):
    if not anomalies:
        return '<div class="no-data">🐟 No abnormal volume detected in top 100.</div>'
    html = '<div style="display:flex; gap:20px; flex-wrap:wrap;"><div style="flex:1; min-width:250px;"><canvas id="anomalyChart"></canvas></div><div style="flex:1;"><table>'
    html += '<tr><th>Coin</th><th>Ratio</th><th>7d Chg</th></tr>'
    for a in anomalies[:10]:
        html += f"<tr><td>{a['name']} ({a['symbol']})</td><td>{a['ratio']:.1f}x</td><td class='{'long' if a['change_7d']>0 else 'short'}'>{a['change_7d']:+.1f}%</td></tr>"
    html += '</table></div></div>'
    return html

def generate_trending_html(trending):
    if not trending:
        return '<div class="no-data">📉 No trending data available.</div>'
    html = '<table><tr><th>Coin</th><th>Rank</th><th>Score</th></tr>'
    for t in trending[:10]:
        html += f"<tr><td>{t['name']} ({t['symbol']})</td><td>{t.get('rank','?')}</td><td>{t.get('score','?')}</td></tr>"
    html += '</table>'
    return html

def generate_momentum_html(momentum, names, changes):
    if not momentum:
        return '<div class="no-data">📊 No momentum data.</div>'
    html = '<div style="display:flex; gap:20px; flex-wrap:wrap;"><div style="flex:2; min-width:400px;"><canvas id="momentumChart"></canvas></div><div style="flex:1; min-width:250px;"><table>'
    html += '<tr><th>#</th><th>Coin</th><th>7d Chg</th></tr>'
    for i, c in enumerate(momentum[:15], 1):
        chg = c.get("price_change_percentage_7d_in_currency") or 0
        html += f"<tr><td>{i}</td><td>{c['name']} ({c['symbol'].upper()})</td><td class='{'long' if chg>0 else 'short'}'>{chg:+.1f}%</td></tr>"
    html += '</table></div></div>'
    return html

def generate_doublers_html(doublers):
    if not doublers:
        return '<div class="no-data">💤 No top-100 coin doubled in the last 21 days.</div>'
    html = '<table><tr><th>Coin</th><th>Price 21d Ago</th><th>Now</th><th>Gain</th></tr>'
    for c in doublers:
        html += f"<tr><td>{c['name']} ({c['symbol'].upper()})</td><td>${c['price_21d_ago']:.4f}</td><td>${c['current_price']:.4f}</td><td class='long'>+{c['change_21d']}%</td></tr>"
    html += '</table>'
    return html

def generate_chart_js(anom_names, anom_ratios, mom_names, mom_changes):
    return f"""
document.addEventListener('DOMContentLoaded', function () {{
  const anomCtx = document.getElementById('anomalyChart');
  if (anomCtx) {{
    new Chart(anomCtx, {{
      type: 'bar',
      data: {{
        labels: {json.dumps(anom_names)},
        datasets: [{{
          label: 'Volume Spike Ratio (x)',
          data: {json.dumps(anom_ratios)},
          backgroundColor: anom_ratios.map(r => r > 3 ? '#f6465d' : '#f0b90b'),
          borderRadius: 4
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#2b3139' }} }}
        }}
      }}
    }});
  }}

  const momCtx = document.getElementById('momentumChart');
  if (momCtx) {{
    new Chart(momCtx, {{
      type: 'bar',
      data: {{
        labels: {json.dumps(mom_names)},
        datasets: [{{
          label: '7d Change %',
          data: {json.dumps(mom_changes)},
          backgroundColor: mom_changes.map(v => v >= 0 ? '#0ecb81' : '#f6465d'),
          borderRadius: 4,
          yAxisID: 'y'
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: true, position: 'top' }} }},
        scales: {{
          y: {{ beginAtZero: true, grid: {{ color: '#2b3139' }}, title: {{ display: true, text: '% Change' }} }}
        }}
      }}
    }});
  }}
}});
"""

# ---------- Main ----------
def main():
    print("🔍 Generating crypto signals...")
    signals = []
    for pair in PAIRS:
        sig = generate_signal(pair)
        if sig:
            signals.append(sig)

    print("📊 Fetching market data...")
    coins = get_top_100()
    trending = get_trending()
    anomalies = []
    for coin in coins:
        ratio, avg = get_volume_anomaly(coin)
        if ratio >= VOLUME_SPIKE_THRESHOLD:
            anomalies.append({
                "name": coin["name"],
                "symbol": coin["symbol"].upper(),
                "current_vol": coin.get("total_volume", 0),
                "avg_vol": avg,
                "ratio": round(ratio, 2),
                "change_7d": coin.get("price_change_percentage_7d_in_currency") or 0
            })
    anomalies.sort(key=lambda x: x["ratio"], reverse=True)
    momentum = sorted(coins, key=lambda x: x.get("price_change_percentage_7d_in_currency") or 0, reverse=True)[:15]
    doublers = get_doublers(coins)

    print("🎨 Generating dashboard HTML...")
    html = generate_html(signals, momentum, trending, anomalies, doublers)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Dashboard saved to {OUTPUT_HTML}")

if __name__ == "__main__":
    main()
