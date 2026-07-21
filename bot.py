#!/usr/bin/env python3
"""
Indian index options-selling bot — RUNNABLE SKELETON.

Defined-risk Iron Condor example on NIFTY. Risk rules enforced FIRST.

Safety:
  * DRY_RUN = True by default: NO real orders are ever sent.
  * Ships with a MockBroker so you can run it right now with no broker/keys:
        python3 bot.py
  * A real Dhan adapter lives in dhan_broker.py. To use it, set BROKER="dhan",
    provide DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env vars, paper-trade for weeks,
    and only THEN flip DRY_RUN off.

NOT FINANCIAL ADVICE. Options selling can lose far more than the premium collected.
"""
from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime

# --------------------------------------------------------------------------- #
# Config — every risk limit lives here, nowhere else.
# --------------------------------------------------------------------------- #

DRY_RUN = True                 # <-- keep True until you've paper-traded for weeks (Phase 4)
BROKER = os.getenv("BROKER", "mock")   # "mock" or "dhan"


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
    strike_step: int = 50              # NIFTY strikes are 50 apart
    wing_width: int = 200              # points between short and protective long strike
    short_otm: int = 300               # points OTM for the short strikes (~1 SD-ish)

    # Dhan underlying (VERIFY: NIFTY index = 13 on IDX_I; BANKNIFTY = 25)
    dhan_under_id: int = 13
    dhan_under_seg: str = "IDX_I"
    dhan_product: str = "INTRADAY"     # INTRADAY or MARGIN (carry)

    # Loop / simulation
    poll_seconds: float = 1.0          # live Dhan: use >= 3 (option-chain rate limit)
    ignore_market_hours: bool = True   # True for simulation; False for live 09:20-15:15
    sim_ticks: int = 60                # MockBroker: how many ticks to simulate then stop


# --------------------------------------------------------------------------- #
# Domain types — shared by every broker implementation.
# --------------------------------------------------------------------------- #

@dataclass
class Leg:
    strike: int
    option_type: str        # "CE" or "PE"
    side: str               # "SELL" or "BUY"
    qty: int
    entry_price: float = 0.0
    security_id: str = ""   # broker-specific contract id (needed to place live orders)


@dataclass
class StrikeQuote:
    strike: int
    ce_ltp: float = 0.0
    pe_ltp: float = 0.0
    ce_id: str = ""
    pe_id: str = ""


@dataclass
class Chain:
    """Normalized option-chain snapshot. Every broker returns one of these."""
    spot: float
    quotes: dict[int, StrikeQuote] = field(default_factory=dict)

    def ltp(self, strike: int, opt: str) -> float:
        q = self.quotes.get(strike)
        if not q:
            return 0.0
        return q.ce_ltp if opt == "CE" else q.pe_ltp

    def sec_id(self, strike: int, opt: str) -> str:
        q = self.quotes.get(strike)
        if not q:
            return ""
        return q.ce_id if opt == "CE" else q.pe_id


@dataclass
class Position:
    legs: list[Leg] = field(default_factory=list)
    credit: float = 0.0     # net premium received per unit (before lot multiplier)


# --------------------------------------------------------------------------- #
# Broker abstraction — MockBroker here; DhanBroker in dhan_broker.py.
# --------------------------------------------------------------------------- #

class Broker:
    """Interface every broker implementation must satisfy."""

    def login(self) -> None: ...
    def chain(self) -> Chain: ...

    def place(self, leg: Leg) -> None:
        raise NotImplementedError

    def square_off_all(self) -> None:
        raise NotImplementedError


