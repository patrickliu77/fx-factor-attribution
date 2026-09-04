# FX Factor Attribution Dashboard

FX moves are easy to explain after the fact, especially when the explanation only
has to sound plausible. I wanted a record that could be checked day by day. This
project takes six USD exchange rates, measures the part of each daily move associated
with a fixed set of market factors, and leaves the rest visible as a residual.

The six pairs are USD/EUR, USD/JPY, USD/CAD, USD/NOK, USD/AUD, and USD/MXN. They are
all stored as USD/XXX, so a positive return always means a stronger dollar. The
pipeline works with completed daily bars and has no forecasting target.

The current dashboard is published at
[patrickliu77.github.io/fx-factor-attribution](https://patrickliu77.github.io/fx-factor-attribution/).

## What the daily result contains

For every pair, model, and rolling window, the accounting is

```text
daily FX return = factor contributions + residual
```

Each contribution is the factor's move on day `t` multiplied by a coefficient fitted
through day `t-1`. The date being explained never appears in its own training sample.
This gives three useful pieces of the move:

* `systematic`: the leave one out dollar and carry factors
* `exogenous`: rates, volatility, commodities, and credit variables
* `residual`: whatever the factor set did not capture

Daily contributions add exactly to the observed return once the residual is included.
The weekly and monthly views are sums of those daily records.

The main number shown in the dashboard is OLS with a 126 trading day window. The
pipeline also runs 63 and 252 day windows. Ridge shows how much correlated factors
can move the allocation. Lasso selects a smaller factor set, followed by an OLS refit
on that set. PCA tracks common structure across the six currencies and stays outside
the attribution numbers.

Ridge and Lasso penalties are chosen with forward time series splits. Scaling is
fitted inside each training window, then coefficients are returned to their original
units before the contribution is calculated.

## Factors and data

Every pair uses a dollar factor built from the other five currencies, a leave one out
carry factor, short and long rate differentials, and the daily change in VIX. The
pair level additions are deliberately small:

| Pair | Additional variables |
|---|---|
| USD/JPY | Gold |
| USD/CAD | WTI crude |
| USD/NOK | Brent crude |
| USD/AUD | Copper and gold |
| USD/MXN | EMB |

Credit and excess bond return series are available during Lasso selection. The full
candidate set is capped at eight variables per pair.

Yahoo Finance supplies exchange rates, commodities, and ETFs. US rates, VIX, and
credit spreads come from FRED. Foreign yields come from the Deutsche Bundesbank,
Japan's Ministry of Finance, the Bank of Canada, Norges Bank, Banxico, and the Reserve
Bank of Australia.

Daily timestamps from these sources do not all describe the same market close. The
project tests alignment by pair and stores the chosen offsets in a frozen profile.
The download path is also fixed: try the source, fall back to the last good cache,
then try a user supplied local file. Every fallback is written to the validation log.

Some official series arrive several days late. A row that uses a carried value is
marked provisional. It can be recalculated after the official observation arrives.
Completed rows stay frozen during regular live runs, which keeps the historical
record stable.

## Residuals and news

The residual is part of the result. It also acts as the trigger for the narrative
layer. A pair is eligible when its absolute residual z score reaches 2.0 and the
absolute residual reaches 50 basis points. The system processes at most three pairs
on one date.

For a triggered pair, Google News RSS provides a dated source set. Gemini receives
those sources together with a table of model facts and recent context. Its English
and Chinese output goes through six checks covering source membership, publication
dates, numbers, causal wording, directional forecasts, and consistency between the
two language versions.

The saved record includes all retrieved sources, the raw response, verification
results, usage, and a content hash. A failed check prevents publication while keeping
the record available for review. News is shown as context for a pair and date. No
headline receives a percentage or a portion of the residual.

## Dashboard

The local application is a FastAPI service with a small JavaScript frontend. It has
News, FX, Attribution, and Methodology views, plus English and Chinese copy and dark
and light themes.

```powershell
powershell -ExecutionPolicy Bypass -File ops\serve.ps1
```

This serves the dashboard at `http://127.0.0.1:8321`. The service reads a snapshot
from `outputs/` and replaces it after a successful nightly run. If a reload fails,
the previous snapshot remains available.

The public site uses the same frontend with selected API responses written to static
JSON files. Its prices and headlines are snapshots taken at the build time displayed
on the page. The pipeline also creates self contained HTML reports in
`outputs/reports/`.

## Running it

Three data sources require credentials. They are read from environment variables:

```powershell
setx FRED_API_KEY <key>
setx BANXICO_TOKEN <token>
setx GEMINI_API_KEY <key>
```

Open a new terminal after using `setx`, then run:

```powershell
$env:PYTHONPATH = "src"

python -m fxdash.run --mode backfill --start 2010-01-01
python -m fxdash.run --mode live
python -m fxdash.narrative.run
pytest
```

The live command fills missed dates and merges the new result without duplicating
rows. Provisional replacements leave an audit trail. Scheduling, override flags,
output files, and Windows setup are covered in [ops/README.md](ops/README.md).

## Layout

```text
src/fxdash/
  data/           acquisition, caching, alignment, and panel assembly
  factors/        factor construction for each pair
  models/         OLS, Ridge, Lasso, rolling fits, and PCA monitoring
  attribution/    daily contributions and the output contract
  schedule/       run modes and idempotent merging
  narrative/      news retrieval, generation, checks, and storage
  web/            FastAPI service and browser interface
  report/         static HTML reports
  run.py          main pipeline entry point

tests/             offline test suite
ops/               scheduling, serving, and publishing scripts
```

Downloaded data, caches, generated contracts, and private working documents are
excluded from the public repository. Credentials stay in environment variables.

The factor research, attribution engine, daily scheduler, monitoring, narrative
layer, and dashboard are running. A concise daily briefing is the next planned stage.
