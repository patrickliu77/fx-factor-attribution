"""Offline experiments must remain matched, reproducible and production-read-only."""
import copy
import json
import os
import socket
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from fxdash import config
from fxdash.data.alignment import USD_CLOSE, align_to_index, offset_for
from fxdash.research import __main__ as cli
from fxdash.research.evaluate import (
    Arm, allocation, arms_for, bootstrap_rmse_difference, comparisons_for,
    evaluate_pair, fit_path, metrics, moving_block_indices, paired_metrics,
)
from fxdash.research.inputs import (
    FileReader, load_raw_offline, prepare_panel, protected_hashes,
    read_snapshot, reserve_output, save_snapshot, write_json,
)
from fxdash.research.report import difference_svg, render_report


@pytest.fixture
def panel(rng):
    index = pd.bdate_range('2023-01-02',periods=155)
    names = {f for a in arms_for('USDCAD') for f in a.factors}
    frame = pd.DataFrame({f:rng.normal(0,.01,len(index)) for f in sorted(names)},index=index)
    frame['y'] = .7*frame.DOLLAR_LOO + rng.normal(0,.002,len(index))
    frame['provisional'] = False
    frame.loc[index[-1],'provisional'] = True
    return frame


@pytest.mark.parametrize('pair',config.PAIRS)
def test_matched_arms_only_change_intended_dimension(pair):
    original = copy.deepcopy(config.EXTRA_FACTORS)
    arms = {a.name:a for a in arms_for(pair)}
    assert len({arms[f'{m}_base'].factors for m in config.MODELS}) == 1
    assert arms['lasso_expanded'].factors == tuple(config.lasso_menu(pair))
    assert all(len(a.factors) <= 8 for a in arms.values())
    if pair in ('USDCAD','USDNOK'):
        old,new = ('WTI','BRENT') if pair == 'USDCAD' else ('BRENT','WTI')
        for m in config.MODELS:
            assert arms[f'{m}_oil_alt'].factors == tuple(new if f == old else f for f in arms[f'{m}_base'].factors)
    assert len(comparisons_for(pair)) == (6 if pair in ('USDCAD','USDNOK') else 3)
    assert original == config.EXTRA_FACTORS


@pytest.mark.parametrize('pair,alternative',[('USDCAD','BRENT'),('USDNOK','WTI')])
def test_oil_alignment_uses_original_fx_calendar(synthetic_raw,pair,alternative):
    panel,audit = prepare_panel(synthetic_raw,pair)
    index = synthetic_raw.fx_returns[pair].dropna().index
    aligned,_ = align_to_index(synthetic_raw.cmdty[alternative],index,offset_for(pair,USD_CLOSE))
    expected = np.log(aligned).diff().reindex(panel.index)
    np.testing.assert_allclose(panel[alternative],expected)
    # The fixture has a HY splice hole, so shifted-on-filtered-index would differ.
    assert len(panel) < len(index)-2
    assert panel.provisional.iloc[-1]
    assert np.isfinite(panel[['y',*audit['columns_checked']]]).all().all()


@pytest.mark.parametrize('model',['ols','ridge','lasso'])
def test_real_rolling_no_current_day_or_future_fit_data(panel,model):
    arm = next(a for a in arms_for('USDCAD') if a.name == f'{model}_base')
    small = panel.iloc[:68].copy()
    before = fit_path(small,'USDCAD',63,arm)
    changed = small.copy()
    changed.iloc[65,changed.columns.get_loc('y')] += .2
    changed.iloc[65,changed.columns.get_loc('DOLLAR_LOO')] += .3
    after = fit_path(changed,'USDCAD',63,arm)
    columns = [f'beta::{f}' for f in arm.factors]
    np.testing.assert_allclose(before.loc[:small.index[65],columns],after.loc[:small.index[65],columns])
    assert not np.isclose(before.loc[small.index[65],'residual'],after.loc[small.index[65],'residual'])
    repeat = fit_path(small,'USDCAD',63,arm)
    pd.testing.assert_frame_equal(before,repeat)
    parts = before.filter(like='contribution::').sum(axis=1)
    np.testing.assert_allclose(parts+before.residual,before.y,atol=1e-12)


def test_evaluator_common_dates_and_final_filter(panel):
    panel.loc[panel.index[90],'provisional'] = True
    result,paths = evaluate_pair(panel,'USDCAD',start=str(panel.index[80].date()),
        end=str(panel.index[-1].date()),windows=(63,),repetitions=10)
    summaries = [r for r in result['summaries'] if r['period']=='all']
    assert len(summaries) == 7
    assert {r['observations'] for r in summaries} == {73}
    assert len({(r['start'],r['end']) for r in summaries}) == 1
    assert result['audit']['excluded_provisional'] == 2
    assert len({tuple(p.index) for p in paths.values()}) == 1


