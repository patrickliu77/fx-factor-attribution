"""The counterfactual changes fold scoring only and never mutates production."""
import importlib.metadata
import json
import platform
import socket

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Lasso

from fxdash import config
from fxdash.models import lasso as production_lasso
from fxdash.models.rolling import ROLLING_SOLVERS
from fxdash.models.ridge import time_series_splits
from fxdash.research.evaluate import Arm,fit_path
from fxdash.research.inputs import digest,save_snapshot,write_json
from fxdash.research import lasso_cv as research
from fxdash.research.lasso_cv_report import comparison_svg


@pytest.fixture
def panel(rng):
    index = pd.bdate_range('2020-01-02',periods=280)
    factors = config.lasso_menu('USDMXN')
    frame = pd.DataFrame(rng.normal(0,.01,(len(index),len(factors))),index=index,columns=factors)
    frame['CARRY_LOO'] = .8*frame.DOLLAR_LOO+rng.normal(0,.003,len(index))
    frame['y'] = .6*frame.DOLLAR_LOO+rng.normal(0,.003,len(index))
    frame['provisional'] = False
    frame.loc[index[-1],'provisional'] = True
    return frame


def test_original_fold_curve_exactly_reproduced(panel):
    x,y = panel[config.lasso_menu('USDMXN')].to_numpy(),panel.y.to_numpy()
    grid = np.logspace(-6,-2,7)
    actual,folds = research.Tuner('penalized').curve(x,y,grid)
    expected = production_lasso._cv_error(x,y,grid)
    np.testing.assert_array_equal(actual,expected)
    assert len(folds)==3


def test_candidate_curve_uses_fold_local_post_ols(panel):
    x,y = panel[config.lasso_menu('USDMXN')].to_numpy()[:63],panel.y.to_numpy()[:63]
    grid = np.array([1e-5,1e-3,1.])
    expected = np.zeros(len(grid))
    for train,test in time_series_splits(len(y)):
        xt,yt = x[train],y[train]
        scale = xt.std(axis=0)
        scale[scale==0] = 1
        zt,zv = (xt-xt.mean(axis=0))/scale,(x[test]-xt.mean(axis=0))/scale
        yc,yv = yt-yt.mean(),y[test]-yt.mean()
        for i,lam in enumerate(grid):
            fitted = Lasso(alpha=lam,fit_intercept=False,max_iter=20000,tol=1e-7,warm_start=False).fit(zt,yc)
            selected = np.abs(fitted.coef_)>0
            beta = np.zeros(x.shape[1])
            if selected.any():
                beta[selected], *_ = np.linalg.lstsq(zt[:,selected],yc,rcond=None)
            expected[i] += np.mean((yv-zv@beta)**2)
    actual,folds = research.Tuner('post_ols').curve(x,y,grid)
    np.testing.assert_array_equal(actual,expected)
    assert all(f['train_stop_exclusive']<=f['test_start'] for f in folds)
    assert all(f['selected_counts'][-1]==0 for f in folds)


@pytest.mark.parametrize('window',[63,126,252])
def test_reference_driver_matches_production_without_monkeypatch(panel,window):
    original = dict(ROLLING_SOLVERS)
    small = panel.iloc[:window+4].copy()
    arm = Arm('lasso_expanded','lasso',tuple(config.lasso_menu('USDMXN')))
    expected = fit_path(small,'USDMXN',window,arm)
    tuner = research.Tuner('penalized')
    fitted = research.rolling_trial(small,'USDMXN',window,list(arm.factors),tuner)
    actual = fit_path(small,'USDMXN',window,arm,fitted=fitted)
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)
    assert ROLLING_SOLVERS==original
    assert {r['tag'].rsplit('/',1)[-1] for r in tuner.records}=={'full','exog'}


@pytest.mark.parametrize('objective',research.OBJECTIVES)
def test_current_day_does_not_enter_beta_or_tuning(panel,objective):
    small = panel.iloc[:67].copy()
    factors = config.lasso_menu('USDMXN')
    before_tuner = research.Tuner(objective)
    before = research.rolling_trial(small,'USDMXN',63,factors,before_tuner)
    changed = small.copy()
    changed.loc[small.index[63]:,'y'] += .1
    changed.loc[small.index[63]:,'DOLLAR_LOO'] += .2
    after_tuner = research.Tuner(objective)
    after = research.rolling_trial(changed,'USDMXN',63,factors,after_tuner)
    np.testing.assert_array_equal(before.betas[0],after.betas[0])
    assert before_tuner.records==after_tuner.records
    assert all(r['train_last']<r['date'] for r in before_tuner.records)


@pytest.mark.parametrize('edge',['low','high','tie'])
def test_grid_expansion_and_first_minimum_tie_rule(panel,monkeypatch,edge):
    tuner = research.Tuner('post_ols')
    def curve(x,y,grid):
        values = np.arange(len(grid),dtype=float)
        if edge=='high':
            values = values[::-1]
        elif edge=='tie':
            values[:] = 0
        return values,[]
    monkeypatch.setattr(tuner,'curve',curve)
    lam = tuner.select_lambda(panel.iloc[:63,:2].to_numpy(),panel.y.to_numpy()[:63],{})
    exponent = config.LAMBDA_GRID_LOG10[1]+4 if edge=='high' else config.LAMBDA_GRID_LOG10[0]-4
    assert lam==pytest.approx(10.**exponent)
    assert len(tuner.records[0]['attempts'])==3 and tuner.records[0]['final_boundary']


