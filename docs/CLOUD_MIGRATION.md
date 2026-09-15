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

## Operational boundaries

The local machine currently owns production delivery. The cloud readiness
workflow runs manually or on the isolated cloud-readiness candidate branch,
with no automatic schedule and no delivery capability. Enabling a cloud
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
- [Azure Blob pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
