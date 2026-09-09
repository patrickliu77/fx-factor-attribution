"""Cross-window reports reject incompatible or altered source experiments."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fxdash import config
from fxdash.research.evaluate import arms_for, comparisons_for
from fxdash.research.inputs import digest, write_json

_spec = importlib.util.spec_from_file_location('window_review',Path(__file__).resolve().parents[1]/'ops/summarize_window_research.py')
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)


def reseal(root,manifest=None):
    manifest = manifest or review.read(root/'run.json')
    manifest['artifacts'] = {p.relative_to(root).as_posix():digest(p.read_bytes())
                             for p in root.rglob('*') if p.is_file() and p.name!='run.json'}
    write_json(root/'run.json',manifest)


@pytest.fixture
def source_runs(tmp_path):
    roots = []
    for w in (63,126,252):
        root = tmp_path/f'w{w}'
        (root/'daily').mkdir(parents=True)
        plan = {'windows':[w],'start':'2023-01-01','end':'2025-12-31','pairs':list(config.PAIRS),'seed':1}
        manifest = {'state':'complete','plan':plan,'input_panel_hashes':{'fixture':'identical'},
                    'source_hashes':{'fixture':'identical'},'versions':{'fixture':'identical'}}
        summaries,comparisons,audits = [],[],{}
        for pair in config.PAIRS:
            frame = pd.DataFrame({'y':[.001,.002,.003],'provisional':[False]*3},
                                 index=pd.to_datetime(['2023-01-02','2024-01-02','2025-12-30']))
            audits[pair] = {'training_anchor':'2022-01-03'}
            for arm in arms_for(pair):
                frame.to_parquet(root/'daily'/f'{pair}_w{w}_{arm.name}.parquet')
                summaries.extend({'pair':pair,'window':w,'arm':arm.name,'period':p} for p in ('all','2023','2024','2025'))
            for family,reference,candidate in comparisons_for(pair):
                for period in ('all','2023','2024','2025'):
                    comparisons.append({'pair':pair,'window':w,'family':family,'reference':reference,'candidate':candidate,
                        'period':period,'rmse_change_bp':-.1,'mae_change_bp':-.1,'allocation_l1_bp':.2,'observations':3,
                        'intervals':[{'block':b,'available':False} for b in (5,21,63)]})
        write_json(root/'summary.json',{'summaries':summaries,'comparisons':comparisons,'audits':audits})
        (root/'report.html').write_text('<p>fixture</p>',encoding='utf-8')
        reseal(root,manifest)
        roots.append(root)
    return roots


def test_load_and_build_all_windows_read_only(source_runs,tmp_path):
    manifests = [(p/'run.json').read_bytes() for p in source_runs]
    runs = review.load_runs(list(reversed(source_runs)))
    assert [r['window'] for r in runs] == [63,126,252]
    out = review.build(runs,tmp_path/'report',tmp_path/'repo')
    assert len(review.read(out/'direction-summary.json')) == 24
    assert len(review.read(out/'summary.json')['comparisons']) == 288
    html = (out/'report.html').read_text(encoding='utf-8')
    assert html.count('<svg') == 3 and 'data:font/woff2;base64,' in html
    assert 'src="https://' not in html and '<script' not in html
    assert [(p/'run.json').read_bytes() for p in source_runs] == manifests
    with pytest.raises(FileExistsError):
        review.build(runs,out,tmp_path/'repo')


@pytest.mark.parametrize('field',['input_panel_hashes','source_hashes','versions'])
def test_unmatched_provenance_rejected(source_runs,field):
    manifest = review.read(source_runs[1]/'run.json')
    manifest[field]['fixture'] = 'different'
    write_json(source_runs[1]/'run.json',manifest)
    with pytest.raises(ValueError,match=field):
        review.load_runs(source_runs)


def test_modified_artifact_rejected(source_runs):
    (source_runs[0]/'report.html').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='integrity'):
        review.load_runs(source_runs)


@pytest.mark.parametrize('problem',['missing','duplicate','settings','dates','target','model_dates','unfinished','window'])
def test_incomplete_or_unmatched_grid_rejected(source_runs,problem):
    root = source_runs[1]
    manifest = review.read(root/'run.json')
    if problem in ('missing','duplicate'):
        data = review.read(root/'summary.json')
        if problem == 'missing':
            data['comparisons'].pop()
        else:
            data['comparisons'].append(data['comparisons'][0])
        write_json(root/'summary.json',data)
    elif problem in ('dates','target','model_dates'):
        paths = list((root/'daily').glob('*.parquet'))
        for path in paths[:1] if problem=='model_dates' else paths:
            frame = pd.read_parquet(path)
            if problem=='target':
                frame['y'] += .0001
            else:
                frame.index = pd.to_datetime(['2023-01-03','2024-01-02','2025-12-30'])
            frame.to_parquet(path)
    elif problem=='settings':
        manifest['plan']['seed'] = 2
    elif problem=='unfinished':
        manifest['state'] = 'running'
    else:
        manifest['plan']['windows'] = [63,126]
    reseal(root,manifest)
    with pytest.raises(ValueError):
        review.load_runs(source_runs)


@pytest.mark.parametrize('values,expected',[([-1,-2,-3],'三个点估计均降低'),([1,2,3],'三个点估计均升高'),
    ([-1,1,0],'方向随窗口改变'),([0,1,2],'含零，未出现反向'),([0,0,0],'含零，未出现反向')])
def test_direction_labels(values,expected):
    assert review.direction_label(values) == expected


def test_invalid_estimate_and_svg_escaping():
    with pytest.raises(ValueError):
        review.direction_label([0,np.nan,1])
    group = {('<bad>','ols_base','ridge_base'):{w:{'rmse_change_bp':0.,'intervals':[]} for w in (63,126,252)}}
    svg = review.forest(group,'estimator')
    assert '&lt;bad&gt;' in svg and '<bad>' not in svg
