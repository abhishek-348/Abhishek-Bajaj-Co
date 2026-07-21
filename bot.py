#!/usr/bin/env python3
"""
Indian index options-selling bot — RUNNABLE SKELETON.

Defined-risk Iron Condor example on NIFTY. Risk rules enforced FIRST.

Safety:
  * DRY_RUN = True by default: NO real orders are ever sent.
  * Ships with a MockBroker so you can run it right now with no broker/keys:
        python3 bot.py
  * To go live later, implement a real Broker subclass (Kite/Fyers/Dhan/Angel),
    paper-trade for weeks, then and only then flip DRY_RUN off.

NOT FINANCIAL ADVICE. Options selling can lose far more than the premium collected.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime

# --------------------------------------------------------------------------- #
# Config — every risk limit lives here, nowhere else.
# --------------------------------------------------------------------------- #

DRY_RUN = True  # <-- must stay True until you've paper-traded for weeks (Phase 4)


@dataclass
class Config:
    symbol: str = "NIFTY"
    lot_size: int = 75                 # VERIFY current NSE lot size before use
    capital: float = 400_000.0         # rupees

    # Risk rules (fractions of capital)
    max_risk_per_trade: float = 0.015  # 1.5% max intended loss per trade
    max_daily_loss: float = 0.03       # 3% -> flatten everything, stop for the day
    stop_multiple: float = 2.0         # exit trade if loss >= 2x credit received

    # Strategy
    wing_width: int = 200              # points between short and protective long strike
    short_otm: int = 300               # points OTM for the short strikes (~1 SD-ish)

    # Loop / simulation
    poll_seconds: float = 1.0          # real bot: ~15-30s. small here so demo is fast
    ignore_market_hours: bool = True   # True for simulation; False for live 09:20-15:15
    sim_ticks: int = 60                # MockBroker: how many ticks to simulate then stop


# --------------------------------------------------------------------------- #
# Domain types
# --------------------------------------------------------------------------- #

@dataclass
class Leg:
    strike: int
    option_type: str        # "CE" or "PE"
    side: str               # "SELL" or "BUY"
    qty: int
    entry_price: float = 0.0


@dataclass
class Position:
    legs: list[Leg] = field(default_factory=list)
    credit: float = 0.0     # net premium received (per lot)


# --------------------------------------------------------------------------- #
# Broker abstraction — swap MockBroker for a real one later.
# --------------------------------------------------------------------------- #

class Broker:
    """Interface. Implement these against your broker SDK for live trading."""

    def login(self) -> None: ...
    def spot(self) -> float: ...
    def option_price(self, strike: int, opt: str) -> float: ...

    def place(self, leg: Leg) -> None:
        raise NotImplementedError

    def square_off_all(self) -> None:
        raise NotImplementedError


class MockBroker(Broker):
    """Simulated broker so the skeleton runs with zero setup.

    Spot does a random walk; option prices use a crude intrinsic+time-value model.
    Good enough to exercise the ENTRY / STOP / KILL-SWITCH plumbing — NOT for backtests.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._spot = 24_000.0
        self.log = logging.getLogger("mockbroker")

    def login(self) -> None:
        self.log.info("MockBroker logged in (simulated).")

    def spot(self) -> float:
        # random walk, ~0.15% std per tick
        self._spot *= 1 + random.gauss(0, 0.0015)
        return round(self._spot, 2)

    def option_price(self, strike: int, opt: str) -> float:
        s = self._spot
        intrinsic = max(s - strike, 0) if opt == "CE" else max(strike - s, 0)
        time_value = max(0.0, 120 - abs(s - strike) * 0.15)  # crude decaying premium
        return round(intrinsic + time_value, 2)

    def place(self, leg: Leg) -> None:
        if DRY_RUN:
            self.log.info("DRY_RUN place: %s %s %d %s @ %.2f",
                          leg.side, leg.strike, leg.qty, leg.option_type, leg.entry_price)
            return
        raise RuntimeError("Live order attempted with MockBroker — implement a real Broker.")

    def square_off_all(self) -> None:
        self.log.warning("SQUARE OFF ALL (simulated).")


# --------------------------------------------------------------------------- #
# Strategy: Iron Condor (DEFINED RISK — the safe place to start)
# --------------------------------------------------------------------------- #

