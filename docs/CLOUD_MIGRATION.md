# Cloud migration

## Scope and current stage

The owner approved trying GitHub Actions with private persistent storage on
September 14, 2026. Quantitative definitions, model settings, data sources and
frozen editions stay unchanged. The first stage is an offline Linux readiness
run. The candidate branch can trigger that test on push; manual runs are also
supported once the workflow is on the default branch. It has read-only
repository permission, receives no provider secrets, and
cannot publish the website or send a campaign. Local schedules remain enabled.

The owner subsequently selected code and tests only, with no paid storage
provisioning. No cloud credentials or private seed have been uploaded. Completing
this readiness stage does not enable unattended production delivery.

The first Linux run exposed a host-timezone conversion in the static builder.
That was fixed without changing quantitative outputs. The preparation release
passed 1,042 Windows tests and its GitHub Linux run (one Windows-only skip).
The next code-only stage adds a conditional state interface, persistent action
claims, a guarded email adapter and checkpoint sequencing. Its tests inject
synthetic execution ports and a local SQLite store. The subsequent adapter stage
implements Azure Blob REST access and portable branch publication, tested with
mock HTTP responses and local bare Git repositories. These adapters are not
connected to production accounts.

The next code-only stage adds an in-memory GitHub OIDC token provider and two
production bindings for news collection and recap composition. Token exchange
uses mock HTTP responses in tests. The briefing integration uses synthetic
market data and model responses with the existing source validator and edition
composer. Neither stage authorizes a cloud account or changes local delivery.

The integrated runner is now implemented. Disposable source copies isolate
quant and build paths from the checkout and the local production tree. The
runner installs frozen editions, reuses or creates single-language audio
attachments, builds and inspects the public export, and connects the guarded
publisher and sender. The production and watchdog workflows are checked in with
activation gates off. No account resource, secret or live schedule was enabled.
See [Cloud operations](CLOUD_OPERATIONS.md) for the current entry points.

The free GitHub environment has subsequently been created: `fx-cloud-production`
permits only the `main` branch and has no environment secrets. Both activation
variables are explicitly `false`. These settings were verified by API readback.
The existing Pages environment and local schedules are unchanged. Azure identity,
storage and provider credentials remain unconfigured.

Private storage provisioning, billing approval and account authorization are
separate from this test. Azure Blob Storage is the proposed state store because
the owner already uses Azure Speech. A requested budget target is not a hard
spending cap. No VM is needed.

## Stages and acceptance

1. Run the pinned offline suite on a GitHub-hosted Linux runner. This establishes
   dependency and platform compatibility, not live-source or email delivery.
2. Approve a dedicated private storage container, authorization and retention.
   Use narrowly scoped credentials or a federated identity. Require TLS, block
   anonymous blob access, and avoid printing signed URLs.
3. Capture the allowlisted private seed and verify a restore. Preserve the
   frozen alignment profile, historical HY spread file, source cache, contract,
   input evidence, editions, recordings and all existing send/generation claims.
4. Implement durable checkpoints for paid calls and sends, plus a single-writer
   lock. The claim must reach cloud storage before the external action starts.
   Saving a ZIP only at job completion cannot provide that guarantee.
5. Run a cloud shadow analysis with no campaign submission or public deployment.
   Check freshness, source availability from the cloud IP, unchanged methodology,
   current six-pair results, evidence timing and exact audio links.
6. Publish a reviewed candidate and verify the public bytes. Before enabling the
   cloud sender, stop the local production sender, take a final state snapshot
   and confirm only one production owner. Keep local read-only serving available.
7. Observe consecutive working-day runs with the personal computer off. Test
   missed schedules, storage outage, interrupted generation, ambiguous email
   submission and replay. Never blindly resend an uncertain campaign.

The target is 09:00 America/New_York on FX weekdays. Start data work earlier;
local live runs have taken roughly 11 to 40 minutes. Preserve the actual morning
evidence cutoff. A late run must identify itself as catch-up. Actions schedules
can be delayed, and queueing and provider processing prevent an exact inbox-time
guarantee. Independent missing-run alerts are needed because a failed publisher
cannot update a previously exported static status panel.

