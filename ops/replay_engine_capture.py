"""Read-only crash reproduction using a verified saved input capture.

Runs existing estimators without fetching, merging or writing production outputs.
Use python -u -X faulthandler and redirect the diagnostic log outside the contract.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    from fxdash.data.vintages import replay_raw
    from fxdash.config import PAIRS, WINDOWS, MODELS
    from fxdash.run import design_for
    from fxdash.factors.build import build_pair_panel
    from fxdash.models.rolling import rolling_fit
    from fxdash.models.pca_monitor import run_monitor
    from fxdash.attribution.engine import attribute, identity_error
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    raw = replay_raw(args.capture)
    print('Verified capture loaded', flush=True)
    for pair in PAIRS:
        panel = build_pair_panel(pair, raw)
        for window in WINDOWS:
            for model in MODELS:
                print(f'Start {pair}/{window}/{model}', flush=True)
                result = attribute(panel, rolling_fit(panel, pair, window, model, design_for(pair, model)))
                error = identity_error(result)
                if error > 1e-12:
                    raise ValueError('identity_check_failed')
                print(json.dumps({'pair': pair, 'window': window, 'model': model,
                                  'observations': len(result.dates), 'identity_error': error}), flush=True)
    for window in WINDOWS:
        print(f'Start PCA/{window}', flush=True)
        print(f'PCA rows: {len(run_monitor(raw.fx_returns, window))}', flush=True)
    print('Replay completed; no production files written', flush=True)


if __name__ == '__main__':
    main()
