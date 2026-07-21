#!/usr/bin/env python3
"""
Dhan broker adapter for bot.py — implements the Broker interface using DhanHQ v2.

Requires:  pip install dhanhq   (tested against dhanhq 2.2.0)
Env vars:  DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN   (create the token in Dhan web -> DhanHQ / API)

What it does:
  * login()          -> builds a DhanContext + dhanhq client
  * chain()          -> pulls the live option chain (spot + per-strike LTP) and
                        joins it with the scrip master to attach security_ids
  * place(leg)        -> places a MARKET order (SKIPPED when dry_run=True)
  * square_off_all()  -> flattens every open F&O position with opposite MARKET orders

IMPORTANT — verify before live use:
  * dhan_under_id / dhan_under_seg in Config (NIFTY index = 13 on IDX_I).
  * Scrip-master column names (Dhan occasionally renames CSV headers).
  * Option-chain endpoint is rate-limited (~1 request / 3s) -> keep poll_seconds >= 3.
  * Product type: INTRADAY (auto-squared EOD) vs MARGIN (overnight carry).
  * Test everything in dry_run first, then with 1 lot.

NOT FINANCIAL ADVICE.
"""
from __future__ import annotations

import logging
import os

from bot import Broker, Chain, Config, Leg, StrikeQuote


class DhanBroker(Broker):
    def __init__(self, cfg: Config, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.log = logging.getLogger("dhan")
        self.dhan = None
        self._expiry: str | None = None
        self._id_map: dict[tuple[int, str], str] = {}   # (strike, "CE"/"PE") -> security_id

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    def login(self) -> None:
        from dhanhq import DhanContext, dhanhq

        client_id = os.getenv("DHAN_CLIENT_ID")
        token = os.getenv("DHAN_ACCESS_TOKEN")
        if not client_id or not token:
            raise RuntimeError("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN env vars.")

        ctx = DhanContext(client_id, token)
        self.dhan = dhanhq(ctx)
        self._expiry = self._nearest_expiry()
        self._load_instrument_ids()
        self.log.info("Dhan logged in. Nearest expiry=%s, %d option ids mapped.",
                      self._expiry, len(self._id_map))

    # ------------------------------------------------------------------ #
    # Expiry + instrument-master lookup (security_id per contract)
    # ------------------------------------------------------------------ #
    def _nearest_expiry(self) -> str:
        resp = self.dhan.expiry_list(self.cfg.dhan_under_id, self.cfg.dhan_under_seg)
        dates = (resp or {}).get("data", [])
        if not dates:
            raise RuntimeError(f"No expiries returned for {self.cfg.symbol}: {resp}")
        return sorted(dates)[0]   # nearest upcoming expiry ("YYYY-MM-DD")

    def _load_instrument_ids(self) -> None:
        """Download Dhan's scrip master once and map (strike, type) -> security_id
        for this underlying + expiry. Column names per the 'detailed' CSV."""
        from dhanhq import dhanhq

        df = dhanhq.fetch_security_list("detailed")
        if df is None:
            raise RuntimeError("Could not fetch Dhan scrip master.")

        exp = self._expiry
        m = (
            (df["SEM_EXM_EXCH_ID"] == "NSE")
            & (df["SEM_INSTRUMENT_NAME"] == "OPTIDX")
            & (df["SM_SYMBOL_NAME"].astype(str).str.upper() == self.cfg.symbol.upper())
            & (df["SEM_EXPIRY_DATE"].astype(str).str.startswith(str(exp)))
        )
        sub = df[m]
        for _, row in sub.iterrows():
            strike = int(float(row["SEM_STRIKE_PRICE"]))
            opt = str(row["SEM_OPTION_TYPE"]).upper()   # "CE" / "PE"
            self._id_map[(strike, opt)] = str(row["SEM_SMST_SECURITY_ID"])

        if not self._id_map:
            raise RuntimeError("No option ids matched — verify scrip-master column names/expiry.")

    # ------------------------------------------------------------------ #
    # Live option chain -> normalized Chain
    # ------------------------------------------------------------------ #
    def chain(self) -> Chain:
        resp = self.dhan.option_chain(
            under_security_id=self.cfg.dhan_under_id,
            under_exchange_segment=self.cfg.dhan_under_seg,
            expiry=self._expiry,
        )
        data = (resp or {}).get("data", {})
        spot = float(data.get("last_price", 0.0))
        quotes: dict[int, StrikeQuote] = {}
        for strike_str, node in (data.get("oc") or {}).items():
            strike = int(float(strike_str))
            ce = node.get("ce") or {}
            pe = node.get("pe") or {}
            quotes[strike] = StrikeQuote(
                strike=strike,
                ce_ltp=float(ce.get("last_price", 0.0)),
                pe_ltp=float(pe.get("last_price", 0.0)),
                ce_id=self._id_map.get((strike, "CE"), ""),
                pe_id=self._id_map.get((strike, "PE"), ""),
            )
        return Chain(spot=spot, quotes=quotes)

    # ------------------------------------------------------------------ #
    # Orders
    # ------------------------------------------------------------------ #
    def place(self, leg: Leg) -> None:
        from dhanhq import dhanhq

        if self.dry_run:
            self.log.info("DRY_RUN place: %s %s %d %s @ %.2f (id=%s)",
                          leg.side, leg.strike, leg.qty, leg.option_type,
                          leg.entry_price, leg.security_id)
            return
        if not leg.security_id:
            raise RuntimeError(f"No security_id for {leg.strike}{leg.option_type} — aborting order.")

        resp = self.dhan.place_order(
            security_id=leg.security_id,
            exchange_segment=dhanhq.NSE_FNO,
            transaction_type=(dhanhq.SELL if leg.side == "SELL" else dhanhq.BUY),
            quantity=leg.qty,
            order_type=dhanhq.MARKET,
            product_type=self.cfg.dhan_product,
            price=0,
        )
        self.log.info("LIVE order %s %s%s x%d -> %s",
                      leg.side, leg.strike, leg.option_type, leg.qty, resp)

    def square_off_all(self) -> None:
        from dhanhq import dhanhq

        if self.dry_run or self.dhan is None:
            self.log.warning("SQUARE OFF ALL (dry_run / no session).")
            return

        resp = self.dhan.get_positions() or {}
        for pos in resp.get("data", []):
            net = int(pos.get("netQty", 0))
            if net == 0:
                continue
            side = dhanhq.SELL if net > 0 else dhanhq.BUY  # opposite of current net
            self.dhan.place_order(
                security_id=str(pos.get("securityId")),
                exchange_segment=pos.get("exchangeSegment", dhanhq.NSE_FNO),
                transaction_type=side,
                quantity=abs(net),
                order_type=dhanhq.MARKET,
                product_type=pos.get("productType", self.cfg.dhan_product),
                price=0,
            )
        self.log.warning("Squared off %d position(s).", len(resp.get("data", [])))