## Private seed helpers

From the project root, with PYTHONPATH set to src:

    python -m fxdash.cloud.snapshot inspect --root .
    python -m fxdash.cloud.preflight --root .

Both operations are read-only and make no network calls. Reports contain counts,
dates and validation codes, not raw settings or subscriber information.

To prepare a private bundle, first choose an ignored destination whose parent
already exists. Use a new filename, and wait for active writers to finish:

    python -m fxdash.cloud.snapshot pack --root . --bundle outputs/cloud/bootstrap.zip
    python -m fxdash.cloud.snapshot restore --bundle outputs/cloud/bootstrap.zip --destination outputs/cloud/restored
    python -m fxdash.cloud.preflight --root outputs/cloud/restored

The destination of restore must not already exist. The helpers check allowed
paths, file sizes and hashes, refuse links and duplicates, exclude code, notes,
logs and temporary files, and scan for known credential values before packing.
Settings such as sender IDs and the sender footer remain private inside the
bundle. Do not upload it as a public Actions artifact or dependency cache.

Packing compares the source inventory again to detect concurrent changes. A
failed pack or restore may leave a partial file or directory; it must not be
uploaded or treated as an accepted seed. Use a different fresh destination on a
retry. Do not delete generation or send receipts to make a retry proceed.

These helpers provide migration packaging only. They do not implement the
runtime cloud journal, schedule, cloud publisher, or production sender.

## Runtime protection and offline simulation

`src/fxdash/cloud/state.py` defines authoritative reads and atomic conditional
writes. `SQLiteStore` implements this contract on a local disk for fault tests.
It requires explicit first-time creation and refuses to reopen a missing store
as an empty replacement. This SQLite file cannot act as shared state between
independent GitHub runners. Do not put it in the repository or an Actions cache.

`journal.py` keeps one persistent owner and records each external-operation
claim before its callback begins. Successful receipts contain only an artifact
hash, campaign ID or submission flag. Provider exceptions, email HTML and sender
details are not copied into the journal. Private stage artifacts are stored
separately by their SHA-256 hash; the journal is completed only after the result
has been saved. A changed input or missing artifact blocks replay.

The owner lock has no automatic expiry. Normal completion releases it. A killed
process can leave an occupied lock, which requires operator reconciliation and
confirmation that the old worker stopped. The explicit reconciliation command
requires an exact reviewed journal hash, matching owner and GitHub run attempt,
a scheduler-stop attestation and a fresh completed-run response from GitHub.
It releases only ownership and preserves every claim. There is no automatic
lock-breaking command. This version prioritizes avoiding concurrent writers over unattended
recovery. Storage reads, claim writes or result writes failing all stop progress.

| Interruption | Behavior on a later attempt |
| --- | --- |
| Claim write fails before the external call | No external call starts |
| External call times out or the worker loses its response | Keep the uncertain claim; require review |
| Result write fails after a provider accepted the request | Keep the pending claim; do not resubmit |
| Saved result is available | Reuse it without another callback |
| Saved result is missing or altered | Stop; do not regenerate silently |
| A second worker starts while the owner is present | Refuse ownership |

`delivery.py` wraps the existing subscription sender through its injected
provider interface. It retains double opt-in settings, the current New York
weekday check, frozen edition content, and public text/audio verification. It
adds separate durable campaign-creation and send claims for each date/language.
The provider is created lazily. An already completed campaign can be recovered
without constructing a provider or submitting it again. Changing text, recipient
list settings or edition hashes does not create another daily campaign.

The adapter also imports any local receipts for the current delivery date before
the local sender skips them. A confirmed legacy submission becomes a durable
completed claim. Missing fields, a partial submission or an unreadable receipt
become blocking records. Existing cloud claims are never overwritten by a seed.
This protects against a later runner starting from an older local snapshot; it
does not replace the final single-owner cutover or a complete paid-call audit.

