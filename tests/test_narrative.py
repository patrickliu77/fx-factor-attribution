"""Narrative layer (SPEC_phase3.md).

This module never goes online and never calls an LLM: retrieval and generation are
stubbed throughout.
"""

import json

import pandas as pd
import pytest

from fxdash.narrative import trigger as T
from fxdash.narrative import verify as V
from fxdash.narrative import store as S
from fxdash.narrative import compose as C
from fxdash.narrative import retrieve as R
from fxdash.narrative import run as RUN

PAIRS = ["USDAUD", "USDCAD", "USDEUR", "USDJPY", "USDMXN", "USDNOK"]
FACTORS = ["DOLLAR_LOO", "CARRY_LOO", "dWTI"]
DATE = "2026-07-31"
PREV = "2026-07-30"


def _row(date, pair, *, residual_bp, z, y_bp=None, window=126, model="ols"):
    """Residual and return given in bp, converted back to log-return units on write."""
    y_bp = residual_bp if y_bp is None else y_bp
    contrib = {"DOLLAR_LOO": 3e-4, "CARRY_LOO": -1e-4, "dWTI": 2e-4}
    return {
        "date": pd.Timestamp(date),
        "pair": pair,
        "window": window,
        "model": model,
        "betas": json.dumps({f: 0.1 for f in FACTORS}),
        "contributions": json.dumps(contrib),
        "r2_full": 0.327,
        "r2_exog": 0.104,
        "selected_factors": json.dumps([]),
        "residual": residual_bp / 1e4,
        "residual_z": z,
        "stale_flags": json.dumps([]),
        "systematic": 2e-4,
        "exogenous": 2e-4,
        "y": y_bp / 1e4,
        "lambda": None,
        "provisional": False,
        "schema_version": "1.1.0",
    }


def _write(root, rows):
    frame = pd.DataFrame(rows)
    for year, block in frame.groupby(frame["date"].dt.year):
        part = root / f"year={year}"
        part.mkdir(parents=True, exist_ok=True)
        block.to_parquet(part / "part.parquet", index=False)
    return root


@pytest.fixture
def contract(tmp_path):
    """Six pairs on one day, residual and z set one by one to sit exactly on the
    criterion boundary."""
    rows = [
        # Exactly on the line: both conditions just meet the bar, must trigger
        _row(DATE, "USDJPY", residual_bp=50.0, z=-2.0),
        # z clears but the magnitude is 0.1 bp short: the AND criterion must block it
        _row(DATE, "USDCAD", residual_bp=49.9, z=-3.5),
        # Large magnitude but z just short: blocked as well
        _row(DATE, "USDEUR", residual_bp=200.0, z=1.99),
        # Three unambiguous triggers, for testing ordering and the cap
        _row(DATE, "USDMXN", residual_bp=154.5, z=5.18),
        _row(DATE, "USDNOK", residual_bp=123.7, z=4.29),
        _row(DATE, "USDAUD", residual_bp=-112.8, z=-4.24),
        # The previous day, for testing "no date given takes the latest day"
        _row(PREV, "USDJPY", residual_bp=10.0, z=0.3),
    ]
    return _write(tmp_path / "contract", rows)


# -------------------------------------------------------- criterion boundaries

def test_both_conditions_must_hold(contract):
    date, facts = T.load_facts(contract_dir=contract)
    assert date == DATE
    by_pair = {f.pair: f for f in facts}

    # Both conditions exactly on the line (>= not >), must trigger
    assert by_pair["USDJPY"].abs_z == pytest.approx(2.0)
    assert by_pair["USDJPY"].abs_residual_bp == pytest.approx(50.0)
    assert by_pair["USDJPY"].triggers() is True

    # z clears but the magnitude is 0.1 bp short. This is exactly why the 50 bp floor
    # exists: the pair barely moved that day, and does not warrant a solemn write-up
    assert by_pair["USDCAD"].triggers() is False
    # Magnitude clears but z does not
    assert by_pair["USDEUR"].triggers() is False


def test_selection_is_ranked_by_abs_z_and_capped(contract):
    _, facts = T.load_facts(contract_dir=contract)
    chosen = T.select_triggered(facts)
    assert [f.pair for f in chosen] == ["USDMXN", "USDNOK", "USDAUD"]
    assert len(chosen) == T.MAX_PER_DAY


def test_report_keeps_the_full_triggered_list_even_when_capped(contract):
    """Several pairs firing on the same day is itself information; the cap applies to
    generation only and must not erase the fact."""
    _, facts = T.load_facts(contract_dir=contract)
    report = T.trigger_report(facts, max_per_day=2)
    assert report["triggered"] == ["USDMXN", "USDNOK", "USDAUD", "USDJPY"]
    assert report["selected"] == ["USDMXN", "USDNOK"]
    assert report["capped"] == 2
    assert report["criterion"]["abs_residual_bp_min"] == 50.0


def test_latest_day_is_used_when_no_date_given(contract):
    date, _ = T.load_facts(contract_dir=contract)
    assert date == DATE
    date, facts = T.load_facts(date=PREV, contract_dir=contract)
    assert date == PREV
    assert len(facts) == 1


def test_unknown_selection_raises(contract):
    with pytest.raises(ValueError):
        T.load_facts(date="2026-01-02", contract_dir=contract)
    with pytest.raises(ValueError):
        T.load_facts(contract_dir=contract, model="ridge")
    with pytest.raises(ValueError):
        T.load_facts(contract_dir=contract, window=999)


# ------------------------------------------------------------------- fact set

