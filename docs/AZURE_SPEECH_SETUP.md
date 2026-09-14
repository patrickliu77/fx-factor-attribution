# Neural speech setup

The Azure adapter is implemented but remains opt-in. No Azure resource or key is
bundled with the project. The existing Windows voice stays selected until an
operator explicitly changes `FXDASH_AUDIO`. No cloud request is made by opening a
page, checking local configuration or building the static site.

## Account step

Create a Speech resource in your own Azure subscription and obtain its key and
region. The [Microsoft quickstart](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/get-started-text-to-speech)
describes that step. Account login, identity verification and subscription choices
belong to the account holder. Do not send the key in a chat or commit it.

Where available, choose the F0 tier. Microsoft's [pricing page](https://azure.microsoft.com/en-us/pricing/details/speech/)
lists 500,000 neural text-to-speech characters per month for F0. Confirm the actual
tier, region, quota and billing settings in your account. Code cannot establish
free-tier eligibility. Each synthesis request is limited to 4,000 transcript
characters; SSML and additional requests may contribute to billed usage.

## Configure locally and audition

Use PowerShell from this repository with the project Python environment activated
(the same environment used by `ops/serve.ps1`). These commands do not publish:

```powershell
# The dot before the path keeps settings in this PowerShell process.
# A hidden prompt asks for the key. No key is put into command history.
. .\ops\configure_speech.ps1
$env:PYTHONPATH = 'src'

# Local presence/format checks only; no request or validation of quota.
python -m fxdash.narrative.azure_speech --check

# Explicitly request two short illustrative recordings, about 30 seconds each.
# Never overwrites an existing file; use a fresh directory for another audition.
python -m fxdash.narrative.azure_speech --preview outputs/speech-audition-01
```

Open `en.mp3` and `zh.mp3` in that directory yourself. No speaker playback starts
automatically. The sample is clearly illustrative, with no claim to describe a
real market day. A preview accepts measured durations from 10 to 60 seconds.

English uses `en-US-GuyNeural` with `newscast`; Chinese uses `zh-CN-YunyangNeural`
with `narration-professional`. SSML sets a slightly slower pace and paragraph
pauses. [Voice/style support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts)
and [SSML controls](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice)
are documented by Microsoft. Naturalness requires listening; unit tests do not
establish pronunciation or perceptual quality.

## Enable after listening

To test one full saved edition in the configured shell:

```powershell
$env:FXDASH_AUDIO = 'azure'
python -m fxdash.narrative.audio_briefing --latest
```

This makes up to one synthesis request per missing language on this attempt, reads
frozen evidence and saves new `audio-v2` attachments. It does not publish. Full
recordings must measure 60 to 180 seconds. Failed cloud generation leaves text
available and does not substitute a Windows recording silently.

After approving the full recording and the account quota, use
`. .\ops\configure_speech.ps1 -Persist -EnableScheduled` to save settings for the
task user. This asks for the key again and changes user environment variables,
without changing the task definition or launching the task. User environment
storage is plaintext, not a credential vault. At startup, both scheduled FX workers
read only `FXDASH_AUDIO`, `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` from the task
user's saved environment. They do not need a new desktop login to receive updates.
An unreadable user environment disables optional audio for that run; text delivery
can continue. Existing interactive shells still need their environment refreshed.
Use the read-only audio check and real task logs to verify the next delivery.

If the correct key and region are already saved and the audition is approved, the
backend alone can be selected without re-entering the key:

```powershell
[Environment]::SetEnvironmentVariable('FXDASH_AUDIO', 'azure', 'User')
```

Use `'off'` in place of `'azure'` to stop future synthesis. Neither command starts a
task, changes its schedule or removes saved recordings. The configuration helper
rejects hidden control characters, including an accidental Ctrl+V character; use
Shift+Insert to paste into its masked prompt. Saving settings does not verify Azure
authentication, quota or billing.

There is no additional scheduled task. Existing dispatchers use the configured
backend. Successful files are reused; failed synthesis permits at most two attempts
per language/edition/version with a 15-minute cooldown. Preview invocations are
manual and are outside that edition retry budget. These limits are not a global
monthly spending cap; manage resource quota and billing in Azure as well.

The service uses one [REST synthesis request](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech)
per render, with redirects and automatic HTTP retries disabled. A response is
size-bounded, and its duration is measured by FFprobe. Provider errors never expose
request headers or response bodies in saved metadata. Only the generated transcript
is submitted to Speech; packet files, private notes and API credentials are not
included in the request body.

## Existing recordings

`audio-v1` remains immutable and uses the original Windows script. New concise
copy is specific to `audio-v2`. Local media routes retain both versions, and static
exports retain verified versions for the editions included in that export. Selecting
`FXDASH_AUDIO=off` disables new synthesis without hiding already-saved files.
