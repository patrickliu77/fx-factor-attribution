# Cloud operations

## What is ready

The code can restore a private seed, run the existing quantitative engine,
collect news, compose a bilingual recap, attach audio, build the site and submit
the daily emails after checking the public text and recordings. All stages use
durable conditional claims. Saved results survive the runner's temporary disk.

Production remains on the owner's computer. No paid storage, cloud identity,
secret upload, real model/speech request or email submission was made during
this preparation. GitHub now has a dedicated `fx-cloud-production` environment
restricted to the `main` branch, with no allowed tags and no environment secrets.
Both repository activation variables are explicitly `false`. The existing
`github-pages` environment is unchanged. Checking in a workflow or preparing its
environment does not establish a functioning deployment.

## State and execution

| Component | Role |
| --- | --- |
| `runtime.py` | Bootstrap, operator cutover, fixed daily seed, stage recovery and health checks |
| `workspace.py`, `worker.py` | Restore into a new temporary directory; copy and hash application code; run the frozen quant/build entry points |
| `ports.py`, `briefing.py`, `audio.py` | Connect production implementations to pending journal claims |
| `journal.py`, `state.py`, `azure_blob.py` | Conditional ownership, immutable artifacts and private Azure persistence |
| `publish.py`, `delivery.py` | Fixed-repository publication and once-per-day/language campaign submission |
| `reconcile.py` | Read-only incident inspection and explicitly reviewed owner release |

Each New York date has one input seed and a fixed mode/model/speech policy.
Reruns restore completed artifacts; they do not recalculate a paid stage.
The next date starts with the most recent complete private checkpoint, including
completed quant work when later news collection failed. Failed or uncertain
operations block that day's continuation. The runner never mails missed dates
as a backlog.

Quant executes `fxdash.run --mode live --skip-report` in a copied workspace.
The six pairs, 63/126/252 windows, OLS/Ridge/post-Lasso definitions, frozen
alignment and PCA diagnostics are unchanged. The worker verifies its source
hashes and cannot run through this entry point in the desktop repository.
Storage, publisher, model, email and speech secrets are removed from the quant
child; only its two data credentials are retained. The build child receives no
provider secrets. Ordinary free RSS/ticker requests made by the builder still
use the existing public-data readers.

Private bundles preserve the input-vintage archive, calendar source evidence,
editions, audio and local claims. Public exports are separately allowlisted and
hashed. New cloud email receipts live in the durable journal; they do not depend
on a temporary copy of the local sender's receipt files. Public verification
observations are retained privately, but every delivery retry probes afresh.

Successful legacy audio is reused at its original version. Incomplete, corrupt,
future-dated or mixed-version attachments stop for review. A new language gets
one Azure request, with no automatic retry after an ambiguous response. New
recordings retain the existing faster, human-readable recap script. The browser
and email keep the detailed figures separate from that recap.

## Account setup, after approval

The free GitHub environment and its `main`-only deployment rule have been
created and read back through the API. No provider secrets were uploaded and no
production workflow was dispatched. These actions remain:

1. Create a private Azure container. Disable anonymous access at account level,
   require TLS, review region and retention, and set budget alerts. An alert is
   not a hard spending cap. Do not enable blanket deletion of active artifacts
   or the journal. Snapshot limits bound an individual upload, not total cost.
2. Create a narrowly scoped federated identity for the protected
   `fx-cloud-production` GitHub environment. Its `main`-only branch policy is
   already configured; verify it again before activation. Confirm the actual OIDC subject and grant only
   the required container data permissions. No general Azure administrator role
   or storage account key is needed by the runner.
