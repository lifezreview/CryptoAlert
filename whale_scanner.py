import requests
from datetime import datetime, timezone, timedelta

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
VOLUME_SPIKE_THRESHOLD = 2.0
TOP_N_MOMENTUM = 15

def get_top_100_coins():
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 100,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "7d,30d",
    }
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return []
    return r.json()

def get_coin_volume_history(coin_id, days=7):
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days}
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return []
    data = r.json()
    return data.get("total_volumes", [])

def get_volume_anomaly(coin):
    coin_id = coin["id"]
    current_volume = coin.get("total_volume", 0)
    if not current_volume:
        return 0, 0
    volume_history = get_coin_volume_history(coin_id, days=7)
    if len(volume_history) < 2:
        return 0, 0
    recent_volumes = [v[1] for v in volume_history[:-1]]
    if not recent_volumes:
        return 0, 0
    avg_volume = sum(recent_volumes) / len(recent_volumes)
    if avg_volume == 0:
        return 0, 0
    ratio = current_volume / avg_volume
    return ratio, avg_volume

def get_trending_coins():
    url = f"{COINGECKO_BASE}/search/trending"
    r = requests.get(url)
    if r.status_code != 200:
        return []
    data = r.json()
    coins = data.get("coins", [])
    trending_list = []
    for item in coins:
        coin_data = item.get("item", {})
        trending_list.append({
            "id": coin_data.get("id"),
            "name": coin_data.get("name"),
            "symbol": coin_data.get("symbol").upper(),
            "market_cap_rank": coin_data.get("market_cap_rank"),
            "score": coin_data.get("score"),
        })
    return trending_list

def find_doublers(coins):
    doubled = []
    for coin in coins:
        coin_id = coin["id"]
        current_price = coin["current_price"]
        if not current_price:
            continue
        date_str = (datetime.utcnow() - timedelta(days=21)).strftime("%d-%m-%Y")
        history_url = f"{COINGECKO_BASE}/coins/{coin_id}/history"
        params = {"date": date_str, "localization": "false"}
        try:
            r = requests.get(history_url, params=params)
            if r.status_code == 200:
                data = r.json()
                old_price = data.get("market_data", {}).get("current_price", {}).get("usd")
                if old_price and old_price > 0 and (current_price / old_price) >= 2.0:
                    coin["change_21d"] = round((current_price / old_price - 1) * 100, 1)
                    coin["price_21d_ago"] = old_price
                    doubled.append(coin)
        except:
            continue
    return doubled

def generate_report(momentum_coins, trending_coins, doubled_coins, volume_anomalies):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open("whale_report.md", "w") as f:
        f.write(f"# Whale & Momentum Scanner ({now})\n\n")
        f.write("*Disclaimer: Not financial advice. Data from CoinGecko.*\n\n")

        f.write("## 🔍 Trending Coins (Search & Interest Surge)\n")
        if not trending_coins:
            f.write("_No trending data available._\n")
        else:
            f.write("| Coin | Symbol | Market Cap Rank | Trending Score |\n")
            f.write("|------|--------|-----------------|----------------|\n")
            for coin in trending_coins[:15]:
                f.write(f"| {coin['name']} | {coin['symbol']} | {coin.get('market_cap_rank', 'N/A')} | {coin.get('score', 0):.1f} |\n")
        f.write("\n")

        f.write("## 📊 Abnormal Volume (Potential Whale Activity)\n")
        if not volume_anomalies:
            f.write("_No significant volume anomalies in the top 100._\n")
        else:
            f.write("| Coin | 24h Volume | 7d Avg Volume | Spike Ratio | Price Change (7d) |\n")
            f.write("|------|------------|---------------|-------------|-------------------|\n")
            for item in volume_anomalies[:10]:
                f.write(f"| {item['name']} ({item['symbol']}) | ${item['current_vol']:,.0f} | ${item['avg_vol']:,.0f} | {item['ratio']:.1f}x | {item['change_7d']:.1f}% |\n")
        f.write("\n")

        f.write("## 🚀 Top Momentum Coins (7d)\n")
        if not momentum_coins:
            f.write("_No momentum data._\n")
        else:
            f.write("| # | Coin | Price | 7d Change | Vol/MCap |\n")
            f.write("|---|------|-------|-----------|----------|\n")
            for i, coin in enumerate(momentum_coins[:TOP_N_MOMENTUM], 1):
                price = coin.get("current_price", 0)
                change = coin.get("price_change_percentage_7d_in_currency") or 0
                mcap = coin.get("market_cap", 1)
                volume = coin.get("total_volume", 0)
                vol_mcap = volume / mcap if mcap else 0
                f.write(f"| {i} | {coin['name']} ({coin['symbol'].upper()}) | ${price:.4f} | {change:.1f}% | {vol_mcap:.4f} |\n")
        f.write("\n")

        f.write("## 💎 Coins That Doubled (Last 21 Days)\n")
        if not doubled_coins:
            f.write("_No coins from the top 100 have doubled in the last 3 weeks._\n")
        else:
            f.write("| Coin | Current Price | Price 21d Ago | Change | Market Cap |\n")
            f.write("|------|---------------|---------------|--------|------------|\n")
            for coin in doubled_coins:
                f.write(f"| {coin['name']} ({coin['symbol'].upper()}) | ${coin['current_price']:.4f} | ${coin.get('price_21d_ago', 0):.4f} | {coin.get('change_21d', 0):.1f}% | ${coin.get('market_cap', 0):,.0f} |\n")
        f.write("\n---\n")
        f.write("*Signals generated using free CoinGecko data.*\n")

def main():
    print("Fetching top 100 coins...")
    top_coins = get_top_100_coins()
    if not top_coins:
        print("Failed to fetch market data.")
        return

    print("Fetching trending coins...")
    trending = get_trending_coins()

    print("Scanning for volume anomalies (may take a minute)...")
    volume_spikes = []
    for coin in top_coins:
        ratio, avg_vol = get_volume_anomaly(coin)
        if ratio >= VOLUME_SPIKE_THRESHOLD:
            volume_spikes.append({
                "name": coin["name"],
                "symbol": coin["symbol"].upper(),
                "current_vol": coin.get("total_volume", 0),
                "avg_vol": avg_vol,
                "ratio": round(ratio, 2),
                "change_7d": coin.get("price_change_percentage_7d_in_currency") or 0,
            })
    volume_spikes.sort(key=lambda x: x["ratio"], reverse=True)

    print("Ranking momentum coins...")
    momentum = sorted(top_coins, key=lambda x: x.get("price_change_percentage_7d_in_currency") or 0, reverse=True)

    print("Finding coins that doubled in 21 days (may take a couple of minutes)...")
    doubled = find_doublers(top_coins)

    print("Generating report...")
    generate_report(momentum, trending, doubled, volume_spikes)
    print("Report saved to whale_report.md")

if __name__ == "__main__":
    main()
