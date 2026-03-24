"""
Penny Stock Screener — FastAPI Backend
======================================
Install:  pip install fastapi uvicorn yfinance
Run:      uvicorn main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from datetime import datetime, timedelta
import yfinance as yf

app = FastAPI(title="Penny Stock Screener")

# Allow all origins for local / hosted use
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend at /
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
def root():
    return FileResponse("index.html")


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


@app.get("/api/analyze/{ticker}")
def analyze(ticker: str):
    ticker = ticker.upper().strip()

    try:
        tk   = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="3mo")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if hist.empty or len(hist) < 2:
        raise HTTPException(status_code=404, detail=f"No data found for {ticker}")

    closes  = list(hist["Close"])
    volumes = list(hist["Volume"])

    price       = round(closes[-1], 4)
    prev_close  = closes[-2]
    today_vol   = int(volumes[-1])
    avg_vol_30  = sum(volumes[-30:]) / min(len(volumes), 30)
    market_cap  = info.get("marketCap", 0) or 0
    bid         = info.get("bid", 0) or 0
    ask         = info.get("ask", price) or price
    spread_pct  = ((ask - bid) / ask) if ask > 0 else 0
    price_chg   = (price - prev_close) / prev_close if prev_close else 0
    price_5d    = closes[-6] if len(closes) >= 6 else closes[0]
    gain_5d     = (price - price_5d) / price_5d if price_5d else 0
    rsi         = compute_rsi(closes)
    ma10        = moving_average(closes, 10)
    ma30        = moving_average(closes, 30)
    vol_ratio   = round(today_vol / avg_vol_30, 2) if avg_vol_30 > 0 else 0

    # ── Step results ────────────────────────────
    s1_price  = price < MAX_PRICE
    s1_vol    = avg_vol_30 > MIN_AVG_VOLUME
    s1_mcap   = market_cap > MIN_MARKET_CAP
    passes_screen = s1_price and s1_vol and s1_mcap

    vol_spike   = vol_ratio >= VOLUME_SPIKE_RATIO
    price_up    = price_chg >= MIN_PRICE_CHANGE
    step2_pass  = vol_spike and price_up

    above_ma10  = (price > ma10)  if ma10  else False
    ma10_vs_30  = (ma10 > ma30)   if (ma10 and ma30) else False
    rsi_ok      = (RSI_LOW <= rsi <= RSI_HIGH) if rsi else False
    step3_pass  = above_ma10 and ma10_vs_30 and rsi_ok

    news = tk.news or []
    recent_news = [
        {"title": n.get("title", ""), "url": n.get("link", "")}
        for n in news
        if n.get("providerPublishTime", 0) >
           (datetime.now() - timedelta(days=7)).timestamp()
    ][:3]
    catalyst_present = len(recent_news) > 0

    spread_ok = spread_pct <= MAX_SPREAD_PCT
    no_pump   = gain_5d <= PUMP_THRESHOLD
    disqualifiers = []
    if not spread_ok:
        disqualifiers.append(f"Bid/ask spread too wide ({spread_pct*100:.1f}%)")
    if not no_pump:
        disqualifiers.append(f"Possible pump-and-dump (+{gain_5d*100:.0f}% in 5 days)")

    signal_strength = 0
    if passes_screen:
        if step2_pass:      signal_strength += 2
        if step3_pass:      signal_strength += 2
        if catalyst_present: signal_strength += 3

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

        # Step results
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
                    "above_ma10":  above_ma10,
                    "ma10_vs_30":  ma10_vs_30,
                    "rsi_ok":      rsi_ok,
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
                    "spread_ok": spread_ok,
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