3. Set environment variables `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
   `FXDASH_STORAGE_ACCOUNT`, `FXDASH_STORAGE_CONTAINER`, `AZURE_SPEECH_REGION`.
   Set environment secrets `FRED_API_KEY`, `BANXICO_TOKEN`, `GEMINI_API_KEY`,
   `AZURE_SPEECH_KEY`, `BREVO_API_KEY`, `FXDASH_PUBLISH_TOKEN`. Never put keys in
   workflow YAML, chat, source commits or public artifacts. Reuse the reviewed
   sender and language lists in the private seed.
4. The publication token must have narrowly scoped repository contents-write
   permission and be supplied explicitly. The existing branch-based Pages setup
   cannot rely on a push made with the default Actions token to trigger its
   build. Do not grant a broader account-wide token for convenience.
5. Enable GitHub failed-workflow email notifications for the account that owns
   the schedule. Test that notification using a deliberate non-sending health
   failure after activation. This personal preference cannot be established by
   committing code.

References: [Azure federation](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure),
[Pages publishing](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site),
[workflow notifications](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications).

## Bootstrap and shadow

Run commands from a reviewed checkout with `PYTHONPATH=src`. `runtime check`
reports configuration presence and `ffprobe` availability without acquiring
credentials or calling a network service:

```text
python -m fxdash.cloud.runtime check
```

After storage authorization, wait for local writers to finish. Use the snapshot
helpers in [Cloud migration](CLOUD_MIGRATION.md) to create and validate an ignored
private ZIP. Supply a short-lived, container-scoped Azure bearer token through
the local process environment `FXDASH_BLOB_ACCESS_TOKEN`, then run:

```text
python -m fxdash.cloud.runtime bootstrap --local-operator --seed <private-seed.zip>
```

This is an explicit upload, not a code-only check. It creates the journal and
runtime head only once, with delivery unauthorized. If bootstrap is interrupted
between those writes, inspect the account before proceeding. Never initialize
over partial state or move to a fresh container to evade existing send claims.

After the private seed and identity are accepted, set the repository variable
`FXDASH_CLOUD_ENABLED=true`. Keep `FXDASH_CLOUD_DELIVERY_ENABLED=false`. Dispatch
**Cloud briefing (activation required)** on `main` in shadow mode. Its model and
speech inputs default to false. Turning either on explicitly authorizes the
corresponding provider calls and requires a separate quota/cost decision.
Shadow still performs live market/news downloads and private storage operations.

Check all 54 combinations, source freshness, preserved historical final rows,
input timestamps, the saved edition and a reviewed public candidate. Test paid
providers only after authorization. `ffprobe` must be installed on the runner;
the workflow fails its tool check if the image no longer supplies it.

## One-owner cutover

On an agreed fresh run date, disable local jobs that can generate, publish or
send and wait for every active worker to exit. Leave read-only localhost serving
available if desired. Take and validate a final private seed containing the
latest claims and receipts. Then explicitly attest that local delivery stopped:

```text
python -m fxdash.cloud.runtime authorize-delivery --local-operator --seed <final-private-seed.zip> --local-sender-stopped
```

The command preserves the cloud journal and installs the final seed. It refuses
cutover if the cloud already has claims for that date, so a shadow cannot be
transformed into a second same-day generation. The attestation is supplied by
the operator; it does not remotely inspect the Windows scheduler.

Only after that review set `FXDASH_CLOUD_DELIVERY_ENABLED=true`. Scheduled runs
then request model and speech generation and use delivery mode. A manually
dispatched delivery likewise needs the durable authorization and speech enabled.
For a given date, keep policy flags unchanged between attempts.

## Schedule and alerts

The prepared production workflow starts at 08:05 New York time, with delivery
checks at 09:00, 09:17, 10:17 and 12:17 on weekdays. It handles daylight saving
time using `America/New_York`. Before 09:00, an enabled delivery run prepares
artifacts and stops before publication. Later invocations reuse them. Evidence
first collected after the morning cutoff is recorded as catch-up.

The independent watchdog checks at 09:37, 11:37 and 15:37. After 09:30 it requires
both the current day's submitted run record and the matching durable bilingual
campaign/send receipts. Missing records or inaccessible storage fail the
watchdog workflow, enabling GitHub's configured failure notifications. This is
an operational alert threshold, not a new historical acceptance deadline.
It cannot prove inbox placement or that a reader listened.

GitHub can delay or drop scheduled jobs and disables inactive public-repository
schedules after 60 days. The watchdog shares GitHub as an infrastructure
dependency, so it cannot detect a complete GitHub scheduling outage by itself.
An independently hosted external heartbeat is a later deployment choice.
09:00 is a target; no exact inbox delivery time is guaranteed.
[Scheduling behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Incident recovery

Use a short-lived local storage token for these commands. The first is read-only:

```text
python -m fxdash.cloud.reconcile inspect
```

It shows operation states, owner, run ID/attempt and the journal hash, without
payloads, credentials or subscriber details. If a worker died while owning the
journal, disable scheduling, confirm that no run is active or queued, inspect
the provider side of every uncertain operation, and review the current hash.
The explicit release command additionally checks GitHub's fixed repository API:

```text
python -m fxdash.cloud.reconcile release --expected-sha256 <reviewed-journal-hash> --owner <owner> --run-id <run-id> --run-attempt <attempt> --scheduler-stopped
```

Only a matching completed run attempt can release that exact owner. The command
records recovery intent before a conditional update and preserves all claims.
If its response is lost, inspect again; do not blindly rerun release. A lock left
before its run identity was recorded cannot use automatic proof and needs a
separately reviewed storage recovery. Never manually delete the journal.

| Observation | Action |
| --- | --- |
| Public Pages bytes are still old | Retry the same day's command; do not regenerate or republish |
| Model/audio request outcome is unknown | Keep the blocking claim; review provider logs and continue on a fresh date when appropriate |
| Campaign creation/send outcome is unknown | Check Brevo by recorded campaign ID if available; never submit a replacement blindly |
| Stored artifact is missing or corrupt | Restore verified private state using its hashes and preserved journal; do not synthesize replacement history |
| Storage cannot be read | Fix authorization/service availability; an unreadable store is never an empty store |
| Need to return to local delivery | Disable cloud schedules, wait for workers, reconcile claims and transfer current state before enabling any local writer |

The recovery command does not manufacture successful receipts or adopt provider
results automatically. An uncertain same-day operation remains blocked even
after owner release. The next date can reuse earlier completed quantitative
state. Cloud and local writers must never operate concurrently.

## Tests and final acceptance

```text
python -m pytest tests/test_cloud_production.py tests/test_cloud_runtime.py tests/test_cloud_briefing.py tests/test_cloud_identity.py tests/test_cloud_azure.py tests/test_cloud_publish.py tests/test_cloud_snapshot.py
```

The production-wiring suite uses synthetic market inputs, injected quant
execution, mocked model/speech/Brevo transports and a temporary bare Git repo.
It uses real private bundles, the real composer, audio hashes, static builder,
public verification and sender. The existing numerical suite tests the engine.
No offline result certifies live market availability, OIDC access, voice quality
or email receipt.

After account setup, record several real weekday runs with the PC off, including
a delayed start and a controlled failed probe. Check both language emails, audio
links, unsubscribe, public hashes and incident notifications. Keep the local
production owner until the reviewed cutover. Code preparation and real delivery
acceptance are separate milestones.
