import json

import pandas as pd
import pytest

from ops.audit_model_migration import compare_contracts


def sample():
    return pd.DataFrame([dict(date=pd.Timestamp("2026-09-01"), pair="USDEUR",
                             window=126, model="ridge", y=.01, systematic=.006,
                             exogenous=.001, residual=.003,
                             contributions=json.dumps({"DOLLAR_LOO": .006, "dVIX": .001}),
                             selected_factors='["DOLLAR_LOO","dVIX"]')])


def test_comparison_uses_signed_contributions_and_closes_identity():
    old = sample()
    new = old.copy()
    new.loc[0, ["systematic", "residual"]] = [.007, .002]
    new.loc[0, "contributions"] = json.dumps({"DOLLAR_LOO": .007, "dVIX": .001})
    table, identity = compare_contracts(old, new)
    assert table.iloc[0].median_factor_distance_bp == pytest.approx(10)
    assert table.iloc[0].max_residual_change_bp == pytest.approx(10)
    assert table.iloc[0].max_return_change_bp == 0
    assert max(identity.values()) < 1e-12


@pytest.mark.parametrize("corruption", ["key", "duplicate", "identity", "nonfinite", "menu"])
def test_audit_rejects_unsafe_replacements(corruption):
    old, new = sample(), sample()
    if corruption == "key":
        new.loc[0, "pair"] = "USDJPY"
    elif corruption == "duplicate":
        new = pd.concat([new, new])
    elif corruption == "identity":
        new.loc[0, "residual"] = .5
    elif corruption == "nonfinite":
        new.loc[0, "contributions"] = '{"DOLLAR_LOO":null,"dVIX":0.001}'
    else:
        new.loc[0, "contributions"] = '{"OTHER":0.007}'
    with pytest.raises(ValueError):
        compare_contracts(old, new)