@pytest.mark.parametrize('fault',['duplicate','nan','warmup','badflag','baddate'])
def test_evaluator_fails_closed(panel,fault):
    start = str(panel.index[80].date())
    if fault=='duplicate':
        panel = pd.concat([panel.iloc[:1],panel])
    elif fault=='nan':
        panel.iloc[100,0] = np.nan
    elif fault=='warmup':
        start = str(panel.index[1].date())
    elif fault=='badflag':
        panel['provisional'] = 'False'
    else:
        start = 'NaT'
    with pytest.raises(ValueError):
        evaluate_pair(panel,'USDCAD',start=start,end=str(panel.index[-1].date()),windows=(63,),repetitions=10)


def test_block_indices_non_circular_and_deterministic():
    ix = moving_block_indices(31,5,40,42)
    assert ix.shape == (40,31)
    assert ix.min() >= 0 and ix.max() < 31
    for start in range(0,30,5):
        assert (np.diff(ix[:,start:start+5],axis=1)==1).all()
    np.testing.assert_array_equal(ix,moving_block_indices(31,5,40,42))


def test_paired_bootstrap_and_short_samples():
    errors = np.linspace(-.01,.01,100)
    ci = bootstrap_rmse_difference(errors,errors,block=21,repetitions=100,seed=42)
    assert ci['low_bp'] == ci['high_bp'] == 0
    ci = bootstrap_rmse_difference(np.ones(100)*.001,np.ones(100)*.002,block=21,repetitions=100,seed=42)
    assert ci['low_bp'] == pytest.approx(10)
    assert ci['high_bp'] == pytest.approx(10)
    assert not bootstrap_rmse_difference(errors,errors,block=63,repetitions=10,seed=42)['available']
    with pytest.raises(ValueError):
        bootstrap_rmse_difference(errors,errors,block=0,repetitions=10,seed=42)


def test_oil_allocation_does_not_double_count_renamed_column():
    idx = pd.bdate_range('2024-01-01',periods=3)
    a = pd.DataFrame({'y':[.002,.003,.001],'residual':[.001,.002,0.],
                      'contribution::WTI':[.001,.001,.001]},index=idx)
    b = a.rename(columns={'contribution::WTI':'contribution::BRENT'})
    assert set(allocation(a,True)) == {'OIL'}
    result = paired_metrics(a,b,family='oil',blocks=[1],repetitions=10,seed=42)
    assert result['allocation_l1_bp'] == 0
    assert paired_metrics(a,b,family='menu',blocks=[1],repetitions=10,seed=42)['allocation_l1_bp'] == pytest.approx(20)
    with pytest.raises(ValueError):
        paired_metrics(a,b.iloc[1:],family='oil',blocks=[1],repetitions=10,seed=42)


def test_beta_drift_units_and_gap_exclusion():
    idx = pd.bdate_range('2024-01-01',periods=4)
    a = pd.DataFrame({'y':[.01]*4,'residual':[.001]*4,'train_r2':[.5]*4,
                      'position':[1,2,4,5],'beta::x':[1.,2.,100.,101.],
                      'sigma::x':[.001]*4,'selected::x':[True]*4},index=idx)
    result = metrics(a,Arm('ols_base','ols',('x',)))
    assert result['median_beta_step_scaled_bp'] == pytest.approx(10)
    assert result['mae_bp'] == pytest.approx(10)
    assert result['zero_rmse_bp'] == pytest.approx(100)
    b = a.copy()
    b['beta::x'] /= 100
    b['sigma::x'] *= 100
    assert metrics(b,Arm('ols_base','ols',('x',)))['median_beta_step_scaled_bp'] == pytest.approx(10)


def test_snapshot_integrity_and_no_overwrite(tmp_path,panel):
    target = tmp_path/'snapshot'
    save_snapshot(target,{'USDCAD':panel},{'model_revision':config.MODEL_REVISION,'end':'2025-12-31'})
    restored,_ = read_snapshot(target,'2025-12-31')
    pd.testing.assert_frame_equal(restored['USDCAD'],panel,check_freq=False)
    with pytest.raises(FileExistsError):
        save_snapshot(target,{'USDCAD':panel},{})
    with pytest.raises(ValueError,match='cutoff'):
        read_snapshot(target,'2024-12-31')
    (target/'USDCAD.parquet').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash'):
        read_snapshot(target,'2025-12-31')