def test_rendered_numbers_are_the_single_source_of_truth(contract):
    """Numbers are formatted exactly once here. What the prompt receives and what
    verification recognizes must be the same strings, otherwise you eventually get
    "-192.7 bp in the prompt, -192.70 bp in the verification table"."""
    _, facts = T.load_facts(contract_dir=contract)
    mxn = next(f for f in facts if f.pair == "USDMXN")
    rendered = mxn.rendered()

    assert rendered["residual"] == "+154.5 bp"
    assert rendered["residual_z"] == "+5.18"
    assert rendered["r2_full"] == "0.327"
    # 154.5 bp -> 1.545%; in binary this float is just under the half, so it formats
    # as +1.54%. As long as the prompt and verification share this one string, either
    # side is self-consistent
    assert rendered["y_pct"] == "+1.54%"
    assert rendered["contribution.DOLLAR_LOO"] == "+3.0 bp"

    allowed = mxn.allowed_numbers()
    assert "+154.5" in allowed and "+5.18" in allowed and "0.327" in allowed
    # No units kept in the whitelist; verification compares bare number strings
    assert not any(" bp" in tok or "%" in tok for tok in allowed)


def test_top_factor_is_the_largest_absolute_contribution(contract):
    _, facts = T.load_facts(contract_dir=contract)
    assert facts[0].top_factor == "DOLLAR_LOO"


def test_fact_survives_a_json_round_trip(contract):
    _, facts = T.load_facts(contract_dir=contract)
    blob = json.dumps([f.to_dict() for f in facts])
    back = json.loads(blob)
    assert len(back) == 6
    assert back[0]["rendered"]["residual_z"].startswith(("+", "-"))


# ================================================================== the checks

def _fact():
    """USD/JPY on 2026-07-31, the numbers from the first live case."""
    return T.Fact(
        pair="USDJPY", date=DATE, window=126,
        y_bp=-192.7, residual_bp=-161.3, residual_z=-4.62,
        systematic_bp=-26.8, exogenous_bp=-4.7,
        r2_full=0.327, r2_exog=0.104, top_factor="DOLLAR_LOO",
        contributions_bp={"DOLLAR_LOO": -22.1, "CARRY_LOO": -4.7, "d10Y_DIFF": -3.1},
        context=T.Context(
            r2_full_median_short=0.341, r2_full_median_long=0.618,
            abs_residual_median_short_bp=48.2, abs_residual_median_long_bp=20.0,
            z_exceed_days_short=4,
        ),
    )


def _narrative(**over):
    """Baseline that passes all six checks. Change one thing to pin one check."""
    base = {
        "en": {
            "what_happened": (
                "USD/JPY moved -192.7 bp on 2026-07-31. Reuters reported the same day."
            ),
            "why_unexplained": (
                "The model assigns -26.8 bp to systematic factors and -4.7 bp to "
                "exogenous factors, leaving -161.3 bp unexplained, with a residual z "
                "of -4.62. Rolling explanatory power was 0.327 that day against a "
                "one-year median of 0.618."
            ),
            "what_to_watch": (
                "The next Bank of Japan meeting and the following month of rate "
                "differential data will test this reading."
            ),
            "sources_used": ["S1"],
        },
        "zh": {
            "what_happened": "当日 USD/JPY 变动 -192.7 bp。路透社同日有报道。",
            "why_unexplained": (
                "模型把 -26.8 bp 归入系统性、-4.7 bp 归入外生，未获解释的部分为 "
                "-161.3 bp，残差 z 为 -4.62。当日解释力 0.327，近 21 个交易日的"
                "中位数为 0.341，全年中位数为 0.618。"
            ),
            "what_to_watch": "日本央行下次会议与后续利差数据可检验这一解读。",
            "sources_used": ["S1"],
        },
        "sources_used": ["S1"],
        "insufficient_evidence": False,
    }
    for key, value in over.items():
        base[key] = value
    return base


def _run(narrative, sources=None, date=DATE):
    return V.verify(narrative, [_fact()], sources or SOURCES, date, CALENDAR)


def test_a_clean_narrative_passes_all_six():
    findings = _run(_narrative())
    assert V.passed(findings), V.failures(findings)
    assert [f.check for f in findings] == [
        "sources_in_set", "source_date_window", "literal_numbers",
        "no_causal_claims", "no_directional_forecast", "bilingual_sources_match",
    ]


def test_invented_source_id_fails():
    """A source id invented out of thin air always fails."""
    bad = _narrative()
    bad["en"]["sources_used"] = ["S1", "S99"]
    bad["zh"]["sources_used"] = ["S1", "S99"]
    findings = _run(bad)
    assert not V.passed(findings)
    # Only check 1 should light up: not being in the set is check 1's business, not
    # the date check's
    assert [f["check"] for f in V.failures(findings)] == ["sources_in_set"]


def test_a_bare_url_in_the_prose_fails():
    """The prose names the outlet, not the URL. Making the model copy 300 characters
    of base64 would be our design error."""
    bad = _narrative()
    bad["en"]["what_happened"] = bad["en"]["what_happened"] + " See " + GOOD_URL
    findings = _run(bad)
    assert not V.passed(findings)
    assert "sources_in_set" in {f["check"] for f in V.failures(findings)}


def test_bilingual_source_drift_fails():
    """The Chinese and English are two language versions of one piece, not two pieces.
    Citation drift is exposed structurally."""
    bad = _narrative()
    bad["zh"]["sources_used"] = []
    findings = _run(bad)
    assert not V.passed(findings)
    assert "bilingual_sources_match" in {f["check"] for f in V.failures(findings)}


def test_claiming_evidence_while_citing_nothing_fails():
    findings = _run(_narrative(sources_used=[], **{
        "zh": {"what_happened": "当日 USD/JPY 变动 -192.7 bp。",
               "why_unexplained": "未获解释的部分为 -161.3 bp。",
               "what_to_watch": "日本央行下次会议可检验这一解读。",
               "sources_used": []},
        "en": {"what_happened": "USD/JPY moved -192.7 bp.",
               "why_unexplained": "-161.3 bp is unexplained.",
               "what_to_watch": "The next Bank of Japan meeting will test this.",
               "sources_used": []},
    }))
    assert not V.passed(findings)
    assert "sources_in_set" in {f["check"] for f in V.failures(findings)}