`pipeline.py` sequences injected input, quant, news, recap, bilingual audio and
site-build callbacks. It stores each output before allowing the next callback.
The default shadow mode never invokes publishing, public verification or email.
The explicitly requested delivery mode checks the New York weekday and 09:00
boundary before publishing. It verifies the public version anew on every retry
and requires durable email receipts before reporting provider submission. The
email adapter rechecks the date and verification age before each provider call.
Provider submission remains distinct from an inbox receipt.

`runtime.py` now binds this orchestration to `ports.py`. The news, composer,
speech attachment, static builder, publisher, public probe and email sender use
the existing production implementations. Quant runs the frozen engine in a
copied, verified workspace. Paid callbacks retain bounded budgets internally:
three model requests with one attempt each and one request per new audio
language. A stage-level claim alone cannot control hidden callback retries.
The cloud command defaults to shadow mode. Delivery additionally requires a
persisted single-owner cutover attestation; workflow variables alone cannot
authorize sending.

Run the synthetic checks with:

    python -m pytest tests/test_cloud_runtime.py

The suite covers independent store connections, lost results, ambiguous email
submissions, old seeds, changed recipient settings, corrupt state, public CDN
lag, date rollover and both New York UTC offsets. Real Brevo, speech, model and
storage endpoints are not called. The existing local production entry points
do not import this runtime.

## Remaining account and live acceptance work

1. After approval, validate the Blob adapter against the private account with
   scoped authentication, account-level anonymous access disabled, retention,
   recovery and cost controls. Register and constrain federation trust before
   using the OIDC provider. Its mocked tests do not establish account access.
2. Verify the prepared GitHub environment, configure its credentials and OIDC
   trust, seed the private store and enable personal failed-workflow notifications.
   Check private-state growth, service quotas and source licensing before use.
3. Exercise the implemented reconciliation command and runbook against the
   authorized account. Never erase a journal to make a blocked run proceed.
4. After storage approval, run a no-send live shadow, then the reviewed cutover
   and consecutive-day acceptance described above. The PC still owns production
   until those checks and approvals are complete.

## Azure Blob adapter

`azure_blob.py` implements the state interface using the Blob REST API, pinned
to service version 2023-11-03. It accepts an account name and container name,
constructs only the primary `blob.core.windows.net` HTTPS endpoint, and limits
objects to the `fxdash-v1/` prefix. Custom endpoints, SAS URLs, account keys,
secondary replicas, arbitrary query strings and redirects are not accepted.

Construction does not fetch a token or make a request. Each explicit operation
obtains a bearer token from the supplied callable. The default callable reads
only `FXDASH_BLOB_ACCESS_TOKEN`. The token goes in an authorization header and
is excluded from snapshots and logs. The HTTP session does not inherit `.netrc`
credentials or proxy settings. `GitHubStorageToken` can now be supplied as this
callable to acquire short-lived tokens automatically. It must be constructed
explicitly; no ambient environment flag changes the Blob adapter's default.
Federated identity registration remains separate work. No identity or secret
was created in this stage.

Before reading or writing a blob, the adapter checks container properties and
requires private access. It uses `If-None-Match: *` for a new object and an exact
quoted `If-Match` ETag for an update. Failed reads are errors; only Azure's explicit
`BlobNotFound` response represents a missing object. This distinction prevents
an authorization failure from being mistaken for an empty send history.

Writes carry SHA-256 metadata and a transport MD5 checksum. Reads validate the
length, both checksums and the encrypted block-blob response. Partial responses,
encoded bodies and oversized objects are rejected. There are no automatic HTTP
retries, deletion calls, container creation or listing operations. A failed or
ambiguous write remains subject to the journal's reconciliation rules. These
checks do not replace account-level access policy or storage retention controls.

## Short-lived storage credentials

`identity.py` exchanges a GitHub job identity for a token scoped to
`https://storage.azure.com/.default`. Construction makes no request. The first
explicit call reads the job's `ACTIONS_ID_TOKEN_REQUEST_TOKEN` in memory, obtains
an assertion for `api://AzureADTokenExchange`, and submits it to the fixed
tenant-specific Microsoft identity endpoint. Tenant and client identifiers must
be GUIDs. The identity service validates the assertion and federation policy;
the application does not infer authorization from decoded JWT claims.

