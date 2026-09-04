# fx-dashboard

Daily factor attribution for six USD exchange rates. Every trading day the
system pulls its inputs, refits rolling regressions, decomposes each day's move
into factor contributions plus a residual, and publishes the result through a
read-only contract, a set of static report pages, and a local dashboard. When a
residual is large enough to say the move happened outside the model, a narrative
layer retrieves that day's news and writes a short, fact-checked note about what
the model did not explain.

The point of the project is explanation, not prediction. Attribution is
contemporaneous: it accounts for a move that has already happened, in event
time. Nothing here forecasts a rate.

## What it produces

For each of USDEUR, USDJPY, USDCAD, USDNOK, USDAUD and USDMXN, on each trading
day, under three rolling windows and three estimators:

```
y(t) = sum_k beta_k(t) * x_k(t) + residual(t)
```

`y` is the daily log return of the pair, quoted USD/XXX throughout, so a rise
always means a stronger dollar. The betas come from a rolling window that ends
at `t-1`, never including the day being explained. The identity closes by
construction, which makes it a usable test anchor: weekly and monthly
attribution is nothing more than these daily contributions summed along time.

Factors are a fixed per-pair set: a leave-one-out dollar factor, a leave-one-out
carry factor, short and long rate differentials against the matching US tenor,
the VIX change, and one or two benchmarked commodity or credit series chosen per
pair. Eight factors per pair is a hard cap.

## Models

| Estimator | Role |
|---|---|
| OLS | The attribution number. `OLS` at the 126-day window is the canonical figure every single-number consumer reads. |
| Ridge | A stability check under collinear factors. Its penalty is chosen on forward-chaining splits and reselected every 21 trading days. |
| Lasso | Variable selection only. Coefficients for attribution come from an OLS refit on the selected set, never from the penalized fit. |
| PCA | Monitoring and alerting only. It produces no attribution number, and components are never given economic labels by rank. |

The three attribution estimators are computed side by side and never averaged.
A robustness check measures how far Ridge and post-Lasso sit from OLS in basis
points, normalized by that pair's own recent residual scale, and reports one of
four states: the three agree, Ridge diverges, Lasso swapped factors, or Lasso
selected nothing. It is a diagnostic shown next to the numbers, not an alert,
and it never changes the system's green/yellow/red state.

Standardization happens inside the training window only, and coefficients are
converted back to original units before anything is attributed. Cross-validation
is forward-chaining; the data is never shuffled.

## Data

Prices, commodities and ETFs come from Yahoo Finance; US rates, the VIX and
credit spreads from FRED; foreign government yields from the official source in
each country (Bundesbank, Japan's Ministry of Finance, Bank of Canada Valet,
Norges Bank, Banxico SIE, RBA F2).

Acquisition follows a fixed three-step fallback: fetch online, else the last
successful cache, else a local user file. Every fallback is named in the
validation log with the file and its last date. An unavailable series is never
silently replaced by a proxy; a substitute must be defined and named separately
and travels under its own name through the log and the model.

Where a source publishes with a lag, the carried-forward value is marked
provisional, the marking travels downstream, and the row is recomputed once the
official value lands. A provisional row that stays unfilled past its age limit
turns the status yellow rather than waiting indefinitely.

## Running it

```powershell
setx FRED_API_KEY   <key>          # restart the terminal afterwards
setx BANXICO_TOKEN  <token>
setx GEMINI_API_KEY <key>          # narrative layer only

$env:PYTHONPATH = "src"

python -m fxdash.run --mode backfill --start 2010-01-01   # build the history
python -m fxdash.run --mode live                          # daily increment
python -m fxdash.narrative.run                            # narrative layer
pytest
```

Keys are read from the environment only and appear in no script, log, cache or
artifact.

`live` is idempotent: rerunning a date produces no duplicate rows, and a
non-provisional row is never modified. It also fills gaps by itself, so a
machine that was off for a few days catches up on its next run.

Scheduling, the override flags, the output contract and the Windows-specific
notes are in [ops/README.md](ops/README.md).

## The dashboard

```powershell
powershell -ExecutionPolicy Bypass -File ops\serve.ps1     # http://127.0.0.1:8321
```

A FastAPI service over a single-page frontend, bilingual, dark and light themes.
Three pages: News, FX, and Attribution, plus a methodology page reached from
Attribution. The service is a pure downstream consumer, reads `outputs/` only,
and never imports the attribution engine to recompute anything; the only new
arithmetic it performs is summation. It hot-reloads when the nightly run lands
and keeps serving the previous snapshot if a rebuild fails.

There is also a set of static self-contained HTML reports under
`outputs/reports/`, one page per pair plus an overview, written by each run.

## The narrative layer

A day triggers when the standardized residual is at least 2 in absolute value
**and** the residual is at least 50 bp, capped at three pairs a day. In practice
that fires every few days, not daily; the floor exists so a statistically
unusual day on which the pair barely moved does not earn a write-up.

On a trigger the layer retrieves that day's coverage, sends a fact table to the
model, and puts the output through six checks before publishing: every source
must be one that was actually retrieved and dated inside the window, every
number must match the whitelist of strings the fact table supplied, and no
causal claim or directional forecast may appear. Failures are recorded rather
than hidden, and the raw model output is kept regardless so a discarded
generation can still be traced.

The residual is a property of the trading day, not of any single story. Nothing
in the interface splits a residual across headlines or assigns a share to one of
them.

## Layout

```
src/fxdash/
  config.py            parameter registry; the SPEC is changed first, then this
  data/                acquisition, alignment, direction checks, panel assembly
  factors/             per-pair factor construction
  models/              OLS, Ridge, Lasso, rolling estimation, PCA monitoring
  attribution/         the identity, and the contract written to disk
  schedule/            run modes, idempotent merge
  narrative/           trigger, retrieval, prompt composition, verification
  web/                 FastAPI service and the single-page frontend
  report/              static HTML report generation
  health.py            health checks     robustness.py  estimator agreement
  heartbeat.py         liveness          status.py      green/yellow/red
tests/                 342 tests, no network access
ops/                   scheduling and serving scripts
```

Comments cite the project's internal design documents (SPEC_phase1 through
SPEC_phase3, SPEC_web) and its working rules (CLAUDE.md) by section or rule
number. Those documents are not part of this repository; each citing comment
restates the point it relies on.

## Status

Phase 0 (factor diagnostics) and Phase 1 (the attribution engine) are complete.
Phase 2, daily automation and run monitoring, closed on 2026-09-03 after three
consecutive green trading days. Phase 3 added the narrative layer, live since
2026-09-01, and the local dashboard. Phase 4, a daily briefing, has not started.

Tests must all pass for a change to count as delivered.