class MockBroker(Broker):
    """Simulated broker so the skeleton runs with zero setup.

    Spot random-walks; option prices use a crude intrinsic+time-value model.
    Exercises the ENTRY / STOP / KILL-SWITCH plumbing — NOT valid for backtests.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._spot = 24_000.0
        self.log = logging.getLogger("mockbroker")

    def login(self) -> None:
        self.log.info("MockBroker logged in (simulated).")

    def _price(self, strike: int, opt: str) -> float:
        s = self._spot
        intrinsic = max(s - strike, 0) if opt == "CE" else max(strike - s, 0)
        time_value = max(0.0, 120 - abs(s - strike) * 0.15)
        return round(intrinsic + time_value, 2)

    def chain(self) -> Chain:
        self._spot *= 1 + random.gauss(0, 0.0015)   # random walk per snapshot
        atm = round(self._spot / self.cfg.strike_step) * self.cfg.strike_step
        quotes: dict[int, StrikeQuote] = {}
        for k in range(atm - 1500, atm + 1500 + 1, self.cfg.strike_step):
            quotes[k] = StrikeQuote(
                strike=k,
                ce_ltp=self._price(k, "CE"), pe_ltp=self._price(k, "PE"),
                ce_id=f"MOCK-{k}-CE", pe_id=f"MOCK-{k}-PE",
            )
        return Chain(spot=round(self._spot, 2), quotes=quotes)

    def place(self, leg: Leg) -> None:
        if DRY_RUN:
            self.log.info("DRY_RUN place: %s %s %d %s @ %.2f (id=%s)",
                          leg.side, leg.strike, leg.qty, leg.option_type,
                          leg.entry_price, leg.security_id)
            return
        raise RuntimeError("Live order with MockBroker — use BROKER=dhan for real trades.")

    def square_off_all(self) -> None:
        self.log.warning("SQUARE OFF ALL (simulated).")


def make_broker(cfg: Config) -> Broker:
    if BROKER == "dhan":
        from dhan_broker import DhanBroker   # imported lazily so mock needs no SDK
        return DhanBroker(cfg, dry_run=DRY_RUN)
    return MockBroker(cfg)


# --------------------------------------------------------------------------- #
# Strategy: Iron Condor (DEFINED RISK — the safe place to start)
# --------------------------------------------------------------------------- #

def _nearest_strike(chain: Chain, target: int, step: int) -> int:
    """Snap to the closest strike that actually exists in the chain."""
    if target in chain.quotes:
        return target
    return min(chain.quotes, key=lambda k: abs(k - target)) if chain.quotes else target


def build_iron_condor(cfg: Config, chain: Chain) -> Position:
    """Sell OTM call + put, buy further-OTM wings so max loss is capped."""
    atm = round(chain.spot / cfg.strike_step) * cfg.strike_step
    sp = _nearest_strike(chain, atm - cfg.short_otm, cfg.strike_step)
    lp = _nearest_strike(chain, sp - cfg.wing_width, cfg.strike_step)
    sc = _nearest_strike(chain, atm + cfg.short_otm, cfg.strike_step)
    lc = _nearest_strike(chain, sc + cfg.wing_width, cfg.strike_step)

    legs = [
        Leg(sp, "PE", "SELL", cfg.lot_size),
        Leg(lp, "PE", "BUY", cfg.lot_size),
        Leg(sc, "CE", "SELL", cfg.lot_size),
        Leg(lc, "CE", "BUY", cfg.lot_size),
    ]
    for leg in legs:
        leg.entry_price = chain.ltp(leg.strike, leg.option_type)
        leg.security_id = chain.sec_id(leg.strike, leg.option_type)

    credit = sum((l.entry_price if l.side == "SELL" else -l.entry_price) for l in legs)
    return Position(legs=legs, credit=round(credit, 2))


def position_pnl(pos: Position, chain: Chain) -> float:
    """Mark-to-market P&L in rupees against a fresh chain snapshot."""
    pnl = 0.0
    for leg in pos.legs:
        now = chain.ltp(leg.strike, leg.option_type)
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
        chain = broker.chain()

        # --- ENTRY: one condor per day in this skeleton ---
        if position is None:
            position = build_iron_condor(cfg, chain)
            credit_rupees = position.credit * cfg.lot_size
            log.info("Entered Iron Condor. Net credit ~%.0f rupees. Strikes: %s",
                     credit_rupees, [l.strike for l in position.legs])
            for leg in position.legs:
                broker.place(leg)
            continue

        # --- monitor ---
        trade_pnl = position_pnl(position, chain)
        day_pnl = trade_pnl  # single trade/day in this skeleton
        log.info("tick %02d | spot %.0f | trade P&L %+.0f", ticks, chain.spot, trade_pnl)

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
        broker.square_off_all()   # loop ended cleanly -> flatten anything open

    log.info("Session end. Final P&L %+.0f rupees.", day_pnl)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-11s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = Config()
    logging.getLogger("bot").info(
        "Starting | BROKER=%s | DRY_RUN=%s | capital=%.0f | max_daily_loss=%.0f | stop=%.1fx",
        BROKER, DRY_RUN, cfg.capital, cfg.max_daily_loss * cfg.capital, cfg.stop_multiple,
    )
    run(cfg, make_broker(cfg))


if __name__ == "__main__":
    main()
