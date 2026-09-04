# Operations

Scheduling runs on the local Windows Task Scheduler. GitHub Actions was rejected
for one decisive reason: cache persistence. `data/cache/` is gitignored, and a
runner starts from nothing every time, which would remove the second step of the
three-step acquisition fallback (online, then cache, then a local user file).

Two independent daily tasks:

| | Attribution pipeline | Narrative layer |
|---|---|---|
| Task name | `fxdash-live` | `fxdash-narrative` |
| Time (local) | 19:30 | 20:15 |
| Entry point | `fxdash.run` | `fxdash.narrative.run` |
| Status file | `outputs/status.json` | `outputs/narrative/status.json` |
| Log | `outputs/logs/live.log` | `outputs/logs/narrative.log` |

They are separate on purpose. The narrative layer goes online and calls an LLM,
and both of those are flaky. Folding it into the pipeline would let one network
hiccup turn the night's attribution red, and attribution is what the downstream
contract is made of. A narrative failure therefore never changes
`outputs/status.json`; it writes its own status file.

## Registering the tasks

```powershell
powershell -ExecutionPolicy Bypass -File ops\register_task.ps1 -WhatIf
powershell -ExecutionPolicy Bypass -File ops\register_task.ps1

powershell -ExecutionPolicy Bypass -File ops\register_narrative_task.ps1 -WhatIf
powershell -ExecutionPolicy Bypass -File ops\register_narrative_task.ps1
```

All three scripts (the two registrations and `serve.ps1`) derive the repository
path from their own location and resolve the interpreter at run time, in this
order: an explicit `-Python <path>`; otherwise the first `python` (or `python3`)
on PATH that can import this project's dependencies; otherwise
`%USERPROFILE%\miniconda3\python.exe`. The probe imports rather than checking
that a file exists, because the python on PATH is often another project's
environment or the Microsoft Store stub. Each script prints which interpreter
it chose and why, so run with `-WhatIf` first when in doubt. `-At` changes the
time. After registering, each script exports the task XML and checks the settings that
matter, then prints the whole document: checking only the settings the script
sets is not enough, because the one that has actually caused a failure here
(`StopOnIdleEnd`, which kills the task the moment someone touches the keyboard)
is a default that no such list would mention.

### Why 19:30 local

The machine runs on US Central Time, so 19:30 local is 20:30 ET. The day's FX
bars close at 17:00 ET, the US close-based factors (FRED DGS, VIX, credit
spreads) publish between 16:15 and 18:00 ET, and foreign yields land earlier, so
every input exists by then. A full recompute takes about 10 minutes, leaving
roughly 12.5 hours before the 09:00 ET cutoff the next morning: enough to absorb
a slow source and the retry schedule (every 15 minutes, at most 3 attempts).

An early-morning slot would compress that buffer to about 2 hours and buy
nothing, because both slots report the same closed session.

Both tasks are registered to wake the machine and to start as soon as possible
after a missed schedule. Together with the automatic gap backfill, a machine
that was off for several days catches up on its next run.

## Run modes

```powershell
$env:PYTHONPATH = "src"

# Daily increment. Idempotent: non-provisional rows are never modified;
# provisional rows are recomputed only when an input's as-of date advances.
python -m fxdash.run --mode live

# Historical backfill
python -m fxdash.run --mode backfill --start 2010-01-01
```

`live` recomputes the whole history and merges, rather than computing only the
last few days. The reason is reproducibility: the rolling engine reselects the
penalty parameter every 21 trading days, so an incremental run starting from an
arbitrary point would land on a different reselection phase and produce
different coefficients for the same day. Attribution for a given day must not
depend on when the run started. A full recompute costs about 10 minutes, which
is not worth trading reproducibility for.

What actually protects frozen history is the merge policy, not the recompute
range: non-provisional rows are never modified, no matter how often the engine
reruns.

## Override flags

Neither is for everyday use, and both leave a trace in
`outputs/run_manifest.json`:

- `--allow-coverage-shrink` permits the history range to be shorter than the
  previous run. Coverage discipline otherwise halts on a shrink, because an
  earlier implementation silently dropped 14% of the sample and nobody noticed.
- `--rewrite-history` works only with `--mode backfill` and permits frozen rows
  to be rewritten. Use it only for a deliberate change to the factor set, the
  schema, or a model. A code or parameter change must never overwrite rows
  through the live path.

## Outputs

Downstream reads `outputs/contract/` and `outputs/status.json`. That contract
does not change.

