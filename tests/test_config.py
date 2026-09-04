"""Self-consistency of the finalized parameters: factor cap, offset table, groups, tenors."""

from fxdash import config as cfg


def test_factor_cap_per_pair():
    # At most 8 per pair including optional factors (CLAUDE.md 19 / SPEC 3.6)
    for pair in cfg.PAIRS:
        menu = cfg.lasso_menu(pair)
        assert len(menu) <= cfg.MAX_FACTORS_PER_PAIR, (pair, menu)
        assert len(menu) == len(set(menu)), pair


def test_aud_menu_is_unchanged_and_exactly_eight():
    # AUD keeps HY_EXCESS only, no dHY_OAS added (SPEC_phase2 4.2)
    menu = cfg.lasso_menu("USDAUD")
    assert "dBAA10Y" not in menu
    assert "dHY_OAS" not in menu
    assert "HY_EXCESS" in menu
    assert len(menu) == 8


def test_dhy_oas_replaces_dbaa10y_in_every_menu():
    """dHY_OAS, available again, replaces its stand-in dBAA10Y (SPEC_phase2 4.2)."""
    for pair in cfg.PAIRS:
        menu = cfg.lasso_menu(pair)
        assert "dBAA10Y" not in menu, pair
        assert "HY_EXCESS" in menu, pair
        if pair != "USDAUD":
            assert "dHY_OAS" in menu, pair


def test_offsets_cover_all_pairs_and_match_frozen_table():
    # SPEC 1.2 frozen values; change the SPEC first
    expected = {
        "USDEUR": {"usd_close": 1, "foreign": 0},
        "USDJPY": {"usd_close": 1, "foreign": 1},
        "USDCAD": {"usd_close": 1, "foreign": 1},
        "USDNOK": {"usd_close": 1, "foreign": 1},
        "USDAUD": {"usd_close": 1, "foreign": 1},
        "USDMXN": {"usd_close": 0, "foreign": 0},
    }
    assert cfg.OFFSETS == expected


def test_us_leg_tenor_matches_foreign_tenor():
    # The US leg matches the foreign leg's actual tenor, keeping term-structure slope
    # out of the differential (SPEC 2.3)
    tenor_to_fred = {"1Y": "DGS1", "2Y": "DGS2", "3Y": "DGS3", "10Y": "DGS10"}
    for pair in cfg.PAIRS:
        short_tenor, long_tenor = cfg.FOREIGN_TENOR[pair]
        assert cfg.US_LEG[pair] == (
            tenor_to_fred[short_tenor],
            tenor_to_fred[long_tenor],
        ), pair


def test_carry_groups_are_disjoint_and_within_pairs():
    assert not set(cfg.LOW_YIELD) & set(cfg.HIGH_YIELD)
    assert set(cfg.LOW_YIELD) | set(cfg.HIGH_YIELD) <= set(cfg.PAIRS)
    # CAD and NOK sit in neither group and use the full carry (SPEC 3.4)
    assert {"USDCAD", "USDNOK"}.isdisjoint(set(cfg.LOW_YIELD) | set(cfg.HIGH_YIELD))


def test_exogenous_subset_only_drops_the_two_constructed_factors():
    # The exog subset drops only DOLLAR_LOO and CARRY_LOO, never an exogenous factor
    # (2026-08-27 ruling 1)
    for pair in cfg.PAIRS:
        menu = cfg.lasso_menu(pair)
        exog = cfg.exogenous_factors(menu)
        assert set(menu) - set(exog) == set(cfg.FX_INTERNAL_FACTORS)


def test_benchmark_table_covers_all_pairs():
    assert set(cfg.BENCHMARK_R2_MEAN) == set(cfg.PAIRS)


def test_commodity_benchmarks_are_pinned():
    # Benchmarks are pinned; the engine does not choose between WTI and Brent (SPEC 3.2)
    assert cfg.EXTRA_FACTORS["USDCAD"] == ["WTI"]
    assert cfg.EXTRA_FACTORS["USDNOK"] == ["BRENT"]
    assert cfg.EXTRA_FACTORS["USDAUD"] == ["COPPER", "GOLD"]
    assert cfg.EXTRA_FACTORS["USDJPY"] == ["GOLD"]
    assert cfg.EXTRA_FACTORS["USDMXN"] == ["EMB"]
    assert cfg.EXTRA_FACTORS["USDEUR"] == []


def test_display_path_is_repo_relative_and_ascii(tmp_path):
    """Console and log lines print repo-relative paths. An absolute path says
    nothing useful in a log, changes across machines, and is the one thing that
    can drag non-ASCII characters into an otherwise ASCII console log."""
    shown = cfg.display_path(cfg.REPO_ROOT / "outputs" / "reports" / "index.html")
    assert shown == "outputs/reports/index.html"
    assert shown.isascii()
    # Nothing to be relative to outside the repository: fall back to absolute
    outside = cfg.display_path(tmp_path / "x.html")
    assert outside.endswith("/x.html")
