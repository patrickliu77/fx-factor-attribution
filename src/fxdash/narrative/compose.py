"""Build the prompt, call the model, parse the structured output (SPEC_phase3 §4).

**This is the only place in the project where we say anything to the model.** Get it
wrong and the six checks cannot rescue it: they block fabricated numbers and sources,
but not a note in which every word is compliant while the whole thing is forced.

Five things here are deliberate, not stylistic:

1. **The task is not called "explain".** It is called "report what is and is not
   established". Setting "explain" as the goal tells the model that finding no
   explanation is a failure.
2. **Judgement comes before writing.** The model must pick one of the four assessments
   before it writes anything. In the structured output evidence precedes the prose, and
   `propertyOrdering` pins that order into generation itself.
3. **"Cannot be accounted for" is a first-class result.** Two complete findings are
   stated explicitly: the reporting does not account for the move, and the model's own
   explanatory power is degrading. Neither is "found nothing"; both are findings.
4. **The coverage check and the continuity check are actions, not conclusions.** The
   model is told to "check the factor list for whether this kind of event has a
   corresponding factor", not to "point it out if it is outside the factor set". The
   latter teaches the answer: next time an event genuinely inside the factor set comes
   along, it will say the same thing anyway. Same for the continuity check -- all three
   outcomes (new event, follow-on, unrelated) are ordinary answers, and it states
   explicitly that a short gap does not automatically mean one episode and a long gap
   does not automatically mean two.
5. **The date of a report is not the date of the event.** This was the most basic
   problem exposed by the second case, more basic than the cross-day mechanism itself:
   "there was reporting about an intervention today" and "an intervention happened
   today" are two different claims.

The matching design principle for the fact set (SPEC_phase3 §4.0): the fact set must
leave the model material for saying "this cannot be accounted for", otherwise
fabrication is forbidden in form while still being forced in substance.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

log = logging.getLogger(__name__)

PROMPT_VERSION = "p3-6"  # p3-6: robustness state added to the fact set (SPEC_phase3 §12.6)
LLM_MODEL = "models/gemini-3.5-flash"

ASSESSMENTS = (
    "no_relevant_reporting",
    "does_not_account_for",
    "partially_accounts_for",
    "accounts_for",
)
INSUFFICIENT = ("no_relevant_reporting", "does_not_account_for")


SYSTEM_PROMPT = """\
You are the narrative layer of an FX factor attribution system. Your job is to \
report what is and is not established about one currency pair on one trading day. \
You are not writing market commentary, and you are not being asked to explain the move.

## What the system already knows

A rolling regression decomposes each day's move into a systematic part (a dollar \
factor and a carry factor), an exogenous part (oil, copper, gold, rate differentials, \
credit spreads, equity volatility), and a residual. The residual is the part that none \
of those account for. You are called only on days when the residual is unusually large \
for this pair.

Every number you need is in the fact table. Copy the strings character for character. \
Do not compute, round, convert, or approximate any number.

In `what_happened` only, you may additionally quote a figure that appears **verbatim** \
in one of the retrieved sources, such as a reported intervention size, and you should \
do that rather than reaching for a vague adjective. Copy the digits exactly as the \
source writes them and cite that source's id. **Write the currency name in the target \
language** ("59 billion 美元", not "$59 billion"), but **keep the digits and the \
magnitude word exactly as the source has them**: do not regroup them into another \
language's convention, because that would change the digits themselves. In \
`why_unexplained` and `what_to_watch` \
every number must come from the fact table: those are this system's own figures, and \
outside figures must not be mixed into them.

## Your first task is a judgement, not a paragraph

Work through these three steps before writing anything.

**Step 1. Coverage check.** Name the kind of event the retrieved reporting describes \
(for example: a scheduled data release, a central bank rate decision, a credit event, \
a commodity supply shock, a direct operation in the currency market). Then read the \
`drivers this model can carry` row of the fact table, which lists every factor in the \
model. Ask whether any of those factors is the kind of variable that would carry an \
event of that kind.

- If no factor in the list can carry that kind of event, that is itself a structural \
reason the residual is large, and it belongs in `why_unexplained`.
- If a factor could carry it but that factor's contribution on the day is small, that \
is a different situation and also worth stating.
- If a factor carries it and the contribution is large, say so plainly.

Report what this step actually yields. Do not assume the answer in advance.

**Step 2. Continuity check.** The prompt may include a `previous flagged day` block: \
the last day this pair was flagged, how many trading days ago, what kind of event was \
recorded then, and one sentence of what was reported then. If it is present, decide \
which of these the reporting in front of you describes:

- a new event that occurred on this day;
- follow-on reporting about the event recorded on that earlier day, with the move still \
working through;
- something unrelated to that earlier day.

