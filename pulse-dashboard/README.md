# PULSE — Live Markets Terminal

A standalone, real-time markets dashboard covering **Indian indices, US indices, cryptocurrency and forex** — with its own serverless backend so it never depends on flaky public CORS proxies.

| Market | Instruments | Source |
| --- | --- | --- |
| 🇮🇳 Indian | Nifty 50, Sensex, Bank Nifty, Nifty IT | Yahoo Finance |
| 🇺🇸 US | S&P 500, Dow Jones, Nasdaq, Russell 2000, VIX | Yahoo Finance |
| ₿ Crypto | BTC, ETH, SOL, BNB, XRP, ADA, DOGE, TRX | CoinGecko |
| 💱 Forex | USD/INR, EUR/INR, GBP/INR, JPY/INR, EUR/USD, GBP/USD, DXY | Yahoo Finance |

Live prices, 24h change, sparklines, a scrolling ticker, per-exchange open/closed status, tab filters, and 60-second auto-refresh.

## How it works

```
Browser ──> /api/yahoo?symbol=^NSEI   ──> Yahoo Finance   (edge-cached ~15s)
Browser ──> /api/crypto               ──> CoinGecko        (edge-cached ~20s)
```

- `api/yahoo.js` and `api/crypto.js` are **Vercel serverless functions** that fetch upstream data server-side and add CORS + cache headers. Because responses are edge-cached, all visitors share one upstream request per cache window, so you stay well within free rate limits.
- `index.html` is a fully static, dependency-free page. It calls the `/api/*` backend first and falls back to public CORS proxies only when opened as a local `file://` preview (no backend present).

## Project layout

```
pulse-dashboard/
├── index.html        # the dashboard (static, no build step)
├── api/
│   ├── yahoo.js      # serverless proxy for indices & forex
│   └── crypto.js     # serverless proxy for crypto
├── vercel.json       # CORS + clean-URL config
├── package.json      # metadata (Node >= 18)
└── README.md
```

## Deploy to Vercel

### Option A — Vercel CLI (fastest)

```bash
npm i -g vercel          # once
cd pulse-dashboard
vercel                   # preview deploy — follow the prompts
vercel --prod            # production deploy
```

### Option B — Import the Git repo in the Vercel dashboard

1. Push this repo to GitHub (already done on the working branch).
2. In Vercel: **Add New… → Project → Import** this repository.
3. Set **Root Directory** to `pulse-dashboard`.
4. Framework preset: **Other** (no build command needed — it's static + functions).
5. Click **Deploy**.

That's it. `index.html` is served statically and the functions are auto-detected under `/api`.

## Optional — higher CoinGecko limits

The free CoinGecko endpoint is fine for normal traffic. For heavier use, create a free **CoinGecko Demo API key** and add it in Vercel:

- **Project → Settings → Environment Variables →** `COINGECKO_API_KEY = <your key>`

`api/crypto.js` picks it up automatically.

## Notes & disclaimer

Prices come from free public feeds and may be delayed by 15+ minutes. This dashboard is for information only and is not investment advice. Verify with an authorised source before trading.
