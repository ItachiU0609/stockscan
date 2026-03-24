"""
Penny Stock Screener — FastAPI Backend (Alpha Vantage)
=======================================================
Install:  pip install fastapi uvicorn requests
Run:      uvicorn main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import datetime, timedelta
import requests
import time

app = FastAPI(title="Penny Stock Screener")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def root():
    return FileResponse("index.html")


# ── Config ──────────────────────────────────────
AV_KEY             = "9BMIN6QQZDHBROA9"
AV_BASE            = "https://www.alphavantage.co/query"

# ── Thresholds ──────────────────────────────────
MAX_PRICE          = 5.00
MIN_AVG_VOLUME     = 500_000
MIN_MARKET_CAP     = 10_000_000
VOLUME_SPIKE_RATIO = 2.5
MIN_PRICE_CHANGE   = 0.03
RSI_LOW            = 40
RSI_HIGH           = 65
MAX_SPREAD_PCT     = 0.05
PUMP_THRESHOLD     = 0.50
STOP_LOSS_PCT      = 0.15
TAKE_PROFIT_PCT    = 0.30


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def moving_average(prices, n):
    if len(prices) < n:
        return None
    return round(sum(prices[-n:]) / n, 4)


def av_get(params: dict):
    params["apikey"] = AV_KEY
    r = requests.get(AV_BASE, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper().strip()

    # ── Daily price history ──────────────────────
    try:
        daily = av_get({
            "function": "TIME_SERIES_DAILY",
            "symbol": ticker,
            "outputsize": "compact"
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Price fetch failed: {e}")

    # Log exactly what Alpha Vantage returned for debugging
    print("Alpha Vantage response keys:", list(daily.keys()))
    print("Full response:", daily)

    if "Time Series (Daily)" not in daily:
        msg = daily.get("Note") or daily.get("Information") or daily.get("message") or str(daily)
        raise HTTPException(status_code=404, detail=f"Alpha Vantage error: {msg}")

    ts = daily["Time Series (Daily)"]
    dates   = sorted(ts.keys(), reverse=True)[:63]
    closes  = [float(ts[d]["4. close"]) for d in dates]
    volumes = [float(ts[d]["5. volume"]) for d in dates]

    price      = round(closes[0], 4)
    prev_close = closes[1]
    today_vol  = int(volumes[0])
    avg_vol_30 = sum(volumes[:30]) / min(len(volumes), 30)
    vol_ratio  = round(today_vol / avg_vol_30, 2) if avg_vol_30 > 0 else 0
    price_chg  = (price - prev_close) / prev_close if prev_close else 0
    price_5d   = closes[5] if len(closes) >= 6 else closes[-1]
    gain_5d    = (price - price_5d) / price_5d if price_5d else 0
    rsi        = compute_rsi(list(reversed(closes)))
    ma10       = moving_average(list(reversed(closes)), 10)
    ma30       = moving_average(list(reversed(closes)), 30)

    # ── Company overview ─────────────────────────
    time.sleep(1)
    try:
        overview = av_get({
            "function": "OVERVIEW",
            "symbol": ticker
        })
        market_cap = float(overview.get("MarketCapitalization", 0) or 0)
    except Exception:
        market_cap = 0

    spread_pct = 0.0

    # ── News ─────────────────────────────────────
    time.sleep(1)
    try:
        news_data = av_get({
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "limit": "5"
        })
        feed = news_data.get("feed", [])
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y%m%dT%H%M%S")
        recent_news = [
            {"title": n.get("title", ""), "url": n.get("url", "")}
            for n in feed
            if n.get("time_published", "") >= cutoff
        ][:3]
    except Exception:
        recent_news = []

    catalyst_present = len(recent_news) > 0

    s1_price  = price < MAX_PRICE
    s1_vol    = avg_vol_30 > MIN_AVG_VOLUME
    s1_mcap   = market_cap > MIN_MARKET_CAP
    passes_screen = s1_price and s1_vol and s1_mcap

    vol_spike  = vol_ratio >= VOLUME_SPIKE_RATIO
    price_up   = price_chg >= MIN_PRICE_CHANGE
    step2_pass = vol_spike and price_up

    above_ma10 = (price > ma10) if ma10 else False
    ma10_vs_30 = (ma10 > ma30)  if (ma10 and ma30) else False
    rsi_ok     = (RSI_LOW <= rsi <= RSI_HIGH) if rsi else False
    step3_pass = above_ma10 and ma10_vs_30 and rsi_ok

    no_pump   = gain_5d <= PUMP_THRESHOLD
    disqualifiers = []
    if not no_pump:
        disqualifiers.append(f"Possible pump-and-dump (+{gain_5d*100:.0f}% in 5 days)")

    signal_strength = 0
    if passes_screen:
        if step2_pass:        signal_strength += 2
        if step3_pass:        signal_strength += 2
        if catalyst_present:  signal_strength += 3

    buy_signal = signal_strength >= 5 and len(disqualifiers) == 0 and passes_screen

    return {
        "ticker": ticker,
        "price": price,
        "price_change_pct": round(price_chg * 100, 2),
        "market_cap": market_cap,
        "avg_volume_30d": round(avg_vol_30),
        "today_volume": today_vol,
        "volume_ratio": vol_ratio,
        "rsi": rsi,
        "ma10": ma10,
        "ma30": ma30,
        "spread_pct": round(spread_pct * 100, 2),
        "gain_5d_pct": round(gain_5d * 100, 2),
        "steps": {
            "screen": {
                "passed": passes_screen,
                "checks": {
                    "price_ok":  s1_price,
                    "volume_ok": s1_vol,
                    "mcap_ok":   s1_mcap,
                }
            },
            "volume_surge": {
                "passed": step2_pass,
                "checks": {
                    "vol_spike": vol_spike,
                    "price_up":  price_up,
                }
            },
            "momentum": {
                "passed": step3_pass,
                "checks": {
                    "above_ma10": above_ma10,
                    "ma10_vs_30": ma10_vs_30,
                    "rsi_ok":     rsi_ok,
                }
            },
            "catalyst": {
                "passed": catalyst_present,
                "news":   recent_news,
            },
            "risk": {
                "passed": len(disqualifiers) == 0,
                "disqualifiers": disqualifiers,
                "checks": {
                    "spread_ok": True,
                    "no_pump":   no_pump,
                }
            }
        },
        "signal_strength": signal_strength,
        "max_signal": 7,
        "buy_signal": buy_signal,
        "trade": {
            "entry":       price,
            "stop_loss":   round(price * (1 - STOP_LOSS_PCT), 4),
            "take_profit": round(price * (1 + TAKE_PROFIT_PCT), 4),
        }
    }