def test_output_boundary_and_protected_hashes(tmp_path):
    repo = tmp_path/'repo'
    (repo/'outputs/contract').mkdir(parents=True)
    (repo/'outputs/contract/sentinel').write_bytes(b'keep')
    before = protected_hashes(repo)
    for bad in (repo,repo.parent,repo/'outputs',repo/'outputs/contract',repo/'outputs/research',repo/'src/new'):
        with pytest.raises(ValueError):
            reserve_output(bad,repo)
    out = reserve_output(repo/'outputs/research/one',repo)
    (out/'scratch').write_bytes(b'allowed')
    with pytest.raises(FileExistsError):
        reserve_output(out,repo)
    assert protected_hashes(repo) == before


def test_file_reader_rejects_unsorted_and_escape(tmp_path):
    idx = pd.to_datetime(['2024-01-02','2024-01-01'])
    pd.DataFrame({'v':[1.,2.]},index=idx).to_parquet(tmp_path/'x.parquet')
    with pytest.raises(ValueError,match='unsorted'):
        FileReader(tmp_path).frame('x.parquet')
    with pytest.raises(ValueError,match='escapes'):
        FileReader(tmp_path).frame('../x.parquet')


def test_offline_oas_tail_rejected_before_network(tmp_path,monkeypatch):
    folder = tmp_path/'data/user'
    folder.mkdir(parents=True)
    pd.DataFrame({'OAS':[3.]},index=pd.to_datetime(['2026-02-06'])).to_csv(folder/config.HY_OAS_USER_FILE)
    def forbidden(*args,**kwargs):
        raise AssertionError('network used')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    with pytest.raises(ValueError,match='offline HY OAS tail'):
        load_raw_offline(tmp_path,'2026-09-01')


def test_cli_offline_snapshot_replay_and_report(tmp_path,panel,monkeypatch):
    # Short real-engine run, including all seven CAD arms, export and replay.
    panel = panel.iloc[:69].copy()
    panel.loc[panel.index[-1],'provisional'] = True
    repo = tmp_path/'repo'
    (repo/'outputs/contract').mkdir(parents=True)
    (repo/'outputs/contract/sentinel').write_bytes(b'untouched')
    snapshot = tmp_path/'input'
    end = str(panel.index[-1].date())
    audits = {'USDCAD':{'production_join_loss':0,'alternative_oil_join_loss':0}}
    save_snapshot(snapshot,{'USDCAD':panel},{'model_revision':config.MODEL_REVISION,'end':end,'input_audits':audits})
    def forbidden(*args,**kwargs):
        raise AssertionError('raw loading or network used during replay')
    monkeypatch.setattr(cli,'load_raw_offline',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    args = cli.parser().parse_args(['--out',str(repo/'outputs/research/one'),'--repo-root',str(repo),
         '--snapshot',str(snapshot),'--pairs','USDCAD','--windows','63','--start',str(panel.index[63].date()),
         '--end',end,'--bootstrap-repetitions','10'])
    first = cli.run(args,progress=lambda _:None)
    args.out = repo/'outputs/research/two'
    args.snapshot = first/'inputs'
    second = cli.run(args,progress=lambda _:None)
    assert (first/'summary.json').read_bytes() == (second/'summary.json').read_bytes()
    assert json.loads((first/'run.json').read_text())['protected_files']['changed'] == []
    assert (repo/'outputs/contract/sentinel').read_bytes() == b'untouched'
    for name in ('estimator.svg','menu.svg','oil.svg','report.html','operations.json','comparisons.csv','metrics.csv'):
        assert (first/name).is_file()
    html = (first/'report.html').read_text(encoding='utf-8')
    assert '10 次重采样' in html and 'data:font/woff2;base64,' in html
    assert '<script' not in html and 'src="https://' not in html
    assert '生产模型与网站均未切换' in html


def test_svg_escapes_labels():
    row = {'pair':'<script>','window':126,'candidate':'ridge_base','rmse_change_bp':0.,'intervals':[]}
    svg = difference_svg([row],'estimator')
    assert '&lt;script&gt;' in svg and '<script>' not in svg


def test_paired_comparison_is_stable_across_process_hash_seeds():
    script = '''
import pandas as pd
from fxdash.research.evaluate import paired_metrics
index = pd.date_range('2024-01-01', periods=3)
left = pd.DataFrame({'y':[0.]*3, 'residual':[0.]*3},index=index)
right = left.copy()
for i, name in enumerate(['a','b','c','d','e','f','g']):
    left['contribution::'+name] = 1e8 if i == 0 else 1e-8
    right['contribution::'+name] = 0.
print(paired_metrics(left,right,family='menu',blocks=[1],repetitions=2,seed=1)['allocation_l1_bp'])
'''
    answers = []
    for seed in ('1','2','3'):
        env = {**os.environ,'PYTHONHASHSEED':seed,'PYTHONDONTWRITEBYTECODE':'1',
               'PYTHONPATH':str(config.REPO_ROOT/'src')}
        result = subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,check=True)
        answers.append(result.stdout.strip())
    assert len(set(answers)) == 1
