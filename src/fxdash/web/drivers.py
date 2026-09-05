"""Leading-factor and currency-context news, independent of residual triggers.

RSS publication times have day precision only. observed_at records when this
process actually retrieved each slate, never an inferred publication timestamp.
No LLM judgments or causal allocations are produced here.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

from ..narrative import retrieve as R
from ..narrative.news_quality import REVISION, screen
from . import headlines as HL
from .summary import latest_row

FACTOR_TERMS = {
    "DOLLAR_LOO": '("US dollar" OR "dollar index" OR "Federal Reserve")',
    "CARRY_LOO": '("carry trade" OR "funding currencies" OR "risk appetite")',
    "d2Y_DIFF": '("two year yield" OR "2-year yield" OR "interest rate outlook")',
    "d10Y_DIFF": '("10-year yield" OR "government bond yields")',
    "dVIX": '(VIX OR "equity volatility")',
    "WTI": '(WTI OR "crude oil")', "BRENT": '(Brent OR "oil supply")',
    "GOLD": '("gold prices" OR bullion)', "COPPER": '("copper prices" OR copper)',
    "EMB": '("emerging market bonds" OR "EM debt")',
    "HY_EXCESS": '("high yield bonds" OR "credit spreads")',
    "dHY_OAS": '("high yield spreads" OR "credit spreads")',
}


def leading_rows(snapshot) -> list[dict]:
    rows = []
    for pair in snapshot.pairs:
        c = snapshot.combo(pair, 126, "ols")
        if c is None or not c.dates:
            continue
        row = latest_row(c)
        values = row.get("contributions", {})
        leading = sorted(((k, v) for k, v in values.items()
                          if v is not None and np.isfinite(v) and v != 0),
                         key=lambda kv: (-abs(kv[1]), kv[0]))[:2]
        rows.append({**row, "leading": [{"factor": k, "contribution_bp": v*1e4}
                                       for k, v in leading]})
    return rows


def collect(snapshot, fetcher=None, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    if started.tzinfo is None:
        raise ValueError("clock must include a timezone")
    rows = leading_rows(snapshot)
    # This is current context. Never label a fresh RSS search as an old session's
    # point-in-time news. An archived brief carries both dates explicitly.
    today = started.astimezone(timezone.utc).date()
    lo, hi = (today - timedelta(days=3)).isoformat(), today.isoformat()
    jobs = {}
    for row in rows:
        pair = row["pair"]
        jobs["currency:"+pair] = HL.DISPLAY_PAIR_TERMS.get(pair, pair)
        for factor in row["leading"]:
            key = factor["factor"]
            if key in FACTOR_TERMS:
                jobs["factor:"+key] = FACTOR_TERMS[key]

    def pull(job):
        key, terms = job
        query = f"{terms} when:3d"
        try:
            candidates = R.parse_feed((fetcher or HL._fetch)(query), max_items=12, phase="context")
            observed = clock().isoformat(timespec="seconds")
            return key, {"query": query, **screen(candidates, key, lo, hi, observed),
                         "observed_at": observed, "error": None}
        except Exception as exc:
            # Exception type suffices, avoiding provider URLs or credentials in logs.
            observed = clock().isoformat(timespec="seconds")
            return key, {"query": query, **screen([], key, lo, hi, observed),
                         "observed_at": observed,
                         "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=6) as pool:
        slates = dict(pool.map(pull, jobs.items()))
    for row in rows:
        row["currency_news"] = "currency:"+row["pair"]
        for factor in row["leading"]:
            factor["news_key"] = "factor:"+factor["factor"]
    return {"as_of": snapshot.date_last, "window": 126, "model": "ols",
            "data_version": snapshot.data_version,
            "fetched_at": clock().isoformat(timespec="seconds"),
            "publication_precision": "day", "news_window": {"start": lo, "end": hi},
            "source_policy": REVISION,
            "mode": "retrieved_context_without_causal_claim", "pairs": rows, "slates": slates}


class DriverBoard:
    def __init__(self, ttl_s=1800):
        self.ttl_s, self._stamp, self._cached = ttl_s, 0.0, None
        self._lock = threading.Lock()

    def snapshot(self, snapshot):
        with self._lock:
            if (self._cached and self._cached["data_version"] == snapshot.data_version
                    and time.monotonic() - self._stamp < self.ttl_s):
                return self._cached
            # Each slate reports failures explicitly. Do not splice old sources
            # into new attribution numbers and make the combination look current.
            self._cached = collect(snapshot)
            self._stamp = time.monotonic()
            return self._cached