def test_time_shifted_source_fails():
    """Retrieval readily returns older pieces on topic but misplaced in time."""
    stale = [{**SOURCES[0], "published": "2026-07-20"}]
    findings = _run(_narrative(), sources=stale)
    assert not V.passed(findings)
    assert "source_date_window" in {f["check"] for f in V.failures(findings)}


def test_source_one_trading_day_later_is_accepted():
    """Next-day publication is normal; one trading day either side counts. 8/1 and 8/2
    are the weekend, so the next trading day is 8/3."""
    next_day = [{**SOURCES[0], "published": "2026-08-03"}]
    findings = _run(_narrative(), sources=next_day)
    assert V.passed(findings), V.failures(findings)


def test_approximated_number_fails():
    """Writing -161.3 bp as "about -160 bp" is not wrong semantically, but allow
    approximation once and this check becomes ornamental."""
    fuzzy = _narrative()
    fuzzy["zh"]["why_unexplained"] = "未获解释的部分约为 -160 bp。"
    findings = _run(fuzzy)
    assert not V.passed(findings)
    assert "literal_numbers" in {f["check"] for f in V.failures(findings)}


def test_a_figure_quoted_from_a_source_is_allowed_in_the_first_paragraph():
    """Check 3 judges "can it be traced to text we hold", not "is it in the fact
    table". The cost of the old definition was that verification systematically forced
    vague wording (massive instead of $59 billion)."""
    src = dict(SOURCES[0],
               title="Japan may have sold up to $59 billion in yen-buying intervention")
    ok = _narrative()
    ok["en"]["what_happened"] = "Reuters reported a sale of up to 59 billion."
    ok["zh"]["what_happened"] = "路透社报道称抛售规模最高达 59 十亿。"
    findings = V.verify(ok, [_fact()], [src], DATE, CALENDAR)
    assert V.passed(findings), V.failures(findings)

    # Must be recorded together with the source id it came from, for traceback
    number_check = next(f for f in findings if f.check == "literal_numbers")
    assert number_check.data["from_sources"] == {"59": ["S1"]}


def test_a_source_figure_is_rejected_in_the_second_paragraph():
    """The second paragraph is our own numbers; no external sources mixed in."""
    src = dict(SOURCES[0],
               title="Japan may have sold up to $59 billion in yen-buying intervention")
    bad = _narrative()
    bad["en"]["why_unexplained"] = (
        "The unexplained residual is -161.3 bp, against a reported 59 billion sale."
    )
    findings = V.verify(bad, [_fact()], [src], DATE, CALENDAR)
    assert not V.passed(findings)
    detail = next(f["detail"] for f in V.failures(findings)
                  if f["check"] == "literal_numbers")
    assert "allowed in the first paragraph only" in detail


def test_a_figure_in_no_text_we_hold_is_still_rejected():
    """It widens coverage, not tolerance: traceable to none of the text we hold still
    fails."""
    bad = _narrative()
    bad["en"]["what_happened"] = "Reuters reported a sale of up to 77 billion."
    findings = V.verify(bad, [_fact()], SOURCES, DATE, CALENDAR)
    assert not V.passed(findings)
    assert "literal_numbers" in {f["check"] for f in V.failures(findings)}


def test_factor_names_and_dates_do_not_trip_the_number_check():
    """The 10 in d10Y_DIFF and the 2026 in a date are not numeric assertions and must
    not be killed by mistake."""
    ok = _narrative()
    ok["en"]["why_unexplained"] += " The d10Y_DIFF slot was flat on July 31, 2026."
    findings = _run(ok)
    assert V.passed(findings), V.failures(findings)


def test_causal_wording_fails():
    """Between news and residual there is only co-occurrence, no causal evidence."""
    for term, lang, para in (("由于", "zh", "what_happened"),
                             ("导致", "zh", "why_unexplained")):
        bad = _narrative()
        bad[lang][para] = f"{term}日本央行会议，当日出现大幅变动。"
        findings = _run(bad)
        assert not V.passed(findings), term
        assert "no_causal_claims" in {f["check"] for f in V.failures(findings)}

    bad = _narrative()
    bad["en"]["what_happened"] = f"The move was caused by the policy meeting. {GOOD_URL}"
    findings = _run(bad)
    assert "no_causal_claims" in {f["check"] for f in V.failures(findings)}


def test_our_own_vocabulary_is_not_mistaken_for_causal_wording():
    """归因 and 触发 are this project's own terms and must not be caught by the causal
    check."""
    ok = _narrative()
    ok["zh"]["why_unexplained"] = (
        "模型归因把 -26.8 bp 放在系统性，未获解释的部分为 -161.3 bp，"
        "残差 z 为 -4.62，已触发本层的判据。"
    )
    findings = _run(ok)
    assert V.passed(findings), V.failures(findings)


def test_directional_forecast_in_the_third_paragraph_fails():
    """CLAUDE.md rule 6: this project does explanation only. The third paragraph may
    only say what would test the explanation."""
    bad = _narrative()
    bad["zh"]["what_to_watch"] = "预计后市美元对日元继续走低。"
    findings = _run(bad)
    assert not V.passed(findings)
    assert "no_directional_forecast" in {f["check"] for f in V.failures(findings)}

    bad = _narrative()
    bad["en"]["what_to_watch"] = "The yen will weaken further into the next meeting."
    findings = _run(bad)
    assert "no_directional_forecast" in {f["check"] for f in V.failures(findings)}


def test_forecast_words_are_only_banned_in_the_third_paragraph():
    """Mentioning market expectations while stating what was reported that day is a
    statement of fact in the first paragraph, not a forecast."""
    ok = _narrative()
    ok["en"]["what_happened"] = (
        "Reuters reporting that day described what the market had expected."
    )
    findings = _run(ok)
    assert V.passed(findings), V.failures(findings)