Only public GitHub-hosted runner URLs matching the supported
`https://<host>.actions.githubusercontent.com/.../_apis/oidc/token?api-version=2.0`
form are accepted. Credentials, ports, fragments, extra query parameters, custom
hosts and redirects are refused. If GitHub changes this endpoint format, the
adapter stops until the new format is reviewed. Sovereign-cloud endpoints and
GitHub Enterprise Server are outside this adapter's scope.

The storage token stays in process memory. A monotonic clock accounts for request
latency and refreshes at least two minutes before expiry. Concurrent callers
share one exchange. A failed refresh clears the cached token; there is no stale
fallback, CLI login, managed-identity discovery or automatic HTTP retry. Responses
are bounded and duplicate JSON keys are rejected. Error messages contain fixed
codes, not URLs, tokens or provider response bodies. The job request token is
also included in the private snapshot and public-export secret scan.

Before activation, configure a protected GitHub environment, restricted branches
and the exact subject accepted by Entra. Verify the repository's actual subject
format, which can include immutable owner/repository IDs. Assign only the needed
container data permissions. The current readiness workflow has no `id-token:
write` permission and does not instantiate this provider. No access token,
federation registration or Azure account request was made by these tests.

## Production news and recap bindings

`BriefingPorts(journal, day, output_dir)` supplies `news` and `recap` callbacks for
`pipeline.Ports`. Its output directory must belong to a separately restored job
workspace. Do not point a future cloud worker at an actively changing local
production directory. The two callbacks do not restore state, run regressions,
install files, render speech, publish the site or send a campaign.

Each callback requires the cloud journal's current owner and a pending claim
whose fingerprint matches all prior stage artifacts. Calling a port directly
without that claim cannot collect news or construct a model client. The news
stage first checks dated legacy editions, preparation claims, saved packets and
drafts. An intact frozen edition keeps its content and hash. An intact legacy
draft is revalidated without another model request. Missing, corrupt, mismatched
or incomplete legacy generation records stop for review. The port does not erase
claims or silently collect a replacement packet.

With no prior attempt, the port reads the existing six-pair snapshot, calls the
production news collector, and attaches the release calendar. Attribution and
news observation times are recorded at the actual collection times. All six
pairs must share a usable attribution date. Sources must fall inside the captured
observation interval. Empty or wholly failed news collection stops preparation.

Eligible pre-09:00 New York evidence retains the morning-edition classification,
even if text generation finishes later. Later evidence produces a catch-up
edition with the actual news cutoff and `scheduled: false`. Collection crossing
09:00 cannot be presented as the original morning packet. Both UTC offsets and
midnight rollover are tested. This does not certify an exact publication time.

Model use defaults to off. The explicit `use_model=True` binding constructs the
existing Gemini client with three requests total, one attempt per request and a
45-second request timeout. Ownership and the date are rechecked before each
request. The existing prompt, source/excerpt validator, bilingual composer and
human-readable recap view are reused unchanged. Quantitative definitions and
factor settings are untouched. The result is a private `cloud-briefing-v1`
artifact; the news packet is saved as `cloud-news-v1`. Model requests are not
repeated if the journal already records the stage or an uncertain attempt.

Run these offline checks with:

    python -m pytest tests/test_cloud_identity.py tests/test_cloud_briefing.py

The original briefing tests use the real validator and composer with synthetic
quant/audio/build ports. The additional `test_cloud_production.py` suite connects
real private bundles, composition, audio manifests, static builds, local Git,
public-byte checks and the sender. Market downloads, model/speech transports and
Brevo are mocked; the quant subprocess boundary is injected. It tests fresh-worker
resume without repeated model calls, synthesis, pushes or campaign submissions.
Passing these tests does not certify live data access, neural voice quality,
Azure authorization, Pages deployment latency or inbox arrival.

## Portable static publisher

`publish.py` works on Windows and Linux and consumes an already built candidate.
It does not collect news, calculate models, synthesize audio or run the builder.
The existing builder remains responsible for producing the candidate from the
approved snapshot. This separation keeps the push step tied to reviewed bytes.

