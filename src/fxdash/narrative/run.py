"""Narrative layer entry point (SPEC_phase3 §2.2).

    python -m fxdash.narrative.run --date 2026-07-31
    python -m fxdash.narrative.run --dry-run          # offline, just show what it would do
    python -m fxdash.narrative.run --rewrite          # explicit recompute, supersedes audit kept

**Reads only outputs/contract/, writes only outputs/narrative/.** Not one byte of the
attribution pipeline's status.json is touched: the narrative layer going down must not
make attribution look broken.

Failure and success take the same path. Retrieval failure, refusal, a verification
discard -- all still write a complete record (sources, raw model output, which check
failed, why), with published simply false. **Failure samples are the only evidence base
for future prompt tuning; throw them away and all that is left is impressions.**
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..config import CONTRACT_DIR, DEFAULT_WINDOW, display_path
from . import compose as C
from . import retrieve as R
from . import store as S
from . import trigger as T
from .verify import Finding, verify

log = logging.getLogger(__name__)

DEFAULT_MAX_COST_USD = 0.50


def _calendar(contract_dir, window, model) -> list[str]:
    return T.trading_days(contract_dir=contract_dir, window=window, model=model)


def _failed_record(fact, sources, error: str, raw=None):
    """Retrieval failure, refusal and parse failure all come through here. Every field
    is present."""
    return S.pair_record(
        fact=fact, sources=sources, raw_output=raw, narrative=None,
        evidence={}, findings=[Finding("pipeline", False, error)], error=error,
    )


def process_pair(fact, client, calendar, fetcher=None) -> dict:
    """The full flow for one pair. Any step blowing up returns a complete failure
    record rather than raising."""
    retrieval = R.retrieve(fact, calendar, fetcher=fetcher)
    sources = retrieval["sources"]
    if retrieval["errors"] and not sources:
        record = _failed_record(fact, [], f"retrieval failed: {retrieval['errors'][0]}")
        record["retrieval"] = retrieval
        return record

    try:
        raw, narrative = C.compose(fact, sources, client)
    except Exception as exc:
        log.warning("%s generation failed: %s", fact.pair, exc)
        record = _failed_record(fact, sources, f"generation failed: {exc}")
        record["retrieval"] = retrieval
        return record

    findings = verify(narrative, [fact], sources, fact.date, calendar)
    record = S.pair_record(
        fact=fact, sources=sources, raw_output=raw, narrative=narrative,
        evidence=raw.get("evidence") or {}, findings=findings,
    )
    record["retrieval"] = retrieval
    return record


def run(
    date: str | None = None,
    *,
    contract_dir: Path | None = None,
    narrative_dir: Path | None = None,
    window: int = DEFAULT_WINDOW,
    model: str = T.DEFAULT_MODEL,
    client=None,
    rewrite: bool = False,
    dry_run: bool = False,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
) -> dict:
    contract_dir = contract_dir or CONTRACT_DIR
    day, facts = T.load_facts(date=date, contract_dir=contract_dir,
                              window=window, model=model)
    report = T.trigger_report(facts)
    selected = T.select_triggered(facts)

    if dry_run:
        return {
            "date": day, "dry_run": True, "trigger": report,
            "queries": {f.pair: R.build_query(f, _calendar(contract_dir, window, model))
                        for f in selected},
            "fact_tables": {f.pair: C.fact_table(f) for f in selected},
        }

    calendar = _calendar(contract_dir, window, model)

    # On a non-trading night the contract's latest day is still the previous trading
    # day, which was processed long ago.
    # **Must return before going online**: write_day's refusal happens after the whole
    # pair loop, and by then the LLM has been run for nothing, only to end in an
    # exception with the heartbeat cut off halfway.
    # This also makes a repeat run on the same night a free no-op, satisfying the
    # idempotence requirement in CLAUDE.md rule 13.
    if not rewrite and S.read_day(day, narrative_dir) is not None:
        note = f"{day} already has an artifact, contract has not advanced, no new work"
        log.info(note)
        S.write_status(
            narrative_dir,
            last_run=S.now_stamp(),
            last_published=S.last_published(narrative_dir),
            notes=[note],
        )
        return {
            "date": day, "covered": True, "trigger": report,
            "published": [], "discarded": [], "usage": {},
            "path": str(S.artifact_path(day, narrative_dir)),
        }

    if client is None:
        from .client import GeminiClient
        client = GeminiClient()

    records, halted = [], None
    for fact in selected:
        # The cost gate sits before each pair: better to write one piece fewer than to
        # run away with the budget
        spent = (getattr(client, "totals", {}) or {}).get("token_cost_usd") or 0.0
        if spent >= max_cost_usd:
            halted = f"cost cap of {max_cost_usd} USD reached, remaining pairs not processed"
            log.warning(halted)
            break
        # Neutral facts about the most recent trigger. The material is already on disk;
        # no need to build it again
        fact.previous = S.previous_trigger(
            fact.pair, day, root=narrative_dir, calendar=calendar)
        records.append(process_pair(fact, client, calendar))

    usage = dict(getattr(client, "totals", {}) or {})
    if halted:
        usage["halted"] = halted

    payload = S.build_payload(
        day, window=window, model=model,
        llm_model=getattr(client, "model", C.LLM_MODEL),
        prompt_version=C.PROMPT_VERSION,
        trigger=report, pairs=records, usage=usage,
    )
    path = S.write_day(payload, root=narrative_dir, rewrite=rewrite)

    published = [r["pair"] for r in records if r["published"]]

    # Alarms are for things that actually went wrong, not for "today was quiet". Zero
    # triggers is this layer's normal state.
    warn = []
    if halted:
        warn.append(halted)
    broken = [r["pair"] for r in records if r.get("error")]
    if broken:
        warn.append(f"{len(broken)} pair(s) failed retrieval or generation: {', '.join(broken)}")
    notes = [] if selected else [f"{day} no trigger among the six pairs, nothing to generate"]
    S.write_status(
        narrative_dir,
        last_run=S.now_stamp(),
        last_published=S.last_published(narrative_dir),
        extra_reasons=warn or None,
        notes=notes or None,
    )
    return {
        "date": day, "path": str(path), "trigger": report,
        "published": published,
        "discarded": [r["pair"] for r in records if not r["published"]],
        "usage": usage,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FX narrative layer")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to the latest day")
    parser.add_argument("--rewrite", action="store_true",
                        help="explicitly recompute an existing day artifact, keeping a supersedes audit")
    parser.add_argument("--dry-run", action="store_true",
                        help="offline; print only the trigger verdict, search queries and fact tables")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run(date=args.date, rewrite=args.rewrite, dry_run=args.dry_run,
                 max_cost_usd=args.max_cost)

    if result.get("dry_run"):
        print(f"date       {result['date']}")
        print(f"trigger    {result['trigger']}")
        for pair, table in result["fact_tables"].items():
            print(f"\n--- {pair} fact table ---\n{table}")
            print(f"\n--- {pair} search query ---\n{result['queries'][pair]}")
        return 0

    if result.get("covered"):
        print(f"date       {result['date']}")
        print(f"covered    artifact already exists for this day; no network, no generation, no change")
        print(f"artifact   {display_path(result['path'])}")
        return 0

    print(f"date       {result['date']}")
    print(f"trigger    {result['trigger']['selected']}")
    print(f"published  {result['published']}")
    print(f"discarded  {result['discarded']}")
    u = result["usage"]
    print(f"usage      {u.get('calls')} calls, {u.get('total_tokens')} tokens, "
          f"{u.get('token_cost_usd')} USD")
    print(f"artifact   {display_path(result['path'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