def test_empty_selection_and_lambda_reuse(panel,monkeypatch):
    tuner = research.Tuner('post_ols')
    def forbidden(*args):
        raise AssertionError('lambda was reselected')
    monkeypatch.setattr(tuner,'select_lambda',forbidden)
    state = {'lam':1e4}
    solved = tuner.solve(panel.iloc[:63,:2].to_numpy(),panel.y.to_numpy()[:63],state,False)
    assert not solved['selected'].any() and not solved['beta_std'].any()
    assert state['empty_selections']==1 and not tuner.records


@pytest.mark.parametrize('fault',['nan','short','duplicate','too_many'])
def test_driver_rejects_invalid_inputs(panel,fault):
    factors = config.lasso_menu('USDMXN')
    if fault=='nan':
        panel.iloc[10,0] = np.nan
    elif fault=='short':
        panel = panel.iloc[:63]
    elif fault=='duplicate':
        panel = pd.concat([panel.iloc[:1],panel])
    else:
        factors = factors+['y']
    with pytest.raises(ValueError):
        research.rolling_trial(panel,'USDMXN',63,factors,research.Tuner('post_ols'))


def test_shared_pack_rejects_other_factors(panel):
    small = panel.iloc[:67]
    factors = config.baseline_factors('USDMXN')
    fitted = research.rolling_trial(small,'USDMXN',63,factors,research.Tuner('penalized'))
    with pytest.raises(ValueError,match='factors or dates'):
        fit_path(small,'USDMXN',63,Arm('lasso_expanded','lasso',tuple(config.lasso_menu('USDMXN'))),fitted=fitted)


def test_selection_comparison_has_percentage_point_units():
    idx = pd.date_range('2020-01-01',periods=4)
    left = pd.DataFrame({'selected::x':[False,False,True,True],'lambda':[1.,1.,2.,2.]},index=idx)
    right = pd.DataFrame({'selected::x':[True,False,True,True],'lambda':[1.,2.,2.,2.]},index=idx)
    result = research.compare_selection(left,right,Arm('lasso','lasso',('x',)))
    assert result['empty_reference_pct']==50 and result['empty_candidate_pct']==25
    assert result['empty_change_pp']==-25 and result['lambda_disagreement_pct']==25


def test_cli_offline_exact_reference_and_replay(tmp_path,panel,monkeypatch):
    repo = tmp_path/'repo'
    (repo/'docs').mkdir(parents=True)
    (repo/'docs/RESEARCH_LASSO_CV_PLAN_20260907.md').write_text('fixture protocol',encoding='utf-8')
    (repo/'outputs/contract').mkdir(parents=True)
    (repo/'outputs/contract/sentinel').write_bytes(b'unchanged')
    small = panel.iloc[:68].copy()
    small.loc[small.index[-1],'provisional'] = True
    start,end = str(small.index[63].date()),str(small.index[-1].date())
    snapshot = tmp_path/'snapshot'
    meta = save_snapshot(snapshot,{'USDMXN':small},{'model_revision':config.MODEL_REVISION,'end':end})
    benchmark = tmp_path/'benchmark'
    (benchmark/'daily').mkdir(parents=True)
    for menu in ('base','expanded'):
        factors = config.baseline_factors('USDMXN') if menu=='base' else config.lasso_menu('USDMXN')
        path = fit_path(small,'USDMXN',63,Arm(f'lasso_{menu}','lasso',tuple(factors)))
        path.to_parquet(benchmark/'daily'/f'USDMXN_w63_lasso_{menu}.parquet')
    manifest = {'state':'complete','plan':{'windows':[63],'start':start,'end':end,'pairs':['USDMXN'],'model_revision':config.MODEL_REVISION},
        'input_panel_hashes':meta['panel_hashes'],'source_hashes':research.source_hashes(repo),
        'versions':{'python':platform.python_version(),**{k:importlib.metadata.version(k) for k in ('numpy','pandas','scikit-learn','pyarrow')}},
        'artifacts':{p.relative_to(benchmark).as_posix():digest(p.read_bytes()) for p in (benchmark/'daily').iterdir()}}
    write_json(benchmark/'run.json',manifest)
    def forbidden(*args,**kwargs):
        raise AssertionError('network used')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    args = research.parser().parse_args(['--snapshot',str(snapshot),'--benchmarks',str(benchmark),'--repo-root',str(repo),
        '--out',str(repo/'outputs/research/one'),'--pairs','USDMXN','--windows','63','--start',start,'--end',end,'--repetitions','10'])
    first = research.run(args,progress=lambda _:None)
    args.out = repo/'outputs/research/two'
    args.snapshot = first/'inputs'
    second = research.run(args,progress=lambda _:None)
    assert (first/'summary.json').read_bytes()==(second/'summary.json').read_bytes()
    assert len(json.loads((first/'summary.json').read_text())['baseline_reproduction'])==2
    assert json.loads((first/'run.json').read_text())['protected_files']['changed']==[]
    assert (repo/'outputs/contract/sentinel').read_bytes()==b'unchanged'
    html = (first/'report.html').read_text(encoding='utf-8')
    assert '相同最终 OLS 重估' in html and '<script' not in html
    with pytest.raises(FileExistsError):
        research.run(args,progress=lambda _:None)
    manifest['input_panel_hashes']['USDMXN'] = 'changed'
    write_json(benchmark/'run.json',manifest)
    with pytest.raises(ValueError,match='inputs differ'):
        research.run(args,progress=lambda _:None)


def test_plot_labels_escape_and_no_interval():
    row = {'pair':'<unsafe>','window':63,'rmse_change_bp':0.,'intervals':[]}
    svg = comparison_svg([row],'base',[63])
    assert '&lt;unsafe&gt;' in svg and '<unsafe>' not in svg
