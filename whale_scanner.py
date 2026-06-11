import requests
import json
from datetime import datetime, timezone, timedelta

# ------- CONFIG -------
WHALE_ALERT_API_KEY = ""  # Get free key at https://whale-alert.io/ (optional but recommended)
MIN_WHALE_USD = 500_000   # Minimum transfer value to flag
EXCHANGE_WALLETS = [
    "binance", "coinbase", "kraken", "bitfinex", "huobi", "okx", "bybit",
    "kucoin", "gate.io", "crypto.com"
]
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
OUTPUT_FILE = "whale_report.md"
# ------- END CONFIG -------

def get_top_100_coins():
    """Return list of top 100 coins by market cap."""
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 100,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "7d,30d"
    }
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return []
    return r.json()

def get_coin_history(coin_id, days_ago=21):
    """Fetch price from days_ago ago."""
    url = f"{COINGECKO_BASE}/coins/{coin_id}/history"
    date_str = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%d-%m-%Y")
    params = {"date": date_str, "localization": "false"}
    r = requests.get(url, params=params)
    if r.status_code != 200:
        return None
    data = r.json()
    if "market_data" in data:
        return data["market_data"]["current_price"]["usd"]
    return None

def find_doublers(coins):
    """Identify coins that doubled in value in last 21 days."""
    doubled = []
    for coin in coins:
        coin_id = coin["id"]
        current_price = coin["current_price"]
        if current_price is None:
            continue
        old_price = get_coin_history(coin_id, 21)
        if old_price and old_price > 0 and (current_price / old_price) >= 2.0:
            coin["change_21d"] = round((current_price / old_price - 1) * 100, 1)
            coin["price_21d_ago"] = old_price
            doubled.append(coin)
    return doubled

def get_momentum_coins(coins):
    """Filter and rank by momentum: high 7d change + high volume/mcap ratio."""
    ranked = []
    for coin in coins:
        change_7d = coin.get("price_change_percentage_7d_in_currency", 0) or 0
        total_volume = coin.get("total_volume", 0) or 0
        market_cap = coin.get("market_cap", 0) or 1
        vol_mcap_ratio = total_volume / market_cap if market_cap else 0
        # Composite score: 70% price change, 30% volume ratio (normalized)
        # Simple ranking: sort by 7d change descending, take top 15 then by vol ratio
        ranked.append({**coin, "vol_mcap_ratio": vol_mcap_ratio})
    # Sort by 7d change (high) then vol_mcap_ratio (high)
    ranked.sort(key=lambda x: (x.get("price_change_percentage_7d_in_currency", 0) or 0, x["vol_mcap_ratio"]), reverse=True)
    return ranked[:15]

def get_whale_transactions():
    """Fetch recent large transactions from Whale Alert if key provided."""
    if not WHALE_ALERT_API_KEY:
        return []
    url = "https://api.whale-alert.io/v1/transactions"
    params = {
        "api_key": WHALE_ALERT_API_KEY,
        "min_value": MIN_WHALE_USD,
        "limit": 100,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("transactions", [])
    except:
        pass
    return []

def analyze_whale_activity(transactions):
    """Group large transfers by symbol and detect exchange inflows/outflows."""
    coin_activity = {}
    for tx in transactions:
        symbol = tx.get("symbol", "UNKNOWN").upper()
        amount_usd = tx.get("amount_usd", 0)
        from_wallet = tx.get("from", {}).get("owner", "").lower()
        to_wallet = tx.get("to", {}).get("owner", "").lower()
        is_exchange = any(ex in from_wallet or ex in to_wallet for ex in EXCHANGE_WALLETS)
        if symbol not in coin_activity:
            coin_activity[symbol] = {"count": 0, "total_usd": 0, "exchange_txs": 0}
        coin_activity[symbol]["count"] += 1
        coin_activity[symbol]["total_usd"] += amount_usd
        if is_exchange:
            coin_activity[symbol]["exchange_txs"] += 1
    # Filter for coins with at least 3 large transfers, sort by total value
    heavy = {k: v for k, v in coin_activity.items() if v["count"] >= 3}
    return sorted(heavy.items(), key=lambda x: x[1]["total_usd"], reverse=True)

def generate_report(momentum_coins, doubled_coins, whale_data):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(OUTPUT_FILE, "w") as f:
        f.write(f"# Whale & Momentum Scanner ({now})\n\n")
        f.write("*Disclaimer: Not financial advice. On-chain data may be delayed. Always verify.*\n\n")

        # Whale Section
        f.write("## 🐋 Whale & Institutional Watch\n")
        if not whale_data:
            f.write("_No Whale Alert API key provided or no large transactions detected._\n")
            f.write("> Get a free key at [whale-alert.io](https://whale-alert.io/) and set it in the script.\n\n")
        else:
            f.write("| Coin | Large TXs (24h) | Total USD Volume | Exchange-Related |\n")
            f.write("|------|------------------|-------------------|-------------------|\n")
            for symbol, stats in whale_data[:10]:
                f.write(f"| {symbol} | {stats['count']} | ${stats['total_usd']:,.0f} | {stats['exchange_txs']} |\n")
            f.write("\n")

        # Momentum Section
        f.write("## 🚀 Top Momentum Coins (7d)\n")
        f.write("| # | Coin | Price | 7d Change | Vol/MCap Ratio | Market Cap |\n")
        f.write("|---|------|-------|-----------|----------------|------------|\n")
        for i, coin in enumerate(momentum_coins, 1):
            change = coin.get("price_change_percentage_7d_in_currency", 0) or 0
            price = coin.get("current_price", 0)
            mcap = coin.get("market_cap", 0)
            vol_ratio = coin.get("vol_mcap_ratio", 0)
            f.write(f"| {i} | {coin['name']} ({coin['symbol'].upper()}) | ${price:.4f} | {change:.1f}% | {vol_ratio:.4f} | ${mcap:,.0f} |\n")
        f.write("\n")

        # Doublers Section
        f.write("## 💎 Coins That Doubled (Last 21 Days)\n")
        if not doubled_coins:
            f.write("_No coins from the top 100 have doubled in the last 3 weeks._\n")
        else:
            f.write("| Coin | Current Price | Price 21d Ago | Change | Market Cap |\n")
            f.write("|------|---------------|---------------|--------|------------|\n")
            for coin in doubled_coins:
                f.write(f"| {coin['name']} ({coin['symbol'].upper()}) | ${coin['current_price']:.4f} | ${coin.get('price_21d_ago', 0):.4f} | {coin.get('change_21d', 0):.1f}% | ${coin.get('market_cap', 0):,.0f} |\n")
        f.write("\n---\n")
        f.write("*Signals generated using CoinGecko and Whale Alert APIs.*\n")

def main():
    print("Fetching top 100 coins...")
    top_coins = get_top_100_coins()
    if not top_coins:
        print("CoinGecko API error.")
        return

    print("Finding coins that doubled...")
    doubled = find_doublers(top_coins)

    print("Ranking momentum coins...")
    momentum = get_momentum_coins(top_coins)

    print("Fetching whale transactions...")
    whale_tx = get_whale_transactions()
    whale_data = analyze_whale_activity(whale_tx)

    print("Writing report...")
    generate_report(momentum, doubled, whale_data)
    print(f"Report saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
