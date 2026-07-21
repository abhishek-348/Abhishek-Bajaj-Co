# Indian Options-Selling Trading Bot — Build Plan & Strategy Guide

> Everything for the trading-bot project lives in this file, separate from `index.html` / `indexCAAB.html`.
> Read the **Reality Check** section first. It determines how the whole bot is designed.

---

## 0. Reality Check — Is 5% / week achievable?

**No — not as a consistent, compounding target.**

- 5%/week compounded ≈ **~1,100%+ per year**. The best quant funds in history do ~40%/year.
- Option **selling** wins most weeks (collect premium) but the losses are rare, sudden, and huge — one gap day can erase months. This is "picking up pennies in front of a steamroller."
- Selling options = **limited profit, very large (near-unlimited) loss**. That is the hardest risk shape to survive as a beginner.

**Realistic targets to design around instead:**

| Target | Verdict |
|---|---|
| 5% / week, compounding | Fantasy. High probability of eventual ruin. |
| 2–4% / **month**, risk-controlled | Genuinely good. Still hard. |
| Don't blow up in the first year | The real goal. Survival first, returns second. |

The rest of this plan optimizes for **survival**. A bot that never blows up beats one that makes 5%/week for 10 weeks then goes to zero.

---

## 1. Phased Build Plan

**Do not skip phases. Do not send real money until Phase 4 is green for weeks.**

### Phase 0 — Learn & Decide (before any code)
- Confirm you understand: margin/SPAN, expiry mechanics (weekly/monthly), assignment, STT on exercised options, lot sizes (NIFTY 75, BANKNIFTY 35 — verify current, they change).
- Pick a broker with a stable API (see §2).
- Open a separate trading account funded with **only money you can lose**.

### Phase 1 — Data & Connectivity
- Broker API auth + token refresh (most Indian broker tokens expire daily — automate the login).
- Live LTP / quotes for index + option chain.
- Historical data source for backtesting (broker historical API, or a paid provider).
- **Deliverable:** a script that prints the live NIFTY option chain every few seconds.

### Phase 2 — Strategy Engine (paper only)
- Encode ONE strategy (start with a defined-risk spread, §3).
- Entry logic, exit logic, position sizing — all in code, no manual decisions.
- **Deliverable:** bot generates signals and logs "would have entered/exited" — no orders sent.

### Phase 3 — Backtesting & Forward Paper Trading
- Backtest on 3–5 years including crash periods (COVID Mar-2020, 2015, 2024 election day, etc.).
- Then forward-paper-trade live for **at least 4–8 weeks**. Real ticks, fake money.
- Track: win rate, avg win, avg loss, **max drawdown**, worst single day.
- **Deliverable:** an honest equity curve you'd be comfortable risking money on.

### Phase 4 — Live, Tiny
- Go live with the **smallest possible size** (1 lot).
- Compare live fills vs paper (slippage is real and eats edge).
- Scale up only after the live curve matches the paper curve.

### Phase 5 — Hardening & Ops
- Auto square-off on max daily loss.
- Restart/crash recovery (bot must know its open positions after a reboot).
- Alerts (Telegram/email) on every trade, error, and kill-switch trigger.
- Kill switch you can hit from your phone.

---

## 2. Tech Stack (India-specific)

| Piece | Options |
|---|---|
| Language | **Python** (best ecosystem for this) |
| Broker API | **Zerodha Kite Connect** (most popular, paid), **Fyers**, **Dhan**, **Angel One SmartAPI** (free), **Upstox** |
| Realtime data | Broker WebSocket feed |
| Backtesting | `backtrader`, `vectorbt`, or a custom loop; option data from broker/NSE |
| Storage | SQLite/Postgres for trades + logs; a flat CSV to start |
| Scheduling | A always-on host (VPS) — the bot must run 09:15–15:30 IST reliably |
| Alerts | Telegram bot API |

> Note: SEBI/broker rules around fully-automated retail algo trading are evolving. Check your broker's current algo/API policy and the SEBI retail-algo framework before going live. Keep a human kill-switch.

---

## 3. Option-Selling Strategies (safest → spiciest)

**Rule for you: only trade DEFINED-RISK strategies until you've survived a full year.**

### A. Credit Spreads — DEFINED RISK ✅ (start here)
Sell an option, buy a further-OTM option as protection. Max loss is capped.
- **Bull Put Spread:** sell put + buy lower put. Profit if market stays up/flat.
- **Bear Call Spread:** sell call + buy higher call. Profit if market stays down/flat.
- **Iron Condor:** bull put spread + bear call spread. Profit if market stays in a range. The classic premium-selling range strategy.
- **Iron Fly:** condor with strikes at ATM — more premium, tighter range.

Why start here: the long leg **caps your worst day**. This is the single most important habit.