def test_context_window_lengths_are_whitelisted():
    """When the model legitimately says "the last 21 trading days", 21 must not be
    killed by the number check."""
    fact = _fact()
    assert "21" in fact.allowed_numbers()
    assert "252" in fact.allowed_numbers()
    assert "126" in fact.allowed_numbers()
    assert "0.618" in fact.allowed_numbers()


# ====================================================== artifacts & heartbeat

def _record(assessment="does_not_account_for", findings=None):
    return S.pair_record(
        fact=_fact(),
        sources=SOURCES,
        raw_output={"raw": "model said this"},
        narrative=_narrative(),
        evidence={"assessment": assessment, "reasoning": "..."},
        findings=findings if findings is not None else _run(_narrative()),
    )


def _payload(date=DATE, **over):
    kwargs = dict(
        window=126, model="ols", llm_model="claude-opus-5", prompt_version="p3-1",
        trigger={"selected": ["USDJPY"]}, pairs=[_record()],
    )
    kwargs.update(over)
    return S.build_payload(date, **kwargs)


def test_does_not_account_for_is_published_not_suppressed():
    """SPEC §10.2 defines "reporting exists but does not account for a move of this
    magnitude" as **correct output**. Suppressing it would hide exactly the honest
    conclusion this layer exists to produce."""
    assert S.decide_published(True, "does_not_account_for") is True
    assert S.decide_published(True, "partially_accounts_for") is True
    assert S.decide_published(True, "accounts_for") is True
    # Only genuinely having no relevant reporting leaves nothing to publish
    assert S.decide_published(True, "no_relevant_reporting") is False
    # Verification failed, never published
    for a in S.ASSESSMENTS:
        assert S.decide_published(False, a) is False


def test_a_discarded_narrative_still_keeps_the_full_record(tmp_path):
    """Failure samples are the only evidence base for future prompt tuning; throw them
    away and all that is left is impressions."""
    bad = _narrative()
    bad["zh"]["why_unexplained"] = "未获解释的部分约为 -160 bp。"
    record = S.pair_record(
        fact=_fact(), sources=SOURCES,
        raw_output={"raw": "the model's exact words"},
        narrative=bad,
        evidence={"assessment": "accounts_for"},
        findings=_run(bad),
    )
    assert record["published"] is False
    assert record["verification"]["passed"] is False
    assert record["verification"]["failures"][0]["check"] == "literal_numbers"
    # The full evidence set is present
    assert record["raw_output"] == {"raw": "the model's exact words"}
    assert record["sources"] == SOURCES
    assert record["narrative"] == bad
    assert record["facts"]["residual_bp"] == -161.3


def test_artifact_is_frozen_once_written(tmp_path):
    path = S.write_day(_payload(), root=tmp_path)
    assert path.name == f"date={DATE}.json"
    with pytest.raises(S.ArtifactExists):
        S.write_day(_payload(), root=tmp_path)


def test_explicit_rewrite_records_what_it_superseded(tmp_path):
    S.write_day(_payload(), root=tmp_path)
    first = S.read_day(DATE, root=tmp_path)
    again = _payload(pairs=[_record(assessment="accounts_for")])
    S.write_day(again, root=tmp_path, rewrite=True)
    second = S.read_day(DATE, root=tmp_path)

    assert second["supersedes"][0]["content_hash"] == first["content_hash"]
    assert second["content_hash"] != first["content_hash"]


def test_artifact_reads_back_identical(tmp_path):
    payload = _payload()
    S.write_day(payload, root=tmp_path)
    back = S.read_day(DATE, root=tmp_path)
    assert back["content_hash"] == payload["content_hash"]
    assert S.content_hash(back) == back["content_hash"]


def test_content_hash_excludes_itself():
    payload = _payload()
    tampered = dict(payload, content_hash="deadbeef")
    assert S.content_hash(tampered) == S.content_hash(payload)


# ------------------------------------------------------------- heartbeat

@pytest.mark.parametrize("age_hours,expected", [
    (None, "red"), (1.0, "green"), (25.9, "green"),
    (26.1, "yellow"), (71.9, "yellow"), (72.1, "red"),
])
def test_heartbeat_thresholds_match_the_main_pipeline(age_hours, expected):
    state, _ = S.heartbeat_state(age_hours)
    assert state == expected


def test_status_reports_age_and_never_touches_the_pipeline_status(tmp_path):
    """A failure in the narrative layer must not change the attribution pipeline's
    green/yellow/red."""
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)  # the isolated_outputs fixture may already have made it
    pipeline = outputs / "status.json"
    pipeline.write_text('{"state": "green"}', encoding="utf-8")
    before = pipeline.read_bytes()

    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    stale = (now - timedelta(hours=30)).isoformat()
    status = S.write_status(outputs / "narrative", last_run=stale, now=now)

    assert status["state"] == "yellow"
    assert status["age_hours"] == pytest.approx(30.0)
    assert status["warn_hours"] == 26
    # Not one byte of the pipeline's status.json changed
    assert pipeline.read_bytes() == before
    assert S.read_status(outputs / "narrative")["state"] == "yellow"


def test_missing_status_reads_as_red_not_as_healthy(tmp_path):
    """An unreadable heartbeat must report red. Silently treating it as normal is
    exactly the failure mode this heartbeat exists to prevent."""
    assert S.read_status(tmp_path / "narrative")["state"] == "red"


def test_last_published_ignores_days_where_nothing_was_published(tmp_path):
    S.write_day(_payload(date="2026-07-30",
                         pairs=[_record(assessment="no_relevant_reporting")]),
                root=tmp_path)
    assert S.last_published(tmp_path) is None
    S.write_day(_payload(date=DATE), root=tmp_path)
    assert S.last_published(tmp_path) is not None


