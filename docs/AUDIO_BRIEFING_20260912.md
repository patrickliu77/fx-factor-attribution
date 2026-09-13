# Saved audio briefings

The News page now has English and Chinese recordings. Each MP3 belongs to a saved
edition, with a transcript, measured duration, voice label and actual recording
time. The language selector chooses the recording language. Opening a page does
not play or download an MP3; playback begins on request. The transcript stays
readable if playback fails.

## What runs automatically

The existing morning and catch-up dispatchers prepare optional audio before
publishing a newly saved edition. A later catch-up check can retry a missing audio
attachment or its failed publication without regenerating the text. The original
text push receipt is preserved when only audio is added later. There is no new
scheduled task and no fixed wake-up requirement.

Generation still runs on the user's host. A powered-off computer cannot produce
new recordings. GitHub Pages serves the last published files without the host
being online. Independent multi-agent orchestration is not implemented by this
change.

## Content and evidence

`audio_script.py` prepares spoken copy deterministically. It checks the saved
edition, its packet hash, embedded evidence, all six currency pairs and their dates.
It then selects the three largest absolute daily FX moves, states their signed
log-return basis points, provisional status, largest factor contribution and
residual. Numbers are read from the packet, with no new regression or LLM call.

At most one already-saved news interpretation is included after rerunning the
existing source and wording checks. Those checks do not establish factual accuracy
or causality. If no usable interpretation is available, the recording says so.
The closing section lists research checks about dates, independent evidence,
sensitivities and residuals. There is no economic-calendar feed or invented list of
today's scheduled releases. Every recording states its data date and is labelled
as a synthetic voice.

## Local prerequisites

- Windows PowerShell with `System.Speech`, with enabled `en-US` and `zh-CN` voices.
- `ffmpeg` with `libmp3lame`, and `ffprobe`, available on the task user's PATH.
- The normal project Python environment. No additional Python package or API key.

The renderer uses [Microsoft System.Speech](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer)
to write WAV files, then [FFmpeg](https://ffmpeg.org/ffmpeg.html) to encode mono MP3.
Temporary WAVs are removed. All subprocesses are windowless and never write to
the speaker device. Installed desktop voices sound less natural than some hosted
neural voices; no premium voice service is configured here.

Windows defaults to enabled. Set `FXDASH_AUDIO=off` in a task's environment to skip
new synthesis; saved recordings remain readable. `FXDASH_AUDIO=windows` explicitly
selects this backend. Other operating systems default to disabled and can still
serve already-generated MP3s.

From the project environment with `PYTHONPATH=src`:

```powershell
# Read-only status for the latest saved edition.
python -m fxdash.narrative.audio_briefing --latest --check

# Generate missing audio, without publishing or regenerating the saved text.
python -m fxdash.narrative.audio_briefing --latest

# Generate and publish optional attachments, reusing ready recordings.
python -m fxdash.narrative.audio_briefing --latest --publish

# Explicitly attach audio to a saved historical catch-up edition.
python -m fxdash.narrative.audio_briefing --date 2026-09-11 --mode catchup
```

The static build itself remains read-only with respect to pipeline outputs. It
copies only recordings verified by the same endpoint that serves the local player.
An ordinary `ops/publish.ps1` run publishes existing recordings; it does not create
them. Synthesis is performed by the dispatchers or explicit commands above.

## Identity, retries and failure boundaries

Attachments live under
`outputs/briefing/audio/{mode}/{date}/{edition-hash-prefix}/audio-v1/`.
The directory uses a shortened hash to avoid Windows path-length issues; full
edition hashes are required in manifests and public URLs. A prefix collision is
refused. Each language retains its own transcript, MP3, manifest and attempt
records. `publication.json` tracks the audio bundle separately from the original
text receipt.

Successful attachments are immutable. A failed or interrupted attempt can retry
after 15 minutes, with at most two synthesis attempts per language and edition
version. A second failure remains visible and needs investigation; there is no
automatic unbounded loop. A corrupted successful attachment is withheld, never
silently overwritten. Fixes to already-published spoken copy require a new script
version. One unavailable language does not hide the other.

Speech synthesis has a 180-second timeout, encoding 45 seconds, and duration
probing 20 seconds per language. Normal local rendering is much faster. A failed
audio step leaves text publication enabled. Measured output must be 60 to 180
seconds; modest tempo adjustment is allowed for unusually fast or slow voices.
Publication retries reuse verified files, with a 15-minute cooldown after a failed
audio-only push. A push receipt alone does not prove that Pages has deployed it.

## Initial observation

The first recordings were explicitly generated on September 12 from the frozen
September 11 catch-up edition. The English recording is 144.488 seconds and the
Chinese recording is 149.552 seconds. This demonstrates generation of real MP3s,
not a historical September 11 broadcast or a naturally scheduled audio run.
Natural automatic deliveries still need to accumulate their own evidence.

Regression tests cover signed units, source matching, missing inputs, partial
language failures, interrupted retries, immutable files, separate publication,
both dispatcher integrations, safe media routes and static export. The real-player
audit checks English and Chinese at desktop/mobile widths, decoding, seeking,
transcripts, error messages and absence of autoplay.