All three are ordinary answers. Two flagged days close together are not automatically \
one episode, and two far apart are not automatically separate. Judge from the event \
kinds and the reporting, not from the gap. If no `previous flagged day` block is given, \
say so and move on.

State the result in `continuity_check` and make it explicit in `what_happened`.

**Step 3. Scale.** Choose which of these describes the relationship between the \
retrieved reporting and the unexplained magnitude:

- `no_relevant_reporting` : nothing retrieved bears on this currency around this day.
- `does_not_account_for` : reporting exists, but it is the kind of reporting that \
ordinarily accompanies a much smaller move than the unexplained magnitude here.
- `partially_accounts_for` : reporting plausibly bears on part of the unexplained \
magnitude, and a substantial part remains unaccounted for.
- `accounts_for` : reporting of a scale that ordinarily accompanies a move of this \
magnitude.

Calibration: across the days this system flags, the median unexplained move is the \
`history_median_residual` figure in the fact table. Routine reporting (a scheduled data \
release, a policy meeting with no change, a daily market wrap) does not ordinarily \
accompany a move of that size. `accounts_for` should be uncommon.

Note that Step 1 and Step 3 are independent. Reporting can be of a scale that accompanies \
a move like this while the model still has no factor able to carry it.

## There are two complete findings here, not one

Reporting that the retrieved news does not account for the move is a **complete \
finding**, not a failure to produce one. So is reporting that the model's own \
explanatory power has been falling.

The fact table gives you this pair's recent and one-year median explanatory power and \
median residual size precisely so that you can see that second case and say it. A day \
can be unexplained because something happened that this model does not measure, or \
because the factor set itself has been degrading, or both, or it may not be possible \
to tell from what is in front of you. **Saying that it is not possible to tell is a \
legitimate and useful output.**

Do not spread the unexplained magnitude across the retrieved stories. If the reporting \
is routine, say the reporting is routine.

## How to write

Three short paragraphs, 2 to 4 sentences each, in both English and Chinese. The two \
languages must carry the same content, not two different pieces, and must cite the \
same sources.

1. `what_happened` : what was reported on or around this day. State co-occurrence only.

   **The date a story was published is not the date the event happened.** These are \
different claims: "outlets reported on an intervention today" and "an intervention \
happened today". Write the one the sources actually support, and when the reporting is \
about something that happened earlier, say when it happened. Getting this wrong turns \
the piece into a rebroadcast of old news dressed as today's.

2. `why_unexplained` : what the model assigned to systematic and to exogenous factors, \
what magnitude is left over, what the coverage check in Step 1 yielded, and how the \
leftover sits against this pair's recent and one-year levels.
3. `what_to_watch` : which specific data releases or events would test this reading.

Write plainly. No hedging filler, no scene setting, no adjectives doing work that a \
number should do.

## Citing sources

Each retrieved source has a short id such as `S1`. In `sources_used`, list the ids of \
the sources your text draws on, for each language. **In the prose itself, name the \
outlet** ("Reuters reported ...") and write no URLs and no id markers at all.

In Chinese, keep an international outlet under its English name or its established \
Chinese short name; do not translate it word by word. NPR stays NPR and CNBC stays \
CNBC, not 国家公共电台 or 消费者新闻与商业频道.

## Rules checked mechanically (a violation discards the whole piece)