def test_a_quiet_stretch_does_not_turn_the_heartbeat_yellow(tmp_path):
    """This layer's most expensive lesson, pinned by a test.

    Residual anomalies measure out at once every 4.5 to 5 days, so quiet is the norm.
    An earlier heartbeat judged colour by "the most recent note", so every four- or
    five-day stretch without a trigger turned yellow and two days later red, while the
    system was fine throughout. An always-on alarm is a wrong criterion; people learn
    to ignore it, and then a real failure goes unseen too.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    status = S.write_status(
        tmp_path / "narrative",
        last_run=(now - timedelta(hours=2)).isoformat(),        # ran two hours ago
        last_published=(now - timedelta(days=9)).isoformat(),   # nine days with no trigger
        now=now,
        notes=["2026-08-01 no trigger among the six pairs, nothing to generate"],
    )
    assert status["state"] == "green"
    assert status["age_hours"] == pytest.approx(2.0)
    assert status["published_age_hours"] == pytest.approx(216.0)
    # Notes are shown as usual, but must not push green up to yellow
    assert any("no trigger among" in r for r in status["reasons"])


def test_a_dead_task_still_turns_the_heartbeat_red_however_fresh_the_content(tmp_path):
    """The converse of the previous test: avoiding false alarms must not turn the
    heartbeat permanently green."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    status = S.write_status(
        tmp_path / "narrative",
        last_run=(now - timedelta(hours=80)).isoformat(),
        last_published=(now - timedelta(hours=80)).isoformat(),
        now=now,
    )
    assert status["state"] == "red"


def test_a_failed_pair_is_a_warning_but_a_quiet_day_is_not(tmp_path):
    """Alarms are for things that actually went wrong. extra_reasons pushes the colour,
    notes do not."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()

    warned = S.write_status(tmp_path / "a", last_run=fresh, now=now,
                            extra_reasons=["1 pair(s) failed retrieval or generation: USDJPY"])
    assert warned["state"] == "yellow"

    quiet = S.write_status(tmp_path / "b", last_run=fresh, now=now,
                           notes=["no trigger among the six pairs, nothing to generate"])
    assert quiet["state"] == "green"


# ============================================================ assembly & calls

class FakeLLM:
    """Fake model. Tests never go online (SPEC_phase3 §9)."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return self.payload


def _model_output(assessment="does_not_account_for"):
    parts = _narrative()
    return {
        "evidence": {
            "event_kind": "central bank rate decision",
            "coverage_check": "no factor in the list carries policy operations",
            "assessment": assessment,
            "reasoning": "routine reporting only",
        },
        "en": parts["en"], "zh": parts["zh"],
    }


def test_fact_table_shows_only_the_allowed_strings():
    """A model that never sees the raw float is never tempted to round it."""
    table = C.fact_table(_fact())
    assert "-161.3 bp" in table and "-4.62" in table and "0.327" in table
    assert "104.2 bp" in table            # magnitude calibration, must be quotable
    # Factor list made explicit; Step 1's coverage check needs something to check against
    assert "drivers this model can carry" in table
    assert "d10Y_DIFF" in table
    assert "0.341" in table and "0.618" in table   # trend context
    assert "-161.29" not in table and "161.3000" not in table


def test_prompt_makes_not_explaining_a_first_class_outcome():
    """Three deliberate pieces of wording; drop any one and the model tilts towards
    "found an explanation"."""
    p = C.SYSTEM_PROMPT
    assert "not being asked to explain the move" in p
    assert "two complete findings here, not one" in p
    assert "complete finding" in p and "not a failure to produce one" in p
    assert "not possible to tell is a legitimate" in p
    assert "Do not spread the unexplained magnitude across the retrieved stories" in p
    # Judgement before writing
    assert p.index("first task is a judgement") < p.index("## How to write")
    assert C.OUTPUT_SCHEMA["required"][0] == "evidence"


def test_coverage_check_gives_an_action_not_an_answer():
    """It must not read "point it out if the event is outside the factor set": that
    teaches the answer, and next time an event genuinely inside the factor set comes
    along it will say the same thing anyway."""
    p = C.SYSTEM_PROMPT
    assert "Coverage check" in p
    assert "drivers this model can carry" in p
    # All three outcomes are spelled out, not just the "no factor" one
    assert "If no factor in the list can carry" in p
    assert "If a factor could carry it but that factor" in p
    assert "If a factor carries it and the contribution is large" in p
    assert "Do not assume the answer in advance" in p
    for field in ("event_kind", "coverage_check"):
        assert field in C.OUTPUT_SCHEMA["properties"]["evidence"]["required"]


def test_prose_must_name_outlets_not_urls():
    p = C.SYSTEM_PROMPT
    assert "name the outlet" in p
    assert "No URLs anywhere in the prose" in p


def test_prompt_and_verifier_ban_the_same_words():
    """The banned words listed in the prompt must match the verifier's lists, otherwise
    the model is asked to follow one set and judged against another."""
    for term in ("因为", "由于", "导致", "归因于"):
        assert term in C.SYSTEM_PROMPT and term in V.CAUSAL_ZH
    for term in ("预计", "预测", "有望", "看涨"):
        assert term in C.SYSTEM_PROMPT and term in V.FORECAST_ZH


def test_compose_derives_the_flag_from_the_assessment():
    """Do not have the model fill in a second boolean that could contradict the
    assessment."""
    for assessment, expected in (
        ("no_relevant_reporting", True),
        ("does_not_account_for", True),
        ("partially_accounts_for", False),
        ("accounts_for", False),
    ):
        raw, narrative = C.compose(_fact(), SOURCES, FakeLLM(_model_output(assessment)))
        assert narrative["insufficient_evidence"] is expected
        assert raw["evidence"]["assessment"] == assessment


