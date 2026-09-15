# Email briefing setup

Email delivery is disabled on a new installation until operator setup.
The project has no subscriber list or email-service credentials bundled with it.
No signup form is rendered while disabled. GitHub Pages serves static files; the
confirmation form and contact records are hosted by Brevo. No always-on project
server or cloud migration is involved in this design.

## What readers receive

One short saved briefing and an immutable MP3 link, in the selected language.
The target is 09:00 America/New_York on FX weekdays. The existing local computer
must be awake, logged in and online. Late login uses the day's catch-up edition.
Generation, Pages deployment and email queues add delay; 09:00 inbox arrival is not
guaranteed. Past missed dates and weekends are not mailed as a backlog.

Public text, calendar context and both audio files must match the frozen local
edition before a campaign can be submitted. A saved send claim prevents a repeat
after interruption. Ambiguous provider responses are marked `review_required`.
Inspect the campaign in Brevo before any operator-assisted recovery. Never delete a
send receipt to retry blindly. `submitted` means provider acceptance, not inbox
delivery, playback or reader consent verification.

## Account-holder steps

1. Create or use your own Brevo account. Review its current plan, sending limits,
   billing and sender requirements. No plan or free-tier eligibility is assumed.
2. Verify the sender identity/domain as requested by Brevo. Supply your legitimate
   sender information and postal details; do not use the examples as real identity.
3. Create separate English and Chinese lists dedicated to confirmed FX briefing
   subscriptions. Do not reuse an existing marketing list or import unconfirmed
   addresses. Readers who subscribe to both language lists can receive both versions.
4. Create a hosted form for each list with explicit briefing consent, double email
   confirmation, a privacy disclosure naming Brevo, and provider anti-abuse controls.
   Test confirmation and withdrawal using your own address. The project does not
   download contacts or implement a separate public signup API.
5. Configure and test the provider unsubscribe footer. The campaign includes Brevo's
   `{{ unsubscribe }}` placeholder. Review suppression/bounce handling in your account.
6. Record the verified sender ID, two list IDs and hosted `https://…sibforms.com/serve/…`
   URLs. Create an API key in the account and keep it out of chat and Git.

Run from the project PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\configure_email.ps1
```

The helper asks for the key using a masked prompt. Paste with Shift+Insert. It asks
for an explicit ENABLE acknowledgement after the operator checks above. Metadata is
saved under ignored `outputs/subscriptions/config.json`; the key is saved separately
in the Windows user environment. This environment storage is plaintext, not a vault.
No network request, signup, campaign, message, publication or task change occurs in
the helper. The form becomes visible after a local reload/public site rebuild.

Configuration presence and format can be checked without contacting the provider:

```powershell
$env:PYTHONPATH = 'src'
python -m fxdash.narrative.subscriptions --check
```

To stop new campaigns and hide the form on the next build:

```powershell
python -m fxdash.narrative.subscriptions --disable
```

Disabling this project does not cancel a campaign already accepted by Brevo, remove
provider records or retract an email. Manage pending campaigns and contact deletion
in the provider account. No addresses, API keys, list IDs or sender settings are
exported in the website API; only explicitly enabled form URLs are public.

## First real acceptance

The email leads with the same short market recap as the new recording. The public
verification check compares that recap as well as the original text and MP3s before
campaign submission. Incomplete older archives can fall back to the numeric summary,
omitting its per-pair `(provisional)` and `（待确认）` labels. Original summaries,
data flags and source material remain in the website details. A copy update does
not resend a date that already has a campaign submission record.

Complete account setup before this check. Use only the owner's confirmed test
subscriptions first. Check that both languages arrive, audio opens, dates match,
unsubscribe works and a repeated task invocation causes no second campaign. The
implementation's mocked tests cannot establish real delivery or provider setup.

The [September 14 release](RELEASE_20260914.md) records the deployed installation's
progress separately. A successful subscription confirmation does not establish
daily campaign delivery, audio-link receipt or unsubscribe acceptance.

References: [hosted double-opt-in forms](https://help.brevo.com/hc/en-us/articles/208771869-Create-a-sign-up-form-in-Brevo),
[campaign creation](https://developers.brevo.com/reference/create-email-campaign),
[submission](https://developers.brevo.com/reference/send-email-campaign-now), and
[unsubscribe pages](https://help.brevo.com/hc/en-us/articles/208772629-Customize-an-unsubscribe-page-to-integrate-into-your-email-campaigns).