### B. Naked Selling — UNDEFINED RISK ⚠️ (avoid as a beginner)
- **Short Straddle:** sell ATM call + put. High theta, but a big move hurts badly.
- **Short Strangle:** sell OTM call + put. Wider safety, still uncapped.
- These make money ~70–80% of days and can lose catastrophically the other days. **Not for a bot you're still learning to trust.**

### C. Common "edges" premium sellers lean on
- **Theta decay:** options lose time value fastest near expiry — sell weeklies, exit before expiry.
- **IV mean-reversion:** sell when implied volatility is high (India VIX elevated), avoid selling into cheap IV.
- **Range days:** condors profit when the index chops sideways.

### Non-negotiable risk rules (bake these into the code)
1. **Max loss per trade** — e.g. stop at 1.5–2× the credit received, hard-coded.
2. **Max loss per day** — bot flattens everything and stops for the day.
3. **Position sizing** — risk ≤ 1–2% of capital per trade. Never "sell more to recover."
4. **No trading around known event risk** unless the strategy is explicitly built for it (RBI policy, Budget, major elections, expiry-day whips).
5. **Defined-risk only** until proven.
6. **Kill switch** — one command flattens all positions.

---

## 4. Minimal Bot Skeleton (Python, pseudocode)

This is a *structure*, not production code. Fill in your broker's SDK calls.
Runs in **DRY_RUN** mode by default — it will not send real orders.

```python
"""
Indian index options-selling bot — SKELETON ONLY.
Defined-risk (Iron Condor) example. DRY_RUN by default.
NOT FINANCIAL ADVICE. Paper trade for weeks before risking money.
"""
import logging, datetime as dt

DRY_RUN = True                 # <-- must stay True until Phase 4
CAPITAL = 100_000              # rupees
MAX_RISK_PER_TRADE = 0.015     # 1.5% of capital
MAX_DAILY_LOSS = 0.03          # 3% of capital -> kill switch
STOP_MULTIPLE = 2.0            # exit if loss = 2x credit received

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")


class Broker:
    """Wrap your broker SDK (Kite / Fyers / Dhan / Angel) here."""
    def login(self): ...                       # handle daily token refresh
    def option_chain(self, symbol, expiry): ...
    def ltp(self, instrument): ...
    def place_order(self, order):
        if DRY_RUN:
            log.info("DRY_RUN order: %s", order); return {"status": "simulated"}
        # real order call here
    def positions(self): ...
    def square_off_all(self):
        log.warning("SQUARE OFF ALL"); ...


def market_open():
    now = dt.datetime.now().time()
    return dt.time(9, 20) <= now <= dt.time(15, 15)   # avoid first/last minutes


def build_iron_condor(chain, spot):
    """Pick ~1 SD OTM short strikes, protective wings further out."""
    # short_put, long_put, short_call, long_call = ...
    # return the 4 legs
    ...


def net_pnl(broker):
    """Compute open + realized P&L for the day."""
    ...


def run():
    broker = Broker(); broker.login()
    entered = False
    credit = 0.0

    while market_open():
        pnl = net_pnl(broker)

        # ---- KILL SWITCH: daily loss ----
        if pnl <= -MAX_DAILY_LOSS * CAPITAL:
            broker.square_off_all()
            log.error("Max daily loss hit. Stopping."); break

        # ---- ENTRY ----
        if not entered:
            chain = broker.option_chain("NIFTY", nearest_weekly_expiry())
            spot = broker.ltp("NIFTY")
            legs = build_iron_condor(chain, spot)
            credit = sum_credit(legs)
            for leg in legs:
                broker.place_order(leg)
            entered = True
            log.info("Entered condor, credit=%.2f", credit)

        # ---- PER-TRADE STOP ----
        if entered and pnl <= -STOP_MULTIPLE * credit:
            broker.square_off_all()
            log.info("Per-trade stop hit."); break

        sleep_seconds(30)

    # end of day: flatten anything left
    broker.square_off_all()


if __name__ == "__main__":
    run()
```

**What's deliberately missing (you must add):** real broker calls, exact strike selection,
slippage handling, order-fill confirmation, crash recovery, and alerting. The skeleton's job
is to enforce the *risk rules first*.

---

## 5. What to do next

1. Pick a broker and get API keys (start with a free one like Angel SmartAPI to learn).
2. Build Phase 1 (data feed) — get the live option chain printing.
3. Encode ONE defined-risk strategy in DRY_RUN.
4. Backtest across crash periods, then paper trade live for 4–8 weeks.
5. Only then, 1 lot of real money.

**Mindset:** the bot's #1 job is to *not blow up*. Returns come from surviving long enough
to compound modestly. Chase 5%/week and the steamroller eventually catches you.

---

*This document is for education and engineering planning. It is not financial advice.
Trading options can lose more than your capital. Verify all lot sizes, margins, taxes,
and regulations against current official sources before trading.*