def test_composed_output_passes_the_six_checks():
    raw, narrative = C.compose(_fact(), SOURCES, FakeLLM(_model_output()))
    findings = _run(narrative)
    assert V.passed(findings), V.failures(findings)


def test_assessment_values_agree_across_modules():
    assert C.ASSESSMENTS == S.ASSESSMENTS
    assert set(S.PUBLISHABLE_ASSESSMENTS) <= set(C.ASSESSMENTS)


# ================================================================== retrieval

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <item>
    <title>BOJ nods to chance of early rate hike as Tokyo props up yen - Reuters</title>
    <link>https://news.google.com/rss/articles/AAA</link>
    <pubDate>Fri, 31 Jul 2026 07:00:00 GMT</pubDate>
    <description>&lt;a href="x"&gt;Reuters&lt;/a&gt; reports the BOJ held rates.</description>
    <source url="https://reuters.com">Reuters</source>
  </item>
  <item>
    <title>Bank of Japan holds rates with hawkish guidance - ft.com</title>
    <link>https://news.google.com/rss/articles/BBB</link>
    <pubDate>Thu, 30 Jul 2026 07:00:00 GMT</pubDate>
    <description>FT coverage.</description>
    <source url="https://ft.com">ft.com</source>
  </item>
  <item>
    <title>Old yen story from June - Nikkei</title>
    <link>https://news.google.com/rss/articles/CCC</link>
    <pubDate>Mon, 15 Jun 2026 07:00:00 GMT</pubDate>
    <description>Out of window.</description>
    <source url="https://nikkei.com">Nikkei</source>
  </item>