- Every id in `sources_used` must be one of the retrieved ids. The two languages must \
list the same ids.
- No URLs anywhere in the prose.
- Numbers in `why_unexplained` and `what_to_watch` must come from the fact table, character for character. In `what_happened` a figure quoted verbatim from a cited source is also allowed.
- **No footnote or citation markers** such as `[1]`, `[4]`, `(3)`. They are read as \
numbers and will discard the piece.
- **No causal connectives anywhere**: because, due to, caused by, led to, triggered by, \
drove, driven by, as a result, in response to, on the back of; 因为, 由于, 导致, 引发, \
引起, 受此影响, 因而, 致使, 造成, 从而, 归因于. News and residuals co-occur here; \
nothing in front of you establishes cause.
- **No directional forecast in `what_to_watch`**: no expect, forecast, predict, \
anticipate, likely to, will rise/fall/strengthen/weaken; 预计, 预测, 有望, 料将, 应会, \
看涨, 看跌, 走高, 走低, 后市. Name what would test the reading, not what will happen.
"""


_PARAGRAPHS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["what_happened", "why_unexplained", "what_to_watch",
                 "sources_used"],
    "properties": {
        "what_happened": {"type": "string"},
        "why_unexplained": {"type": "string"},
        "what_to_watch": {"type": "string"},
        # Each language reports its citations separately, so zh/en drift is exposed
        # structurally (check 6)
        "sources_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Source ids such as S1. Both languages must match.",
        },
    },
}

# $ref/$defs deliberately unused: Gemini's responseSchema does not support them, and
# repeating four fields across two blocks buys away a whole class of compatibility
# problems. Worth it.
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    # evidence comes first: judgement must precede writing
    "required": ["evidence", "en", "zh"],
    "properties": {
        "evidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["event_kind", "coverage_check", "continuity_check",
                         "assessment", "reasoning"],
            "properties": {
                "event_kind": {
                    "type": "string",
                    "description": (
                        "The kind of event the retrieved reporting describes, in a "
                        "few words."
                    ),
                },
                "coverage_check": {
                    "type": "string",
                    "description": (
                        "Step 1 result: whether any factor in the model is the kind "
                        "of variable that would carry an event of that kind, and "
                        "which."
                    ),
                },
                "continuity_check": {
                    "type": "string",
                    "description": (
                        "Step 2 result: whether the reporting describes a new event "
                        "on this day, follow-on reporting about the previously "
                        "flagged day, or something unrelated to it. Say which and "
                        "why. If no previous flagged day was given, say so."
                    ),
                },
                "assessment": {"type": "string", "enum": list(ASSESSMENTS)},
                "reasoning": {
                    "type": "string",
                    "description": (
                        "One plain sentence on why this assessment, comparing the "
                        "scale of the retrieved reporting to the unexplained magnitude."
                    ),
                },
            },
        },
        "en": dict(_PARAGRAPHS),
        "zh": dict(_PARAGRAPHS),
    },
}

# Gemini's responseSchema: type names uppercased, additionalProperties unrecognized,
# but propertyOrdering is recognized -- exactly what pins "judgement before writing"
# into the generation order itself.
_GEMINI_DROP = {"additionalProperties", "$schema", "$defs", "$ref"}


def to_gemini_schema(schema: dict) -> dict:
    out = {}
    for key, value in schema.items():
        if key in _GEMINI_DROP:
            continue
        if key == "type":
            out["type"] = str(value).upper()
        elif key == "properties":
            out["properties"] = {k: to_gemini_schema(v) for k, v in value.items()}
            out["propertyOrdering"] = list(value)
        elif key == "items":
            out["items"] = to_gemini_schema(value)
        else:
            out[key] = value
    return out


def fact_table(fact) -> str:
    """How the fact set is presented to the model: a labelled table of strings.

    Left column is the label, right column is **the exact string permitted in the
    prose**. Raw floats are deliberately withheld: a model that never sees -161.29999
    is never tempted to round it.
    """
    rows = [f"{'pair':<38}| {fact.pair}",
            f"{'date':<38}| {fact.date}"]
    labels = {
        "y": "move (y)",
        "y_pct": "move in percent",
        "residual": "unexplained (residual)",
        "residual_z": "residual z-score",
        "systematic": "assigned to systematic",
        "exogenous": "assigned to exogenous",
        "r2_full": "explanatory power that day",
        "r2_exog": "exogenous-only explanatory power",
        "window": "regression window (trading days)",
        "r2_full_median_recent": "median explanatory power, recent window",
        "r2_full_median_year": "median explanatory power, one year",
        "abs_residual_median_recent": "median |residual|, recent window",
        "abs_residual_median_year": "median |residual|, one year",
        "z_exceed_days_recent": "days with |z| >= 2 in recent window",
        "context_window_recent": "recent window length (trading days)",
        "context_window_year": "one-year window length (trading days)",
        "history_median_residual": "median unexplained move on flagged days",
        "robustness_d_ridge": "OLS vs Ridge divergence (typical residuals)",
        "robustness_d_lasso": "OLS vs post-Lasso divergence (typical residuals)",
        "robustness_abstain_run": "Lasso abstain run (trading days)",
    }
    rendered = fact.rendered()
    # Robustness state row (§12.6): neutral statements. Numeric rows go through the
    # rendered whitelist; the label row is assembled here
    rb = getattr(fact, "robustness", None) or {}
    if rb.get("available"):
        parts = []
        if rb.get("agree"):
            parts.append("OLS, Ridge and post-Lasso read this day the same way")
        else:
            if "ridge_diverge" in (rb.get("states") or []):
                parts.append("Ridge reads this day differently from OLS")
            if "lasso_reselect" in (rb.get("states") or []):
                parts.append("post-Lasso selects a different factor set")
            if "lasso_abstain" in (rb.get("states") or []):
                parts.append("Lasso selected no factors this day")
        rows.append(f"{'estimator agreement':<38}| " + "; ".join(parts))
    for key, label in labels.items():
        if key in rendered:
            rows.append(f"{label:<38}| {rendered[key]}")
    for key, value in rendered.items():
        if key.startswith("contribution."):
            rows.append(f"{'contribution: ' + key.split('.', 1)[1]:<38}| {value}")
    # Make the factor list explicit. No commentary added; it is just lifted out of the
    # contribution rows so Step 1's coverage check has something to check against
    # (SPEC §4.3)
    if fact.contributions_bp:
        rows.append(f"{'drivers this model can carry':<38}| "
                    + ", ".join(fact.contributions_bp))
    return "\n".join(rows)


def source_block(sources: list[dict]) -> str:
    """Same-day reports and after-the-fact recaps are listed separately, so the model
    can tell which is which.

    Lesson from the first live run: the candidate set was entirely recaps published
    three days later, and on that input judging accounts_for was close to the only
    reasonable answer. The distinction is itself material for judgement.

    **URLs are not shown.** Citation goes through short ids and the prose names the
    outlet; showing the model 300 characters of base64 only tempts it to copy them
    (SPEC §5.1, principle 2).
    """
    if not sources:
        return "(no sources retrieved)"

    def render(rows, header):
        out = [header]
        for s in rows:
            outlet = s.get("source") or "unknown"
            domain = s.get("publisher_domain") or "domain unknown"
            out.append(f"  [{s.get('id', '?')}] {s.get('title') or '(no title)'}")
            out.append(f"       outlet:    {outlet} ({domain})")
            out.append(f"       published: {s.get('published') or 'unknown'}")
            out.append(f"       summary:   {s.get('summary', '')}")
        return "\n".join(out)

    same = [s for s in sources if s.get("phase") != "after"]
    after = [s for s in sources if s.get("phase") == "after"]
    blocks = []
    if same:
        blocks.append(render(
            same, "### Published on the day itself or the trading day before"))
    if after:
        blocks.append(render(after, (
            "### Published after the day (retrospective coverage)\n"
            "Retrospective pieces argue for the significance of an event by "
            "construction. Weigh them accordingly.")))
    if not same:
        blocks.insert(0, "NOTE: nothing was retrieved from the day itself.")
    return "\n\n".join(blocks)


def previous_block(fact) -> str:
    """Neutral facts about the most recent trigger. Gives only the date, the gap, the
    event kind, the assessment and a one-sentence gist, and **no conclusion whatsoever
    about whether it is the same event** -- that is for the model to decide
    (SPEC_phase3 §4.4)."""
    prev = getattr(fact, "previous", None) or {}
    if not prev.get("date"):
        return ("### Previous flagged day\n\n"
                "(this pair has no earlier flagged day on record)")
    ago = prev.get("trading_days_ago")
    lines = [
        "### Previous flagged day",
        "",
        f"  date:        {prev['date']}",
        f"  how long ago: {ago if ago is not None else 'unknown'} trading days",
        f"  event kind:  {prev.get('event_kind') or 'unknown'}",
        f"  assessment:  {prev.get('assessment') or 'unknown'}",
        f"  reported then: {prev.get('what_happened_gist') or 'unknown'}",
    ]
    return "\n".join(lines)


def build_user_message(fact, sources: list[dict]) -> str:
    return (
        f"## Fact table (these strings are the only numbers you may write)\n\n"
        f"```\n{fact_table(fact)}\n```\n\n"
        f"## Retrieved sources (cite by id, name the outlet in prose)\n\n"
        f"{source_block(sources)}\n\n"
        f"## Context from this pair's own history\n\n"
        f"{previous_block(fact)}\n\n"
        f"Work through the coverage check, the continuity check and the scale "
        f"judgement first, then write the three paragraphs in both languages."
    )


class LLMClient(Protocol):
    """Minimal interface for structured output. Tests inject a fake; live runs inject
    the Gemini client."""

    def complete(self, system: str, user: str, schema: dict) -> dict:
        ...


def derive_insufficient(evidence: dict) -> bool:
    return (evidence or {}).get("assessment") in INSUFFICIENT


def compose(fact, sources: list[dict], client: LLMClient) -> tuple[dict, dict]:
    """Returns (raw output, narrative for verification).

    The raw output goes into the artifact verbatim: when verification discards a
    record, this is the only thing left to trace back through (D10).
    """
    raw = client.complete(
        SYSTEM_PROMPT, build_user_message(fact, sources), OUTPUT_SCHEMA)
    if isinstance(raw, str):
        raw = json.loads(raw)

    evidence = raw.get("evidence") or {}
    by_id = {s.get("id"): s for s in sources if s.get("id")}
    en = raw.get("en") or {}
    cited = list(en.get("sources_used") or [])
    narrative = {
        "en": en,
        "zh": raw.get("zh") or {},
        "sources_used": cited,
        # The id-to-URL mapping is done in code; the model copies not one character
        # (SPEC §5.1, principle 2)
        "cited_urls": [by_id[i]["url"] for i in cited if i in by_id],
        # Derived from the assessment, rather than having the model fill in a second
        # boolean that could contradict it
        "insufficient_evidence": derive_insufficient(evidence),
    }
    return raw, narrative
