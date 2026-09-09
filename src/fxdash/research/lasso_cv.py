"""Isolated Lasso CV-objective experiment. No production monkeypatch or writes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd
from sklearn.linear_model import Lasso

from .. import config
from ..models.ridge import time_series_splits
from ..models.rolling import RollingResult, _fit_once
from ..models.validation import prepare_fold
from .__main__ import capture_operations, source_hashes, strict_date, timestamp
from .evaluate import Arm, fit_path, metrics, paired_metrics
from .inputs import digest, protected_hashes, read_snapshot, reserve_output, save_snapshot, write_json

OBJECTIVES = ('penalized','post_ols')


class Tuner:
    """Independent estimator and audit log; each rolling state owns its lambda."""
    def __init__(self,objective):
        if objective not in OBJECTIVES:
            raise ValueError('unknown CV objective')
        self.objective = objective
        self.estimator = Lasso(fit_intercept=False,max_iter=20000,tol=1e-7,warm_start=False)
        self.records = []

    def lasso_beta(self,z,y,lam):
        self.estimator.set_params(alpha=lam)
        self.estimator.fit(z,y)
        return np.asarray(self.estimator.coef_,float).copy()

    @staticmethod
    def post_beta(z,y,beta):
        selected = np.abs(beta)>0
        result = np.zeros(z.shape[1])
        if selected.any():
            result[selected], *_ = np.linalg.lstsq(z[:,selected],y,rcond=None)
        return result,selected

    def curve(self,x,y,grid):
        errors = np.zeros(len(grid))
        folds = []
        for train,test in time_series_splits(len(y)):
            zt,yt,zv,yv = prepare_fold(x,y,train,test)
            fold_errors, selected_counts = [],[]
            for i,lam in enumerate(grid):
                beta = self.lasso_beta(zt,yt,lam)
                selected_counts.append(int((np.abs(beta)>0).sum()))
                if self.objective=='post_ols':
                    beta,_ = self.post_beta(zt,yt,beta)
                error = float(np.mean((yv-zv@beta)**2))
                errors[i] += error
                fold_errors.append(error)
            folds.append({'train_start':train.start,'train_stop_exclusive':train.stop,
                          'test_start':test.start,'test_stop_exclusive':test.stop,
                          'mse':fold_errors,'selected_counts':selected_counts})
        if not np.isfinite(errors).all():
            raise ValueError('non-finite CV scores')
        return errors,folds

    def select_lambda(self,x,y,state):
        grid = np.logspace(*config.LAMBDA_GRID_LOG10,config.LAMBDA_GRID_POINTS)
        attempts = []
        for attempt in range(3):
            errors,folds = self.curve(x,y,grid)
            best = int(np.argmin(errors))
            boundary = best in (0,len(grid)-1)
            attempts.append({'grid':grid.tolist(),'summed_fold_mse':errors.tolist(),
                             'folds':folds,'best_index':best,'boundary':boundary})
            if not boundary or attempt==2:
                lam = float(grid[best])
                self.records.append({'objective':self.objective,'tag':state.get('tag'),
                    'date':state.get('date'),'train_last':state.get('train_last'),
                    'train_observations':len(y),'factors':x.shape[1],
                    'lambda':lam,'attempts':attempts,'final_boundary':boundary})
                return lam
            low,high = np.log10(grid[0]),np.log10(grid[-1])
            if best==0:
                low -= 2.
            else:
                high += 2.
            grid = np.logspace(low,high,config.LAMBDA_GRID_POINTS)
        raise AssertionError('unreachable grid search')

    def solve(self,z,y,state,refit,*,cv_data=None):
        if refit or state.get('lam') is None:
            x_cv,y_cv = cv_data if cv_data is not None else (z,y)
            state['lam'] = self.select_lambda(x_cv,y_cv,state)
        beta,selected = self.post_beta(z,y,self.lasso_beta(z,y,state['lam']))
        if not selected.any():
            state['empty_selections'] = state.get('empty_selections',0)+1
        return {'beta_std':beta,'selected':selected,'lam':state['lam']}


def rolling_trial(panel,pair,window,factors,tuner):
    """Mirror the fixed production driver, injecting a solver by argument only."""
    if window not in config.WINDOWS or not 0<len(factors)<=config.MAX_FACTORS_PER_PAIR or len(set(factors))!=len(factors):
        raise ValueError('invalid window or factors')
    if len(panel)<=window or not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise ValueError('insufficient or invalid panel dates')
    x,y = panel[factors].to_numpy(float),panel.y.to_numpy(float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('non-finite training input')
    n = len(panel)-window
    betas,selected = np.empty((n,len(factors))),np.zeros((n,len(factors)),bool)
    lams,r2,r2e = np.empty(n),np.empty(n),np.full(n,np.nan)
    exog = [factors.index(f) for f in config.exogenous_factors(factors)]
    full_state = {'tag':f'{pair}/w{window}/{tuner.objective}/full'}
    exog_state = {'tag':f'{pair}/w{window}/{tuner.objective}/exog'}
    for step,t in enumerate(range(window,len(panel))):
        lo = t-window
        refit = step%config.LAMBDA_REFIT_EVERY==0
        for state in (full_state,exog_state):
            state.update(date=str(panel.index[t].date()),train_last=str(panel.index[t-1].date()))
        betas[step],selected[step],lams[step],r2[step] = _fit_once(x[lo:t],y[lo:t],tuner.solve,full_state,refit)
        if exog:
            _,_,_,r2e[step] = _fit_once(x[lo:t][:,exog],y[lo:t],tuner.solve,exog_state,refit)
    return RollingResult(dates=panel.index[window:],factors=list(factors),betas=betas,selected=selected,
                         lam=lams,r2_full=r2,r2_exog=r2e)


def core_sources(values):
    return {k:v for k,v in values.items() if k.startswith('src/fxdash/') and not k.startswith('src/fxdash/research/')}


def check_benchmarks(paths,metadata,current_sources,versions,args):
    benchmarks = {}
    for path in paths:
        root = Path(path).resolve()
        manifest = json.loads((root/'run.json').read_text(encoding='utf-8'))
        plan = manifest['plan']
        if manifest.get('state')!='complete' or len(plan['windows'])!=1:
            raise ValueError('benchmark must be a complete single-window experiment')
        window = plan['windows'][0]
        if window in benchmarks:
            raise ValueError('duplicate benchmark window')
        if (plan['start'],plan['end']) != (args.start,args.end) or plan['model_revision']!=config.MODEL_REVISION:
            raise ValueError('benchmark dates or revision differ')
        if not set(args.pairs).issubset(plan['pairs']):
            raise ValueError('benchmark lacks a requested pair')
        if any(manifest['input_panel_hashes'].get(p)!=metadata['panel_hashes'].get(p) for p in args.pairs):
            raise ValueError('benchmark inputs differ')
        if core_sources(manifest['source_hashes'])!=core_sources(current_sources) or manifest['versions']!=versions:
            raise ValueError('production calculation sources or environment differ from benchmark')
        for name,expected in manifest['artifacts'].items():
            target = (root/name).resolve()
            if not target.is_relative_to(root) or digest(target.read_bytes())!=expected:
                raise ValueError('benchmark artifact integrity failed')
        for pair in args.pairs:
            for menu in ('base','expanded'):
                if f'daily/{pair}_w{window}_lasso_{menu}.parquet' not in manifest['artifacts']:
                    raise ValueError('benchmark lacks sealed Lasso path')
        benchmarks[window] = (root,manifest)
    if not set(args.windows).issubset(benchmarks):
        raise ValueError('missing benchmark window')
    return benchmarks


def compare_selection(left,right,arm):
    names = [f'selected::{f}' for f in arm.factors]
    a,b = left[names].to_numpy(bool),right[names].to_numpy(bool)
    return {'empty_reference_pct':float(100*np.mean(~a.any(axis=1))),
            'empty_candidate_pct':float(100*np.mean(~b.any(axis=1))),
            'empty_change_pp':float(100*(np.mean(~b.any(axis=1))-np.mean(~a.any(axis=1)))),
            'selection_disagreement_pct':float(100*np.mean(np.any(a!=b,axis=1))),
            'lambda_disagreement_pct':float(100*np.mean(left['lambda'].to_numpy()!=right['lambda'].to_numpy()))}


def run(args,progress=print):
    if len(set(args.pairs))!=len(args.pairs) or len(set(args.windows))!=len(args.windows) or args.repetitions<1 or args.start>args.end:
        raise ValueError('invalid experiment settings')
    repo = args.repo_root.resolve()
    panels,metadata = read_snapshot(args.snapshot,args.end)
    if not set(args.pairs).issubset(panels):
        raise ValueError('snapshot lacks a requested pair')
    sources = source_hashes(repo)
    versions = {'python':platform.python_version(),**{k:importlib.metadata.version(k) for k in ('numpy','pandas','scikit-learn','pyarrow')}}
    benchmarks = check_benchmarks(args.benchmarks,metadata,sources,versions,args)
    before = protected_hashes(repo)
    out = reserve_output(args.out,repo)
    started = time.perf_counter()
    manifest = {'schema':'lasso-cv-objective-1','state':'running','created_at':timestamp(),
        'source_hashes':sources,'versions':versions,
        'protocol_sha256':digest((repo/'docs/RESEARCH_LASSO_CV_PLAN_20260907.md').read_bytes()),
        'plan':{'start':args.start,'end':args.end,'pairs':args.pairs,'windows':args.windows,'menus':['base','expanded'],
                'objectives':list(OBJECTIVES),'seed':args.seed,'repetitions':args.repetitions,'blocks':[5,21,63],
                'grid_log10':list(config.LAMBDA_GRID_LOG10),'grid_points':config.LAMBDA_GRID_POINTS,'max_attempts':3,
                'reselect_every':config.LAMBDA_REFIT_EVERY,'production_mutation':False,'network':False,
                'cv_intercept':'training-target mean; unchanged','attribution_intercept':'omitted; unchanged'},
        'benchmarks':{str(w):{'manifest_sha256':digest((r/'run.json').read_bytes()),'directory':r.name} for w,(r,_) in benchmarks.items()}}
    write_json(out/'run.json',manifest)
    write_json(out/'protected-before.json',before)
    try:
        saved = save_snapshot(out/'inputs',{p:panels[p] for p in args.pairs},{k:v for k,v in metadata.items() if k not in ('schema','panel_hashes')})
        manifest['input_panel_hashes'] = saved['panel_hashes']
        operations = capture_operations(repo)
        write_json(out/'operations.json',operations)
        (out/'daily').mkdir()
        (out/'tuning').mkdir()
        result = {'summaries':[],'comparisons':[],'baseline_reproduction':[],'tuning_audits':[]}
        for pair in args.pairs:
            original = panels[pair].loc[:args.end]
            if not isinstance(original.index,pd.DatetimeIndex) or original.index.tz is not None or not original.index.is_unique or not original.index.is_monotonic_increasing:
                raise ValueError('invalid snapshot calendar')
            if not pd.api.types.is_bool_dtype(original.provisional.dtype) or original.provisional.isna().any():
                raise ValueError('explicit provisional flags required')
            first = int(original.index.searchsorted(args.start))
            if first<max(args.windows) or first==len(original):
                raise ValueError('insufficient history or empty evaluation period')
            for window in args.windows:
                panel = original.iloc[first-window:]
                dates = panel.index[(panel.index>=pd.Timestamp(args.start)) & ~panel.provisional]
                if not len(dates):
                    raise ValueError('no final evaluation dates')
                periods = {'all':dates,**{str(y):dates[dates.year==y] for y in sorted(set(dates.year))}}
                for menu in ('base','expanded'):
                    factors = config.baseline_factors(pair) if menu=='base' else config.lasso_menu(pair)
                    arm = Arm(f'lasso_{menu}','lasso',tuple(factors))
                    paths = {}
                    for objective in OBJECTIVES:
                        progress(f'{pair} / {window} / {menu} / {objective}')
                        tuner = Tuner(objective)
                        fitted = rolling_trial(panel,pair,window,factors,tuner)
                        path = fit_path(panel,pair,window,arm,fitted=fitted)
                        name = f'{pair}_w{window}_{menu}_{objective}'
                        path.to_parquet(out/'daily'/f'{name}.parquet')
                        write_json(out/'tuning'/f'{name}.json',tuner.records)
                        full_traces = [t for t in tuner.records if t['tag'].endswith('/full')]
                        result['tuning_audits'].append({'pair':pair,'window':window,'menu':menu,'objective':objective,
                            'full_reselections':len(full_traces),'grid_expansions':sum(len(t['attempts'])-1 for t in full_traces),
                            'final_boundary_count':sum(t['final_boundary'] for t in full_traces)})
                        if objective=='penalized':
                            old = pd.read_parquet(benchmarks[window][0]/'daily'/f'{pair}_w{window}_lasso_{menu}.parquet')
                            pd.testing.assert_frame_equal(path,old,check_exact=True,check_freq=False)
                            result['baseline_reproduction'].append({'pair':pair,'window':window,'menu':menu,'exact':True,'observations':len(path)})
                        paths[objective] = path
                        for period,subset in periods.items():
                            result['summaries'].append({'pair':pair,'window':window,'menu':menu,'objective':objective,
                                'factors':factors,'period':period,**metrics(path.loc[subset],arm)})
                    for period,subset in periods.items():
                        left,right = paths['penalized'].loc[subset],paths['post_ols'].loc[subset]
                        key = f'{pair}/{window}/{menu}/{period}/{args.seed}/cv_objective'
                        seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4],'big')
                        result['comparisons'].append({'pair':pair,'window':window,'menu':menu,'period':period,
                            **paired_metrics(left,right,family='cv_objective',blocks=(5,21,63),repetitions=args.repetitions,seed=seed),
                            **compare_selection(left,right,arm)})
        write_json(out/'summary.json',result)
        pd.json_normalize(result['summaries']).to_csv(out/'metrics.csv',index=False)
        pd.json_normalize(result['comparisons']).to_csv(out/'comparisons.csv',index=False)
        pd.DataFrame(result['tuning_audits']).to_csv(out/'tuning-audit.csv',index=False)
        after = protected_hashes(repo)
        changes = sorted(k for k in before.keys() | after.keys() if before.get(k)!=after.get(k))
        write_json(out/'protected-after.json',after)
        manifest['protected_files'] = {'count':len(before),'changed':changes}
        if changes:
            raise ValueError('protected files changed during experiment')
        from .lasso_cv_report import render
        render(out,result,manifest,operations)
        manifest.update(state='complete',completed_at=timestamp(),elapsed_seconds=round(time.perf_counter()-started,2),
                        reference_paths_reproduced=len(result['baseline_reproduction']))
        manifest['artifacts'] = {p.relative_to(out).as_posix():digest(p.read_bytes()) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='run.json'}
        write_json(out/'run.json',manifest)
        progress(f'Complete: {out / "report.html"}')
        return out
    except Exception as exc:
        manifest.update(state='failed',failed_at=timestamp(),error=f'{type(exc).__name__}: {exc}')
        write_json(out/'run.json',manifest)
        raise


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--benchmarks',type=Path,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--repo-root',type=Path,default=config.REPO_ROOT)
    p.add_argument('--pairs',nargs='+',choices=config.PAIRS,default=list(config.PAIRS))
    p.add_argument('--windows',nargs='+',type=int,choices=config.WINDOWS,default=list(config.WINDOWS))
    p.add_argument('--start',type=strict_date,default='2023-01-01')
    p.add_argument('--end',type=strict_date,default='2025-12-31')
    p.add_argument('--repetitions',type=int,default=1000)
    p.add_argument('--seed',type=int,default=20260907)
    return p


if __name__=='__main__':
    run(parser().parse_args(),progress=lambda s:print(s,flush=True))