Inspection derives the allowed API files from the frontend's existing request
set, checks the build manifest, compares static assets with the checked-out
source, and allows only the existing dated audio paths. It rejects extra files,
links, private output directories and known credentials. It calculates a hash
over the complete file inventory. This is a bounded export check, not a general
classifier of sensitive content inside an otherwise valid public JSON field;
the builder's public-data contract and tests remain necessary.

The command-line entry point is local-only:

    python -m fxdash.cloud.publish --candidate <fresh-build-directory>
    python -m fxdash.cloud.publish --candidate <fresh-build-directory> --stage-dir <new-directory> --expected-sha256 <reviewed-hash>

Staging exclusively creates a new directory, copies the reviewed bytes and
creates a single-parentless Git commit. It never overwrites or removes an old
site or checkout. An interrupted stage may leave a partial new directory; use a
fresh destination and inspect the old one before any manual cleanup. The CLI
has no push option, even when publishing credentials are present.

The explicit Python `push` operation is intended for the guarded production
publisher port. It requires a caller-supplied PAT and the previously observed
40-character `gh-pages` commit. It is restricted to this project's fixed GitHub
remote and branch. It rechecks the remote and uses an exact `--force-with-lease`
condition, so a concurrent publisher's update is not overwritten. A timeout or
uncertain response does not trigger another push. The durable journal must own
this outward operation when the production port is connected.

Authentication is injected into the child Git environment, not its arguments or
repository config. Ambient credential helpers, tracing, askpass, hooks, global
attributes, signing and global Git configuration are disabled. Known provider
secrets are removed from that child environment. No PAT is generated, read from
the workstation's credential manager, or uploaded by this adapter.

For the current branch-based Pages setup, the adapter accepts explicitly supplied
fine-grained/classic PAT formats and rejects the default Actions token. GitHub
documents that a `GITHUB_TOKEN` push does not trigger a Pages build. An alternative
future deployment can use the official Pages artifact workflow, but changing
the publishing source is a separate deployment decision. A successful push
returns `public_verified: false`; the existing public text/audio probe must
succeed before the email adapter can submit a campaign.

Run the adapter tests without external accounts:

    python -m pytest tests/test_cloud_azure.py tests/test_cloud_publish.py

The Azure tests emulate the service protocol, including a committed write whose
response is lost. The publisher tests run real Git against temporary local bare
repositories and race another writer between the remote probe and push. These
results establish adapter behavior; they do not establish Azure authorization,
GitHub Pages deployment, live-input freshness or inbox delivery.

## Operational boundaries

The local machine currently owns production delivery. The cloud readiness
workflow runs manually or on the isolated cloud-readiness candidate branch,
with no automatic schedule and no delivery capability. The separate production
and watchdog definitions have schedules but all jobs are disabled until the
documented repository activation gates are explicitly set. Enabling a cloud
production job before storage, claims and cutover are accepted is unsafe.

GitHub access, Azure authorization and any billing acknowledgement require the
account holder. Never paste keys into chat. Private state is not committed to
the public repository. Keep Brevo's existing double-opt-in and unsubscribe flow.

## Primary references

- [Actions scheduling](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Actions cache security](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Pages deployment workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [Azure Blob concurrency](https://learn.microsoft.com/en-us/azure/storage/blobs/concurrency-manage)
- [Azure Put Blob](https://learn.microsoft.com/en-us/rest/api/storageservices/put-blob)
- [Azure Get Blob](https://learn.microsoft.com/en-us/rest/api/storageservices/get-blob)
- [Private container properties](https://learn.microsoft.com/en-us/rest/api/storageservices/get-container-properties)
- [Azure bearer-token authorization](https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory)
- [GitHub OIDC with Azure](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure)
- [GitHub OIDC claims and job tokens](https://docs.github.com/en/actions/reference/security/oidc)
- [Microsoft federated client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [GitHub Pages branch publishing](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [Git push lease semantics](https://git-scm.com/docs/git-push)
- [Azure Blob pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