| Path | Contents |
|---|---|
| `outputs/contract/year=*/part.parquet` | Daily attribution, partitioned by year |
| `outputs/contract_latest.json` | Last day's rows plus schema version |
| `outputs/status.json` | Green/yellow/red, provisional counts and age tripwire, per-source as-of |
| `outputs/run_manifest.json` | Full record of the run: provisional overwrite audit, health findings, override-flag traces |
| `outputs/source_as_of.json` | As-of dates of the publication-lag sources, used to decide whether an overwrite has a legitimate trigger |
| `outputs/coverage.json` | Panel start, end and row count per pair, compared across runs |
| `outputs/reports/` | One self-contained HTML page per pair, plus an overview |
| `outputs/narrative/` | Narrative artifacts, one file per triggered day |
| `outputs/logs/` | Task logs |

Anything under `outputs/` that a run can regenerate is not committed. The
`.gitignore` uses an allowlist for that directory so the default is exclusion.
Two directories are committed anyway because they cannot be regenerated:
`outputs/alignment/` (the frozen offset profile and its diagnostic figures) and
`outputs/narrative/` (rerunning a day produces different text, which is why the
artifacts are frozen in the first place).

## Web service

```powershell
powershell -ExecutionPolicy Bypass -File ops\serve.ps1      # http://127.0.0.1:8321
```

Leave it running. Hot reload uses `status.json` as a commit marker: when the
night's data lands, the service builds a new snapshot and swaps it in without a
restart. If the rebuild fails it keeps serving the previous snapshot, visible as
`server.reload_state` on `/api/status`, and it never interferes with the
pipeline writing to disk.

Single worker only: multiple workers would each hold their own snapshot and
reload out of step, which is pointless on localhost. The service is a pure
downstream consumer, so every attribution number on a page is either a value in
the contract or a per-key sum of them.

## Environment variables

`FRED_API_KEY`, `BANXICO_TOKEN` and `GEMINI_API_KEY` are read from user-level
environment variables. Set them with `setx NAME <value>` and restart the
terminal; the Task Scheduler reads the registering user's environment, so a
variable set inside a session does not reach it. No key appears in any script,
log, cache, or artifact.

## Windows notes

- **PowerShell 5.1 needs a BOM on any `.ps1` containing non-ASCII text.** Without
  one it reads the file in the system ANSI code page, and parsing fails on the
  first non-ASCII string literal. Save such scripts as UTF-8 with BOM. The
  scripts here are stored that way.
- **Do not read or write source files through `Get-Content` / `Set-Content` on
  PowerShell 5.1.** Without an explicit `-Encoding`, the round trip corrupts
  non-ASCII text irreversibly and adds a BOM, and the edit looks like it
  succeeded. Use Python or an editor instead; keep PowerShell for running
  processes.
- **`live.log` is written in the console code page, not UTF-8**, because the task
  redirects stdout through `cmd.exe`. A normal run emits only ASCII, and paths
  are printed repository-relative for exactly this reason, so the encoding is
  invisible in day-to-day reading. It surfaces only in a traceback, whose file
  paths follow wherever the repository is checked out; read such a log with
  `Get-Content outputs\logs\live.log -Encoding Default`. Every other artifact is
  written by Python with an explicit UTF-8 encoding. `narrative.log` is UTF-8:
  its task sets `PYTHONIOENCODING` explicitly.

## Publishing

The public repository is assembled from a fixed scope (`src/`, `tests/`, the
`ops/` scripts and this manual, the root README, `pytest.ini`,
`requirements.txt`) into a separate directory with its own history. Before
anything is pushed:

1. Clone the assembled repository into a clean temporary directory.
2. Run the full test suite **in the clone**, not in the assembled tree.
3. Scan the clone for anything that identifies a machine or a person: absolute
   paths, user names, host names, e-mail addresses.

Step 2 is not optional, and testing in place does not substitute for it. The
first build's `.gitignore` listed `data/`, which also matched `src/fxdash/data/`,
so fifteen modules were silently untracked while an in-place run of the suite
passed, because the files were physically present. Only a clone shows what was
actually committed.

## Resolved issues

Kept here because each one was invisible to the checks that existed at the time.

- **`serve.ps1` could never start the service.** The guard path
  `src\fxdash\web\app.py` had been written with real control characters (a form
  feed and a bell) where the backslash sequences `\f` and `\a` belong, so
  `Test-Path` never matched and the script threw on every run. The cause was a
  shell heredoc consuming backslash escapes when the file was generated. Fixed
  2026-09-04; the `.ps1` files now parse clean and are checked for stray control
  characters.
- **A command in the operations manual was split in two.** The same escape
  problem put a bare carriage return into `ops\register_narrative_task.ps1`
  inside a code block, so the command wrapped mid-word and could not be copied
  out of the document. Text-mode readers hide a bare CR by treating it as a line
  break; the scan that found it reads bytes. Fixed 2026-09-04.
- **The first public build dropped the whole data-acquisition package.** See
  Publishing above. Fixed by anchoring the ignore rules at the repository root
  (`/data/`, `/outputs/`) and by adopting the clone-and-test step. 2026-09-04.
