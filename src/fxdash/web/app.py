"""FastAPI application factory (SPEC_web §1-2).

/api routes are registered first, StaticFiles(html=True) is mounted at the root
last -- reverse the order and static swallows /api.
uvicorn launch: uvicorn fxdash.web.app:create_app --factory
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import (
    DEFAULT_WINDOW,
    LITERATURE_BANDS_DAILY,
    MODELS,
    OUTPUT_DIR,
    PCA_CORR_WARN,
    WINDOWS,
)
from . import headlines as HL
from . import newsfeed as NF
from .market import RANGES as MARKET_RANGES
from .store import DataStore, clean, clean_list
from .summary import SCALES, combo_scale, latest_row, pair_scales, systematic_split

STATIC_DIR = Path(__file__).parent / "static"  # path derived at runtime (CLAUDE.md 11)


class RevalidatingStatic(StaticFiles):
    """Static assets always carry Cache-Control: no-cache.

    starlette's StaticFiles sends only ETag and Last-Modified, no Cache-Control,
    so the browser heuristically picks its own cache lifetime: the frontend
    changed, the server has the new file, and a user refresh shows nothing new.
    no-cache does not mean "do not cache", it means "revalidate every time"; an
    ETag hit still returns 304, costs almost no bandwidth, and guarantees what
    you see is what is on disk.

    Without it the discipline becomes "remember to hit Ctrl+Shift+R every time",
    which eventually gets missed.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(output_dir: Path | None = None,
               cache_dir: Path | None = None) -> FastAPI:
    store = DataStore(Path(output_dir) if output_dir else OUTPUT_DIR,
                      cache_dir=Path(cache_dir) if cache_dir else None)
    app = FastAPI(title="FX Dashboard", docs_url="/api/docs")
    app.state.store = store

    api = APIRouter(prefix="/api")

    def snap():
        return store.current()

    def get_combo(snapshot, pair: str, window: int, model: str):
        if pair not in snapshot.pairs:
            raise HTTPException(404, detail=f"unknown pair: {pair}")
        if window not in snapshot.windows:
            raise HTTPException(422, detail=f"window must be one of {snapshot.windows}")
        if model not in snapshot.models:
            raise HTTPException(422, detail=f"model must be one of {snapshot.models}")
        combo = snapshot.combo(pair, window, model)
        if combo is None:
            raise HTTPException(404, detail=f"no data for {pair}/{window}/{model}")
        return combo

    def etag_guard(request: Request, response: Response, snapshot, key: str):
        """ETag = data_version + endpoint params. 304 within a day, auto-refresh across days."""
        tag = f'W/"{snapshot.data_version}:{key}"'
        response.headers["ETag"] = tag
        response.headers["Cache-Control"] = "no-cache"
        if request.headers.get("if-none-match") == tag:
            raise HTTPException(304)

    # ------------------------------------------------------------------ meta
    @api.get("/meta")
    def meta(request: Request, response: Response):
        s = snap()
        etag_guard(request, response, s, "meta")
        # factor list derived from the data's key order, not from config -- stay
        # purely downstream (SPEC_web §2)
        factors = {}
        for pair in s.pairs:
            ols = s.combo(pair, DEFAULT_WINDOW, "ols")
            lasso = s.combo(pair, DEFAULT_WINDOW, "lasso")
            factors[pair] = {
                "baseline": ols.factors if ols else [],
                "menu": lasso.factors if lasso else [],
            }
        return {
            "pairs": s.pairs,
            "windows": s.windows,
            "default_window": DEFAULT_WINDOW if DEFAULT_WINDOW in s.windows
            else (s.windows[0] if s.windows else None),
            "models": s.models,
            "default_model": "ols" if "ols" in s.models else (s.models[0] if s.models else None),
            "factors": factors,
            "scales": list(SCALES),
            "date_range": {"first": s.date_first, "last": s.date_last},
            "literature_bands": LITERATURE_BANDS_DAILY,
            "schema_version": s.schema_version,
            "model_revision": s.status.get("model_revision"),
            "data_version": s.data_version,
        }

    # ---------------------------------------------------------------- status
    @api.get("/status")
    def status():
        s = snap()
        return {
            **s.status,
            "server": {
                "data_version": s.data_version,
                "loaded_at": s.loaded_at,
                "reload_state": store.reload_state,
            },
        }

    @api.get("/healthz")
    def healthz():
        return {"ok": True, "data_version": snap().data_version}

    # --------------------------------------------------------------- summary
    def _summary_payload(s, window: int, model: str) -> dict:
        scales: dict = {name: {"n_days": n, "pairs": {}} for name, n in SCALES.items()}
        for pair in s.pairs:
            combo = s.combo(pair, window, model)
            if combo is None:
                continue
            for name in SCALES:
                scales[name]["pairs"][pair] = combo_scale(combo, SCALES[name])
        return {
            "as_of": s.date_last,
            "window": window,
            "model": model,
            "scales": scales,
        }

    @api.get("/summary")
    def summary(
        request: Request,
        response: Response,
        window: int = Query(DEFAULT_WINDOW),
        model: str = Query("ols"),
    ):
        s = snap()
        if window not in s.windows:
            raise HTTPException(422, detail=f"window must be one of {s.windows}")
        if model not in s.models:
            raise HTTPException(422, detail=f"model must be one of {s.models}")
        etag_guard(request, response, s, f"summary:{window}:{model}")
        return _summary_payload(s, window, model)

    # -------------------------------------------------------------- overview
    @api.get("/overview")
    def overview(
        request: Request,
        response: Response,
        window: int = Query(DEFAULT_WINDOW),
        model: str = Query("ols"),
    ):
        s = snap()
        if window not in s.windows:
            raise HTTPException(422, detail=f"window must be one of {s.windows}")
        if model not in s.models:
            raise HTTPException(422, detail=f"model must be one of {s.models}")
        etag_guard(request, response, s, f"overview:{window}:{model}")

        heartbeat = (s.status or {}).get("heartbeat", {})
        pairs = []
        for pair in s.pairs:
            combo = s.combo(pair, window, model)
            if combo is not None:
                pairs.append(latest_row(combo))
        return {
            "as_of": s.date_last,
            "window": window,
            "model": model,
            "status_digest": {
                "state": (s.status or {}).get("state"),
                "provisional_rows": (s.status or {}).get("provisional_rows"),
                "heartbeat_state": heartbeat.get("state"),
                "heartbeat_age_hours": heartbeat.get("age_hours"),
                "last_live_success": heartbeat.get("last_live_success"),
                "reasons": (s.status or {}).get("reasons", []),
            },
            "pairs": pairs,
            "robustness": s.robustness,
            "summary": _summary_payload(s, window, model),
            "freshness": {
                "source_as_of": (s.status or {}).get("source_as_of", {}),
                "coverage": (s.coverage or {}).get("pairs", {}),
                "contract_rows": s.rows,
            },
            "data_version": s.data_version,
        }

    # ----------------------------------------------------------- pair series
    @api.get("/pairs/{pair}/summary")
    def pair_summary(
        pair: str,
        window: int = Query(DEFAULT_WINDOW),
        model: str = Query("ols"),
    ):
        s = snap()
        combo = get_combo(s, pair, window, model)
        return {
            "pair": pair,
            "window": window,
            "model": model,
            "as_of": combo.dates[-1] if combo.dates else None,
            "scales": pair_scales(combo),
        }

    ALL_FIELDS = {"core", "contributions", "betas", "r2", "selection", "stale"}

    @api.get("/pairs/{pair}/series")
    def pair_series(
        pair: str,
        request: Request,
        response: Response,
        window: int = Query(DEFAULT_WINDOW),
        model: str = Query("ols"),
        start: str | None = None,
        end: str | None = None,
        fields: str | None = None,
        observations: int | None = Query(None, ge=1, le=1260),
    ):
        s = snap()
        combo = get_combo(s, pair, window, model)
        etag_guard(
            request, response, s,
            f"series:{pair}:{window}:{model}:{start}:{end}:{fields}:{observations}",
        )

        wanted = (
            {f.strip() for f in fields.split(",") if f.strip()} if fields else ALL_FIELDS
        )
        unknown = wanted - ALL_FIELDS
        if unknown:
            raise HTTPException(422, detail=f"unknown fields: {sorted(unknown)}")

        lo, hi = 0, len(combo.dates)
        if start:
            while lo < hi and combo.dates[lo] < start:
                lo += 1
        if end:
            while hi > lo and combo.dates[hi - 1] > end:
                hi -= 1
        if observations is not None:
            lo = max(lo, hi - observations)

        out: dict = {
            "pair": pair,
            "window": window,
            "model": model,
            "factors": combo.factors,
            "dates": combo.dates[lo:hi],
        }
        if "core" in wanted:
            out.update(
                y=clean_list(combo.y[lo:hi]),
                residual=clean_list(combo.residual[lo:hi]),
                residual_z=clean_list(combo.residual_z[lo:hi]),
                systematic=clean_list(combo.systematic[lo:hi]),
                exogenous=clean_list(combo.exogenous[lo:hi]),
                provisional=[bool(v) for v in combo.provisional[lo:hi]],
            )
        if "r2" in wanted:
            out.update(
                r2_full=clean_list(combo.r2_full[lo:hi]),
                r2_exog=clean_list(combo.r2_exog[lo:hi]),
            )
        if "contributions" in wanted:
            out["contributions"] = {
                f: clean_list(v[lo:hi]) for f, v in combo.contributions.items()
            }
        if "betas" in wanted:
            out["betas"] = {f: clean_list(v[lo:hi]) for f, v in combo.betas.items()}
        if "selection" in wanted and combo.selected is not None:
            # only lasso has selected and λ; other models omit the keys (SPEC_web §2)
            out["selected"] = {
                f: [int(v) for v in arr[lo:hi]] for f, arr in combo.selected.items()
            }
            out["lambda"] = clean_list(combo.lam[lo:hi])
        if "stale" in wanted:
            window_dates = set(combo.dates[lo:hi])
            out["stale_events"] = [
                e for e in combo.stale_events if e["date"] in window_dates
            ]
        sys_f, exo_f = systematic_split(combo.factors)
        out["factor_groups"] = {"systematic": sys_f, "exogenous": exo_f}
        return out


    # ---------------------------------------------------------------- market
    # price levels serve only the ticker and the trend chart, via a read-only
    # adapter over data/cache; every number on the attribution side still comes
    # from contract (the documented exception to SPEC_web §0 rule one, §2.1)
    @api.get("/market/ticker")
    def market_ticker():
        s = snap()
        if s.market is None or not s.market.available:
            return {"available": False, "trading_day": None, "items": []}
        return s.market.ticker()

    @api.get("/market/series/{pair}")
    def market_series(pair: str, range: str = Query("6m", alias="range")):
        s = snap()
        if pair not in s.pairs:
            raise HTTPException(404, detail=f"unknown pair: {pair}")
        if range not in MARKET_RANGES:
            raise HTTPException(
                422, detail=f"range must be one of {sorted(MARKET_RANGES)}")
        if s.market is None or not s.market.available:
            return {"available": False, "reason": "no_cache", "range": range}
        return s.market.series(pair, range)

    # ------------------------------------------------------------------ news
    # daily headlines: read Google News directly, memory only (the second
    # documented exception to rule 1, see headlines.py)
    board = HL.HeadlineBoard()

    def _week_window():
        """Week window = the last 5 contract trading days, the same window the
        Attribution page's weekly decomposition uses."""
        s = snap()
        dates = max((c.dates for c in s.combos.values()), key=len, default=[])
        if not dates:
            return None, None
        lo = dates[-NF.WEEK_DAYS] if len(dates) >= NF.WEEK_DAYS else dates[0]
        return lo, dates[-1]

    def _all_narrative_days():
        # follow the app-configured output_dir, never a global constant. Reading
        # the global would make the tests' tmp_path fixture useless -- the same
        # class of leak already happened once on the market cache
        return NF.load_days(root=snap().output_dir / "narrative")

    def _recent_flagged():
        """The last few trigger days (spanning weeks). The evidence panel and the
        citation matrix use this scope: triggers fire only every 4.5 to 5 days,
        so restricted to this week those panels would be dead most of the time."""
        return NF.recent_flagged_days(_all_narrative_days())

    def _narrative_days():
        """Narrative artifacts inside the week window.

        Must filter by the **trading-day window**, not by "the last N files":
        artifacts are sparse, only trigger days have content, and taking the last
        5 files as this week would put a month-old story on the page labelled as
        this week.
        """
        start, end = _week_window()
        days = _all_narrative_days()
        if start is None:
            return []
        return [d for d in days if d.get("date") and start <= d["date"] <= end]

    @api.get("/news")
    def news():
        """Everything the News page needs in one call: story evidence for this
        week's trigger days, today's headlines, and earlier this week.

        News serves only as **contemporaneous associative evidence**; it never
        allocates residual (user ruling 2026-09-02, full text at the top of the
        newsfeed module).

        When the week window holds no story at all, return a fallback: the most
        recent day that has a published record, **together with its date**. The
        page may show it, but must say which day it is and never pass it off as
        this week.
        """
        s = snap()
        start, end = _week_window()
        all_days = _all_narrative_days()
        days = [d for d in all_days
                if start and d.get("date") and start <= d["date"] <= end]
        week = {"items": NF.cited_stories(days)}

        fallback = None
        if not week["items"]:
            flagged = NF.recent_flagged_days(all_days, limit=1)
            if flagged:
                fallback = {"date": flagged[-1].get("date"),
                            "items": NF.cited_stories(flagged)}

        # today's headlines are fetched at request time under a TTL: the
        # commentary trigger gate governs the LLM spend, it should not also
        # switch off "what is in the news today". The response is a snapshot
        # taken at fetched_at, and the page says so instead of calling it live;
        # a static build takes that snapshot once in the evening (2026-09-04
        # ruling). On fetch failure fall back to the sources in the artifacts,
        # then to an honest empty state.
        #
        # "Today" holds only wall-clock-today stories; earlier ones in this
        # calendar week go in their own earlier list (user ruling: Today should
        # not carry 8/31, those belong to this week). RSS dates have only day
        # precision and are recorded in GMT, so cross-timezone there is fuzz
        # around midnight -- known and accepted
        live = board.snapshot(s.pairs)
        wall_today = HL.today_str()
        week_monday = HL.calendar_week_start(wall_today)
        pool = live.get("all_items") or live["items"]

        def in_week(i):
            return (i.get("published")
                    and week_monday <= i["published"] <= wall_today)

        # the main list holds events only; opinion, analysis, market recaps and
        # trade views go to the collapsed section, none are dropped (user ruling
        # 2026-09-02, classified by content not by source, see newsfeed.story_kind)
        events = [i for i in pool if i.get("kind") != "opinion"]
        todays = HL.fair_slice(
            [i for i in events if i.get("published") == wall_today], s.pairs)
        earlier_items = HL.fair_slice(
            [i for i in events if i.get("published")
             and week_monday <= i["published"] < wall_today], s.pairs,
            cap=HL.EARLIER_CAP)
        opinion_items = sorted(
            [i for i in pool if i.get("kind") == "opinion" and in_week(i)],
            key=lambda i: i.get("published") or "", reverse=True)[:HL.EARLIER_CAP]

        if todays or earlier_items:
            today = {
                "mode": "fetched",
                "date": wall_today,
                "fetched_at": live["fetched_at"],
                "items": todays,
                "errors": live.get("errors", []),
            }
        else:
            artifact = NF.today_headlines(days)
            today = {
                "mode": "artifact" if artifact["items"] else "empty",
                "date": artifact.get("date"),
                "items": artifact["items"],
                "errors": live.get("errors", []),
            }
        earlier = {"start": week_monday, "end": wall_today, "items": earlier_items}

        return {
            "as_of": s.date_last,
            "week_start": start,
            "week_end": end,
            "week": week,
            "today": today,
            "earlier": earlier,
            "opinions": {"items": opinion_items},
            "fallback": fallback,
            "covered_days": [d.get("date") for d in days],
        }

    @api.get("/pairs/{pair}/news")
    def pair_news(pair: str):
        s = snap()
        if pair not in s.pairs:
            raise HTTPException(404, detail=f"unknown pair: {pair}")
        feed = NF.pair_evidence(_recent_flagged(), pair)
        feed["headlines"] = board.for_pair(s.pairs, pair)
        feed["headlines_fetched_at"] = board.snapshot(s.pairs).get("fetched_at")
        return feed

    @api.get("/narrative/status")
    def narrative_status():
        """Narrative-layer heartbeat. **Age is recomputed at read time**, not
        taken from the number in the file.

        The age_hours inside status.json is computed at **write** time. Once the
        job dies the file stops updating, so that number freezes at the last
        successful run and the heartbeat fails to detect exactly the thing it
        exists to catch (SPEC_phase3 §2.3). So trust only the timestamp here and
        compute the age now.

        **Color is judged on last_run only.** last_published is content
        freshness, driven by the market; it is returned but takes no part in the
        color: residual anomalies fire only every 4.5 to 5 days, so running it
        against the 26-hour amber line would alarm daily.
        """
        from datetime import datetime

        from ..narrative import store as NS

        def _age(text):
            if not text:
                return None
            try:
                stamp = datetime.fromisoformat(text)
                now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
                return (now - stamp).total_seconds() / 3600.0
            except Exception:
                return None

        root = snap().output_dir / "narrative"
        stored = NS.read_status(root)
        last_run = stored.get("last_run")
        last_pub = stored.get("last_published")
        age = _age(last_run)
        pub_age = _age(last_pub)
        state, reasons = NS.heartbeat_state(age)
        days = NF.load_days(root=root)
        return {
            "state": state,
            "last_run": last_run,
            "last_published": last_pub,
            "age_hours": None if age is None else round(age, 2),
            "published_age_hours": None if pub_age is None else round(pub_age, 2),
            "warn_hours": NS.HEARTBEAT_WARN_HOURS,
            "crit_hours": NS.HEARTBEAT_CRIT_HOURS,
            "reasons": reasons + [r for r in (stored.get("reasons") or [])
                                  if r not in reasons],
            "generated_at": stored.get("generated_at"),
            "days_on_record": len(days),
        }

    @api.get("/attribution/weekly")
    def attribution_weekly(
        window: int = Query(DEFAULT_WINDOW), model: str = Query("ols"),
        days: int = Query(5),
    ):
        """Attribution page: per-pair weekly bucket decomposition + story x pair
        citation map (evidence mapping)."""
        s = snap()
        if days not in SCALES.values():
            raise HTTPException(422, detail="days must be one of 1, 5, 21")
        if window not in s.windows:
            raise HTTPException(422, detail=f"window must be one of {s.windows}")
        if model not in s.models:
            raise HTTPException(422, detail=f"model must be one of {s.models}")
        rows = []
        for pair in s.pairs:
            combo = s.combo(pair, window, model)
            if combo is None:
                continue
            block = NF.weekly_decomposition(combo, n_days=days)
            if block:
                rows.append(block)
        flagged = _recent_flagged()
        return {
            "as_of": s.date_last,
            "window": window,
            "model": model,
            "bucket_order": [key for key, _l, _m in NF.BUCKETS] + ["residual"],
            "bucket_labels": {**{k: l for k, l, _m in NF.BUCKETS},
                              "residual": "Residual"},
            "pairs": rows,
            "days": days,
            "matrix": NF.citation_matrix(flagged, s.pairs),
            "story_counts": NF.story_counts(flagged, s.pairs),
            "robustness": s.robustness,
        }

    @api.get("/narrative/daily")
    def narrative_daily(
        window: int = Query(DEFAULT_WINDOW), model: str = Query("ols")
    ):
        """Mount point for today's overall summary. LLM text belongs to Phase 3;
        this release returns facts and the frontend assembles a readable line."""
        s = snap()
        if window not in s.windows:
            window = s.windows[0]
        if model not in s.models:
            model = s.models[0]
        movers = []
        for pair in s.pairs:
            combo = s.combo(pair, window, model)
            if combo is None or not combo.dates:
                continue
            row = latest_row(combo)
            total = abs(row["systematic"] or 0) + abs(row["exogenous"] or 0)                 + abs(row["residual"] or 0)
            movers.append({
                "pair": pair,
                "date": row["date"],
                "y": row["y"],
                "top_factor": row["top_factor"],
                "systematic": row["systematic"],
                "exogenous": row["exogenous"],
                "residual": row["residual"],
                "residual_z": row["residual_z"],
                "r2_full": row["r2_full"],
                "shares": {
                    "systematic": (abs(row["systematic"] or 0) / total) if total else None,
                    "exogenous": (abs(row["exogenous"] or 0) / total) if total else None,
                    "residual": (abs(row["residual"] or 0) / total) if total else None,
                },
            })
        movers.sort(key=lambda m: abs(m["y"] or 0), reverse=True)
        return {
            "status": "pending",
            "as_of": s.date_last,
            "window": window,
            "model": model,
            "facts": {"movers": movers},
        }

    # ---------------------------------------------------------------- system
    @api.get("/system")
    def system():
        s = snap()
        m = s.manifest or {}
        return {
            "mode": m.get("mode"),
            "benchmark": m.get("benchmark", {}),
            "health_findings": m.get("health_findings", []),
            "health_current": m.get("health_current", []),
            "merge": m.get("merge", {}),
            "provisional_overwrites": len(m.get("provisional_overwrites") or []),
            "flags": {
                "coverage_shrink_allowed": m.get("coverage_shrink_allowed"),
                "rewrite_history_allowed": m.get("rewrite_history_allowed"),
            },
            "source_as_of": m.get("source_as_of", {}),
            "coverage": s.coverage or {},
            "status": s.status or {},
            "data_version": s.data_version,
        }

    @api.get("/pca")
    def pca(window: int = Query(DEFAULT_WINDOW)):
        s = snap()
        if s.pca is None:
            return {"available": False}
        block = s.pca[s.pca["window"] == window]
        return {
            "available": True,
            "window": window,
            "dates": [d.strftime("%Y-%m-%d") for d in block["date"]],
            "corr_pc1_dollar": clean_list(block["corr_pc1_dollar"].to_numpy()),
            "corr_pc2_carry": clean_list(block["corr_pc2_carry"].to_numpy()),
            "carry_projection_r2": clean_list(
                block["carry_projection_r2"].to_numpy()
            )
            if "carry_projection_r2" in block
            else None,
            "thresholds": PCA_CORR_WARN,
        }

    # ------------------------------------------------------------- narrative
    @api.get("/narrative/{pair}")
    def narrative(pair: str, date: str | None = None):
        """Phase 3 mount point. Always pending in this release (SPEC_web §2)."""
        s = snap()
        combo = get_combo(s, pair, DEFAULT_WINDOW if DEFAULT_WINDOW in s.windows
                          else s.windows[0], "ols" if "ols" in s.models else s.models[0])
        z = None
        if combo.dates:
            i = len(combo.dates) - 1
            if date and date in combo.dates:
                i = combo.dates.index(date)
            z = clean(combo.residual_z[i])
            date = combo.dates[i]
        return {
            "status": "pending",
            "pair": pair,
            "date": date,
            "trigger": {"residual_z": z, "abs_z_threshold_hint": 2.0},
        }

    app.include_router(api)

    # static mounted at the root last (html=True makes / return index.html)
    if STATIC_DIR.exists():
        app.mount("/", RevalidatingStatic(directory=STATIC_DIR, html=True),
                  name="static")

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if exc.status_code == 304:
            return Response(status_code=304)
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}
        )

    return app