</channel>
</rss>"""

CALENDAR = ["2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"]
GOOD_URL = "https://news.google.com/rss/articles/AAA"
SOURCES = [{
    "id": "S1",
    "url": GOOD_URL,
    "title": "BOJ nods to chance of early rate hike as Tokyo props up yen",
    "source": "Reuters",
    "published": "2026-07-31",
    "publisher_domain": "https://reuters.com",
    "phase": "same_day",
    "summary": "Reuters reports the BOJ held rates.",
    "url_kind": "google_news_redirect",
}]


def test_window_is_one_trading_day_each_side():
    assert R.window("2026-07-31", CALENDAR) == ("2026-07-30", "2026-08-03")


def test_query_carries_dates_and_no_causal_assumption():
    """Push a causal hypothesis in at the retrieval stage and the candidate set is
    already skewed; the checks all run at the writing stage."""
    q = R.build_query(_fact(), CALENDAR)
    assert "Bank of Japan" in q and "yen" in q
    assert "after:2026-07-29" in q and "before:2026-08-04" in q
    for banned in ("caused", "explain", "why", "because", "impact"):
        assert banned not in q.lower()


def test_retrieval_runs_two_passes_and_puts_same_day_first():
    """Lesson from the first live run: a single retrieval over the whole window came
    back with 11 of 12 items being recaps published three days later, and not one
    same-day report. On that input, judging accounts_for was close to the only
    reasonable answer."""
    seen = []

    def fetcher(query):
        seen.append(query)
        return RSS_XML

    out = R.retrieve(_fact(), CALENDAR, fetcher=fetcher)
    assert set(out["queries"]) == {"same_day", "after"}
    # The first pass is strictly the day itself and the day before
    assert "before:2026-08-01" in out["queries"]["same_day"]
    assert out["counts"]["same_day"] == 2
    assert [s["phase"] for s in out["sources"]][:2] == ["same_day", "same_day"]


def test_source_block_separates_same_day_from_retrospective():
    same = dict(SOURCES[0])
    later = dict(SOURCES[0], url="https://news.google.com/rss/articles/ZZZ",
                 published="2026-08-03", phase="after", title="Three days later")
    block = C.source_block([same, later])
    assert "Published on the day itself" in block
    assert "retrospective coverage" in block.lower()
    assert block.index("Published on the day itself") < block.index("Three days later")
    assert "reuters.com" in block


def test_prompt_bans_footnote_markers():
    """When a check conflicts with the prompt, change the prompt: adding [4] to the
    number whitelist would open a hole in check 3."""
    assert "No footnote or citation markers" in C.SYSTEM_PROMPT
    assert "[4]" in C.SYSTEM_PROMPT


def test_source_block_hides_urls_and_shows_ids():
    """Showing the model 300 characters of base64 only tempts it to copy them."""
    block = C.source_block([dict(SOURCES[0])])
    assert "[S1]" in block
    assert GOOD_URL not in block
    assert "reuters.com" in block


def test_parse_feed_extracts_source_date_and_strips_the_suffix():
    """<source> is a plain element with no namespace, carrying a url attribute that
    gives the publisher's real domain. The first live run looked for
    {http://news.google.com}source and got None for all 12."""
    rows = R.parse_feed(RSS_XML, keep=("2026-07-30", "2026-08-03"))
    assert [r["published"] for r in rows] == ["2026-07-31", "2026-07-30"]
    assert rows[0]["source"] == "Reuters"
    assert rows[0]["publisher_domain"] == "https://reuters.com"
    # The trailing " - Reuters" in the title is added by Google; the source gets its
    # own field
    assert rows[0]["title"].endswith("props up yen")
    assert rows[0]["url"] == GOOD_URL
    assert rows[0]["url_kind"] == "google_news_redirect"
    assert "<a href" not in rows[0]["summary"]


def test_parse_feed_drops_out_of_window_items():
    """The retrieval window is aligned with check 2's acceptance window, so we do not
    manufacture technical failures for ourselves."""
    rows = R.parse_feed(RSS_XML, keep=("2026-07-30", "2026-08-03"))
    assert all("CCC" not in r["url"] for r in rows)


def test_retrieve_records_errors_instead_of_raising():
    def boom(query):
        raise ConnectionResetError("remote closed")

    out = R.retrieve(_fact(), CALENDAR, fetcher=boom)
    assert out["sources"] == []
    assert out["errors"] and "ConnectionResetError" in out["errors"][0]
    assert out["provider"] == "google_news_rss"


def test_retrieve_keeps_everything_it_found():
    """Uncited sources go into the artifact too; they are the only evidence for judging
    whether the model cherry-picked."""
    out = R.retrieve(_fact(), CALENDAR, fetcher=lambda q: RSS_XML)
    assert len(out["sources"]) == 2      # two same_day items after dedup
    assert out["window"] == ["2026-07-30", "2026-08-03"]


# =============================================================== orchestration

class FakeClient:
    """Generation only; retrieval is injected through the fetcher."""

    model = "fake-model"

    def __init__(self, output=None, cost=0.0):
        self._output = output or _model_output()
        self.cost = cost

    def complete(self, system, user, schema):
        return self._output

    @property
    def totals(self):
        return {"token_cost_usd": self.cost, "calls": 1, "total_tokens": 100}


def _feed(_query):
    return RSS_XML


@pytest.fixture
def live(tmp_path, contract):
    return contract, tmp_path / "narrative"


def _run_pipeline(contract_dir, narrative_dir, client=None, fetcher=_feed, **kw):
    """Inject the fetcher into process_pair. Tests never go online."""
    import fxdash.narrative.run as run_mod
    original = run_mod.process_pair

    def patched(fact, cl, calendar, fetcher=None):
        return original(fact, cl, calendar, fetcher=chosen)

    chosen = fetcher
    run_mod.process_pair = patched
    try:
        return run_mod.run(date=DATE, contract_dir=contract_dir,
                           narrative_dir=narrative_dir,
                           client=client or FakeClient(), **kw)
    finally:
        run_mod.process_pair = original


def test_run_writes_an_artifact_and_its_own_status(live):
    contract_dir, narrative_dir = live
    out = _run_pipeline(contract_dir, narrative_dir)
    assert out["trigger"]["selected"] == ["USDMXN", "USDNOK", "USDAUD"]
    day = S.read_day(DATE, root=narrative_dir)
    assert day is not None and len(day["pairs"]) == 3
    assert day["pairs"][0]["retrieval"]["provider"] == "google_news_rss"
    assert S.read_status(narrative_dir)["state"] in ("green", "yellow", "red")


def test_run_never_touches_the_pipeline_status(live, tmp_path):
    """The narrative layer going down must not make attribution look broken."""
    contract_dir, narrative_dir = live
    pipeline = tmp_path / "status.json"
    pipeline.write_text('{"state": "green"}', encoding="utf-8")
    before = pipeline.read_bytes()

    def boom(_q):
        raise ConnectionResetError("remote closed")

    _run_pipeline(contract_dir, narrative_dir, fetcher=boom)
    assert pipeline.read_bytes() == before


def test_a_failed_retrieval_still_produces_a_complete_record(live):
    contract_dir, narrative_dir = live

    def boom(_q):
        raise ConnectionResetError("remote closed")

    _run_pipeline(contract_dir, narrative_dir, fetcher=boom)
    record = S.read_day(DATE, root=narrative_dir)["pairs"][0]
    assert record["published"] is False
    assert "retrieval failed" in record["error"]
    assert record["verification"]["failures"][0]["check"] == "pipeline"
    assert record["facts"]["pair"] == "USDMXN"      # facts stored as usual
    assert record["retrieval"]["errors"]            # failure reason stored as usual


def test_cost_gate_stops_before_the_next_pair(live):
    contract_dir, narrative_dir = live
    out = _run_pipeline(contract_dir, narrative_dir,
                        client=FakeClient(cost=9.99), max_cost_usd=0.50)
    assert out["published"] == [] and out["discarded"] == []
    assert "cost cap" in out["usage"]["halted"]


def test_dry_run_touches_nothing(live):
    import fxdash.narrative.run as run_mod
    contract_dir, narrative_dir = live
    out = run_mod.run(date=DATE, contract_dir=contract_dir,
                      narrative_dir=narrative_dir, dry_run=True)
    assert out["dry_run"] is True
    assert set(out["fact_tables"]) == {"USDMXN", "USDNOK", "USDAUD"}
    assert not narrative_dir.exists()


def test_artifact_is_frozen_against_a_second_run(live):
    """A second run neither overwrites the artifact nor crashes, and must turn back
    before going online.

    On a non-trading night the contract does not advance, the task still starts at
    20:15, and what it reads is still the previous trading day. The old behaviour ran
    the whole pair loop, burned an LLM pass for nothing, then ended by raising
    ArtifactExists in write_day with the heartbeat cut off halfway -- so every weekend
    turned yellow and every long weekend turned red.
    """
    contract_dir, narrative_dir = live
    first = _run_pipeline(contract_dir, narrative_dir)
    before = S.artifact_path(DATE, narrative_dir).read_bytes()

    class Exploding:
        """Blows up if the second run touches the model at all."""

        model = "must-not-be-called"
        totals = {"token_cost_usd": 0.0}

        def complete(self, system, user, schema):
            raise AssertionError("must not call the model on a day that already has an artifact")

    def no_network(_query):
        raise AssertionError("must not go online on a day that already has an artifact")

    second = _run_pipeline(contract_dir, narrative_dir,
                           client=Exploding(), fetcher=no_network)

    assert second["covered"] is True
    assert second["published"] == []
    assert S.artifact_path(DATE, narrative_dir).read_bytes() == before
    assert first["path"] == second["path"]
    # A no-op still refreshes the heartbeat: this run was normal and must not be
    # allowed to decay into an alarm
    status = S.read_status(narrative_dir)
    assert status["state"] == "green"
    assert any("no new work" in r for r in status["reasons"])


def test_write_day_still_refuses_to_overwrite_underneath(live):
    """The previous test changed run's behaviour; the freeze itself is not loosened --
    the store layer still refuses."""
    contract_dir, narrative_dir = live
    _run_pipeline(contract_dir, narrative_dir)
    with pytest.raises(S.ArtifactExists):
        S.write_day(_payload(date=DATE), root=narrative_dir)


def test_gemini_schema_conversion_is_what_the_api_accepts():
    """Gemini's responseSchema does not recognize $ref/$defs/additionalProperties, but
    does recognize propertyOrdering -- exactly what pins "judgement before writing"
    into the generation order."""
    g = C.to_gemini_schema(C.OUTPUT_SCHEMA)
    blob = json.dumps(g)
    assert "additionalProperties" not in blob and "$ref" not in blob
    assert g["type"] == "OBJECT"
    assert g["propertyOrdering"][0] == "evidence"
    assert g["properties"]["en"]["propertyOrdering"] == [
        "what_happened", "why_unexplained", "what_to_watch", "sources_used"]


# ========================================================= cross-day context

def test_previous_trigger_is_neutral_facts_only(tmp_path):
    """Gives the date, the gap, the event kind, the assessment and a one-sentence
    gist, and **no conclusion whatsoever about whether it is the same event**. Make
    that call for the model and it will write up two triggers two weeks apart with
    entirely different event kinds as a continuation."""
    cal = ["2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"]
    earlier = S.build_payload(
        "2026-07-31", window=126, model="ols", llm_model="m", prompt_version="p",
        trigger={}, pairs=[S.pair_record(
            fact=_fact(), sources=SOURCES, raw_output={},
            narrative={"en": {"what_happened":
                              "Reuters reported an intervention. More text here."}},
            evidence={"event_kind": "direct operation in the currency market",
                      "assessment": "accounts_for"},
            findings=_run(_narrative()))])
    S.write_day(earlier, root=tmp_path)

    prev = S.previous_trigger("USDJPY", "2026-08-03", root=tmp_path, calendar=cal)
    assert prev["date"] == "2026-07-31"
    assert prev["trading_days_ago"] == 1
    assert prev["event_kind"] == "direct operation in the currency market"
    assert prev["assessment"] == "accounts_for"
    # A one-sentence gist, not the full text
    assert prev["what_happened_gist"] == "Reuters reported an intervention."
    # No field of the "is it the same event" kind is allowed
    assert not any("same" in k or "related" in k or "continu" in k for k in prev)


def test_previous_trigger_ignores_the_same_day_and_later(tmp_path):
    payload = S.build_payload(
        "2026-08-03", window=126, model="ols", llm_model="m", prompt_version="p",
        trigger={}, pairs=[S.pair_record(
            fact=_fact(), sources=SOURCES, raw_output={}, narrative=_narrative(),
            evidence={"assessment": "accounts_for"}, findings=_run(_narrative()))])
    S.write_day(payload, root=tmp_path)
    assert S.previous_trigger("USDJPY", "2026-08-03", root=tmp_path) == {}


def test_previous_block_renders_and_handles_absence():
    fact = _fact()
    fact.previous = {"date": "2026-07-31", "trading_days_ago": 1,
                     "event_kind": "direct operation in the currency market",
                     "assessment": "accounts_for",
                     "what_happened_gist": "Reuters reported an intervention."}
    block = C.previous_block(fact)
    assert "2026-07-31" in block and "1 trading days" in block
    assert "accounts_for" in block
    # The gap goes on the whitelist, otherwise the model saying "one trading day ago"
    # would be killed by check 3
    assert "1" in fact.allowed_numbers()

    assert "no earlier flagged day" in C.previous_block(_fact())


def test_continuity_check_offers_three_outcomes_including_unrelated():
    """There must be more than the two paths of "continuation" and "say nothing"."""
    p = C.SYSTEM_PROMPT
    assert "Continuity check" in p
    assert "a new event that occurred on this day" in p
    assert "follow-on reporting about the event recorded on that earlier day" in p
    assert "something unrelated to that earlier day" in p
    assert "All three are ordinary answers" in p
    # The gap length must not draw the conclusion on the model's behalf
    assert "not automatically one episode" in p
    assert "not automatically separate" in p
    assert "continuity_check" in C.OUTPUT_SCHEMA["properties"]["evidence"]["required"]


def test_prompt_separates_report_date_from_event_date():
    """The most basic problem exposed by the second case, more basic than the
    cross-day mechanism itself."""
    p = C.SYSTEM_PROMPT
    assert "The date a story was published is not the date the event happened" in p
    assert "rebroadcast of old news dressed as today" in p


def test_prompt_keeps_outlet_names_untranslated_in_chinese():
    p = C.SYSTEM_PROMPT
    assert "NPR stays NPR" in p
    assert "国家公共电台" in p      # counter-example in the prompt, spelling out what not to do


def test_the_previous_block_actually_reaches_the_user_message():
    """This one exists because of a real bug: previous_block was written and run
    attached it, but the edit inserting it into build_user_message failed silently and
    the model never received the block. It then truthfully wrote "No previous flagged
    day was provided" -- nothing fabricated, but the check ran for nothing. The
    assembly step needs tests of its own; testing the components is not enough."""
    fact = _fact()
    fact.previous = {"date": "2026-07-31", "trading_days_ago": 1,
                     "event_kind": "direct operation in the currency market",
                     "assessment": "accounts_for",
                     "what_happened_gist": "Reuters reported an intervention."}
    message = C.build_user_message(fact, SOURCES)
    assert "Previous flagged day" in message
    assert "2026-07-31" in message
    assert "continuity check" in message


def test_user_message_says_no_previous_day_when_there_is_none():
    message = C.build_user_message(_fact(), SOURCES)
    assert "no earlier flagged day on record" in message