def build_iron_condor(cfg: Config, broker: Broker) -> Position:
    """Sell OTM call + put, buy further-OTM wings so max loss is capped."""
    spot = broker.spot()
    atm = round(spot / 50) * 50  # snap to 50-pt strikes

    short_put = atm - cfg.short_otm
    long_put = short_put - cfg.wing_width
    short_call = atm + cfg.short_otm
    long_call = short_call + cfg.wing_width

    legs = [
        Leg(short_put, "PE", "SELL", cfg.lot_size),
        Leg(long_put, "PE", "BUY", cfg.lot_size),
        Leg(short_call, "CE", "SELL", cfg.lot_size),
        Leg(long_call, "CE", "BUY", cfg.lot_size),
    ]
    for leg in legs:
        leg.entry_price = broker.option_price(leg.strike, leg.option_type)

    # net credit per unit = premium sold - premium bought
    credit = sum((l.entry_price if l.side == "SELL" else -l.entry_price) for l in legs)
    return Position(legs=legs, credit=round(credit, 2))


def position_pnl(pos: Position, broker: Broker) -> float:
    """Mark-to-market P&L in rupees (per lot's worth of legs)."""
    pnl = 0.0
    for leg in pos.legs:
        now = broker.option_price(leg.strike, leg.option_type)
        # SELL profits when price falls; BUY profits when price rises
        move = (leg.entry_price - now) if leg.side == "SELL" else (now - leg.entry_price)
        pnl += move * leg.qty
    return round(pnl, 2)


# --------------------------------------------------------------------------- #
# Risk manager — the most important object in the bot.
# --------------------------------------------------------------------------- #

class RiskManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = logging.getLogger("risk")

    def breached_daily_loss(self, day_pnl: float) -> bool:
        limit = -self.cfg.max_daily_loss * self.cfg.capital
        if day_pnl <= limit:
            self.log.error("DAILY LOSS LIMIT hit: %.0f <= %.0f", day_pnl, limit)
            return True
        return False

    def breached_trade_stop(self, trade_pnl: float, credit_rupees: float) -> bool:
        if credit_rupees <= 0:
            return False
        if trade_pnl <= -self.cfg.stop_multiple * credit_rupees:
            self.log.warning("PER-TRADE STOP hit: pnl %.0f, credit %.0f",
                             trade_pnl, credit_rupees)
            return True
        return False


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def market_open(cfg: Config) -> bool:
    if cfg.ignore_market_hours:
        return True
    now = datetime.now().time()
    return dtime(9, 20) <= now <= dtime(15, 15)  # skip first/last minutes


def run(cfg: Config, broker: Broker) -> None:
    log = logging.getLogger("bot")
    risk = RiskManager(cfg)
    broker.login()

    position: Position | None = None
    credit_rupees = 0.0
    day_pnl = 0.0
    ticks = 0

    while market_open(cfg) and ticks < cfg.sim_ticks:
        ticks += 1

        # --- ENTRY: one condor per day for this skeleton ---
        if position is None:
            position = build_iron_condor(cfg, broker)
            credit_rupees = position.credit * cfg.lot_size
            log.info("Entered Iron Condor. Net credit ~%.0f rupees. Strikes: %s",
                     credit_rupees, [l.strike for l in position.legs])
            for leg in position.legs:
                broker.place(leg)
            continue

        # --- monitor ---
        trade_pnl = position_pnl(position, broker)
        day_pnl = trade_pnl  # single trade/day in this skeleton
        log.info("tick %02d | spot %.0f | trade P&L %+.0f", ticks, broker.spot(), trade_pnl)

        # --- KILL SWITCH: daily loss ---
        if risk.breached_daily_loss(day_pnl):
            broker.square_off_all()
            log.error("Kill switch: stopping for the day.")
            break

        # --- per-trade stop ---
        if risk.breached_trade_stop(trade_pnl, credit_rupees):
            broker.square_off_all()
            log.info("Trade stopped out. Done for the day.")
            break

        time.sleep(cfg.poll_seconds)

    else:
        # loop ended without a break -> flatten anything still open
        broker.square_off_all()

    log.info("Session end. Final P&L %+.0f rupees.", day_pnl)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-10s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = Config()
    logging.getLogger("bot").info(
        "Starting | DRY_RUN=%s | capital=%.0f | max_daily_loss=%.0f | per-trade stop=%.1fx",
        DRY_RUN, cfg.capital, cfg.max_daily_loss * cfg.capital, cfg.stop_multiple,
    )
    broker = MockBroker(cfg)  # <-- swap for KiteBroker(cfg) etc. when live
    run(cfg, broker)


if __name__ == "__main__":
    main()
