"""Read three sealed single-window runs and build a local sensitivity report."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from fxdash import config
from fxdash.research.__main__ import capture_operations
from fxdash.research.evaluate import arms_for, comparisons_for
from fxdash.research.inputs import digest, protected_hashes, reserve_output, write_json
from fxdash.research.report import FAMILIES, LABELS, font_css, table

WINDOWS = (63,126,252)
COLORS = {63:'#2563eb',126:'#0f766e',252:'#a16207'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_runs(paths):
    """Fail closed on incomplete grids, changed evidence or unmatched observations."""
    runs = []
    for path in paths:
        root = Path(path).resolve()
        manifest = read(root/'run.json')
        if manifest.get('state') != 'complete' or len(manifest['plan']['windows']) != 1:
            raise ValueError('each source must be a complete single-window run')
        artifacts = manifest['artifacts']
        if 'summary.json' not in artifacts:
            raise ValueError('summary missing from sealed artifacts')
        for name, expected in artifacts.items():
            target = (root/name).resolve()
            if not target.is_relative_to(root) or digest(target.read_bytes()) != expected:
                raise ValueError(f'artifact integrity failed: {name}')
        window = manifest['plan']['windows'][0]
        result = read(root/'summary.json')
        pairs = manifest['plan']['pairs']
        if set(pairs) != set(config.PAIRS) or len(pairs) != len(config.PAIRS):
            raise ValueError('this review requires all six pairs')
        plan = manifest['plan']
        periods = ['all', *map(str,range(pd.Timestamp(plan['start']).year,pd.Timestamp(plan['end']).year+1))]
        expected_metrics = {(pair,window,a.name,period) for pair in pairs for a in arms_for(pair) for period in periods}
        expected_comparisons = {(pair,window,f,r,c,period) for pair in pairs for f,r,c in comparisons_for(pair) for period in periods}
        metric_keys = [(r['pair'],r['window'],r['arm'],r['period']) for r in result['summaries']]
        comparison_keys = [(r['pair'],r['window'],r['family'],r['reference'],r['candidate'],r['period']) for r in result['comparisons']]
        if set(metric_keys) != expected_metrics or len(metric_keys) != len(set(metric_keys)):
            raise ValueError('missing, extra or duplicated model result')
        if set(comparison_keys) != expected_comparisons or len(comparison_keys) != len(set(comparison_keys)):
            raise ValueError('missing, extra or duplicated paired comparison')
        observed = {}
        for pair in pairs:
            for arm in arms_for(pair):
                name = f'daily/{pair}_w{window}_{arm.name}.parquet'
                if name not in artifacts:
                    raise ValueError('daily path missing from sealed artifacts')
                frame = pd.read_parquet(root/name)
                if not frame.index.is_unique or not frame.index.is_monotonic_increasing or frame.provisional.isna().any():
                    raise ValueError('invalid daily path dates or flags')
                frame = frame.loc[plan['start']:plan['end']]
                y = frame.loc[~frame.provisional.astype(bool),'y']
                if y.empty or not np.isfinite(y).all():
                    raise ValueError('invalid evaluated observations')
                if pair in observed and not y.equals(observed[pair]):
                    raise ValueError('models do not share evaluated dates and targets')
                observed[pair] = y
        runs.append({'root':root,'manifest':manifest,'result':result,'window':window,'observed':observed})
    if len(runs) != 3 or {r['window'] for r in runs} != set(WINDOWS):
        raise ValueError('exactly one run for each of 63, 126 and 252 is required')
    runs.sort(key=lambda r:r['window'])
    reference = runs[0]
    fixed_plan = {k:v for k,v in reference['manifest']['plan'].items() if k != 'windows'}
    for run in runs[1:]:
        manifest = run['manifest']
        if {k:v for k,v in manifest['plan'].items() if k != 'windows'} != fixed_plan:
            raise ValueError('experiment settings differ beyond window length')
        for key in ('input_panel_hashes','source_hashes','versions'):
            if manifest[key] != reference['manifest'][key]:
                raise ValueError(f'unmatched {key}')
        for pair in fixed_plan['pairs']:
            if not run['observed'][pair].equals(reference['observed'][pair]):
                raise ValueError('windows do not share evaluated dates and targets')
    return runs


def grouped_rows(runs, family, period='all'):
    groups = {}
    for run in runs:
        for row in run['result']['comparisons']:
            if row['family'] == family and row['period'] == period:
                key = (row['pair'],row['reference'],row['candidate'])
                groups.setdefault(key,{})[run['window']] = row
    return groups


def interval(row, block=21):
    return next((i for i in row['intervals'] if i['block']==block and i['available']),None)


def direction_label(values):
    if not values or not np.isfinite(values).all():
        raise ValueError('finite window estimates required')
    if all(v<0 for v in values):
        return '三个点估计均降低'
    if all(v>0 for v in values):
        return '三个点估计均升高'
    if min(values)<0<max(values):
        return '方向随窗口改变'
    return '含零，未出现反向'


def forest(groups, family, standalone=False):
    """Three aligned facets with one shared bp scale per experiment family."""
    bounds = [0.]
    for group in groups.values():
        for row in group.values():
            bounds.append(row['rmse_change_bp'])
            ci = interval(row)
            if ci:
                bounds.extend([ci['low_bp'],ci['high_bp']])
    span = max(max(abs(v) for v in bounds)*1.12,.1)
    height = 112+44*len(groups)
    text_style = 'text{font:13px Outfit,"Microsoft YaHei",sans-serif;fill:#24334d}'
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 {height}" role="img" aria-labelledby="{family}-title">',
           f'<title id="{family}-title">{escape(FAMILIES[family][1])}，三个训练窗口的 RMSE 差，单位 bp</title>',
           '<style>'+(font_css() if standalone else '')+text_style+'</style>',
           f'<rect width="900" height="{height}" rx="16" fill="#f5f8fd"/>',
           '<text x="18" y="29">Pair / candidate</text>']
    for j,window in enumerate(WINDOWS):
        left = 310+190*j
        center = left+75
        def x(v):
            return center+v/span*75
        svg.append(f'<text x="{center}" y="29" text-anchor="middle">{window} observations</text>')
        for v in (-span,0,span):
            color = '#64748b' if v==0 else '#e2e8f0'
            svg.extend([f'<line x1="{x(v):.2f}" x2="{x(v):.2f}" y1="44" y2="{height-48}" stroke="{color}"/>',
                        f'<text x="{x(v):.2f}" y="{height-24}" text-anchor="middle">{v:+.2f}</text>'])
        for i,(key,group) in enumerate(groups.items()):
            row, y = group[window],66+44*i
            if j==0:
                svg.append(f'<text x="18" y="{y+4}">{escape(key[0]+" / "+LABELS[key[2]])}</text>')
            ci = interval(row)
            if ci:
                svg.append(f'<line x1="{x(ci["low_bp"]):.2f}" x2="{x(ci["high_bp"]):.2f}" y1="{y}" y2="{y}" stroke="{COLORS[window]}" stroke-width="3" opacity=".5"/>')
            svg.append(f'<circle cx="{x(row["rmse_change_bp"]):.2f}" cy="{y}" r="4.5" fill="{COLORS[window]}"/>')
    return '\n'.join([*svg,'</svg>'])


def build(runs,out,repo):
    before = protected_hashes(repo)
    out = reserve_output(out,repo)
    manifest = {'state':'running','aggregation_only':True,'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'script_sha256':digest(Path(__file__).read_bytes()),
                'source_runs':{str(r['window']):{'directory':r['root'].name,'manifest_sha256':digest((r['root']/'run.json').read_bytes())} for r in runs},
                'verified':'all sealed artifacts, full comparison grid, input hashes, source hashes, versions, evaluated dates and targets'}
    write_json(out/'review.json',manifest)
    result = {k:[row for run in runs for row in run['result'][k]] for k in ('summaries','comparisons')}
    write_json(out/'summary.json',result)
    pd.json_normalize(result['summaries']).to_csv(out/'metrics.csv',index=False)
    pd.json_normalize(result['comparisons']).to_csv(out/'comparisons.csv',index=False)
    operations = capture_operations(repo)
    write_json(out/'operations.json',operations)
    sections = []
    direction = []
    for family,(_,title,_) in FAMILIES.items():
        groups = grouped_rows(runs,family)
        svg = forest(groups,family)
        (out/f'{family}.svg').write_text(forest(groups,family,True),encoding='utf-8')
        rows = []
        for key,group in groups.items():
            values = [group[w]['rmse_change_bp'] for w in WINDOWS]
            sign = direction_label(values)
            ci_below = [w for w in WINDOWS if interval(group[w]) and interval(group[w])['high_bp']<0]
            direction.append({'family':family,'pair':key[0],'reference':key[1],'candidate':key[2],
                              'rmse_change_by_window':dict(zip(map(str,WINDOWS),values)),
                              'point_direction':sign,'ci21_below_zero_windows':ci_below})
            rows.append([key[0],LABELS[key[1]],LABELS[key[2]],*(f'{v:+.3f}' for v in values),sign,
                         ', '.join(map(str,ci_below)) or '无'])
        detailed = []
        for row in result['comparisons']:
            if row['family'] != family:
                continue
            cis = []
            for b in (5,21,63):
                ci = interval(row,b)
                cis.append(f'[{ci["low_bp"]:+.3f}, {ci["high_bp"]:+.3f}]' if ci else 'n/a')
            detailed.append([row['pair'],row['window'],row['period'],LABELS[row['candidate']],row['observations'],
                             f'{row["rmse_change_bp"]:+.3f}',f'{row["mae_change_bp"]:+.3f}',f'{row["allocation_l1_bp"]:.3f}',*cis])
        sections.append(f'<section id="{family}"><h2>{title}</h2><div class="figure-scroll">{svg}</div><p>三个小图共用横轴尺度。候选减参照，负值代表重构 RMSE 较低；线段为 21 区块的点对点 95% 区间。</p>'+
                        table(['Pair','Reference','Candidate','63 Δbp','126 Δbp','252 Δbp','点估计方向','CI21 全低于零的窗口'],rows)+
                        '<details><summary>全部年度结果、贡献分配差与区块长度敏感性</summary>'+table(
                            ['Pair','Window','Period','Candidate','n','Δ RMSE bp','Δ MAE bp','L1 bp','CI / 5','CI / 21','CI / 63'],detailed)+'</details></section>')
    write_json(out/'direction-summary.json',direction)
    audit_rows = [[run['window'],p,run['result']['audits'][p]['training_anchor'],str(y.index[0].date()),str(y.index[-1].date()),len(y)] for run in runs for p,y in run['observed'].items()]
    sections.append('<section><h2>样本与原始报告</h2>'+table(['Window','Pair','Training anchor','First','Last','n'],audit_rows)+
                    '<p>训练起点随窗口长度变化，评估日期与调参日历一致。旧 126 窗口保持原样。系数稳定性、选集频率和全部逐日路径可在各窗口原始报告与本页同目录的 JSON / CSV 查看。</p><ul>'+''.join(
                        f'<li><a href="{escape(Path(os.path.relpath(r["root"]/"report.html",out)).as_posix(),quote=True)}">{r["window"]} 观测窗口：详细报告</a></li>' for r in runs)+'</ul></section>')
    sections.append('<section><h2>证据边界</h2><p>这些历史数据已经参与项目开发，输入来自事后缓存快照。区间只对已拟合残差做成对区块重采样，没有每次重训，也未校正多重比较。三个窗口不是三份独立样本，方向一致不能自动升级为生产切换依据。当前研究不涉及收益预测或因果识别。</p><p>本轮未改变网站或任务。重复读取同一天的日志不算新增运行日；晨报自然运行和人工新闻标注仍需真实记录。</p><details><summary>运行状态快照</summary><pre>'+escape(json.dumps(operations,ensure_ascii=False,indent=2))+'</pre></details></section>')
    css = '''
*{box-sizing:border-box}body{margin:0;background:#eef2f8;color:#20304b;font:16px/1.75 Outfit,"Microsoft YaHei",sans-serif}main{max-width:1220px;margin:auto;padding:32px 24px 60px}header{padding:20px 0}h1{font-size:38px;line-height:1.3}h2{font-size:25px}a{color:#245bba}section{padding:28px;background:white;border:1px solid #dce3ef;border-radius:18px;margin:24px 0}nav{display:flex;gap:24px;flex-wrap:wrap}.notice{background:#e6effc;border-left:4px solid #4277cf;padding:16px 22px}.figure-scroll,.table-scroll{max-width:100%;overflow-x:auto;margin:20px 0}svg{display:block;width:100%;min-width:850px}table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #e4eaf3}th{background:#edf2fa}td{font-variant-numeric:tabular-nums}summary{cursor:pointer;color:#245bba;padding:12px 0}pre{font:12px/1.6 "IBM Plex Mono",monospace;white-space:pre-wrap;overflow-wrap:anywhere}footer{font-size:13px;color:#59708e}@media(max-width:620px){main{padding:16px 12px}h1{font-size:29px}section{padding:20px 14px}}
'''
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FX · 窗口敏感性研究</title><style>'+font_css()+css+'</style></head><body><main><header><p>FX ATTRIBUTION / WINDOW SENSITIVITY</p><h1>换一个窗口，结论还成立吗？</h1><p>63 / 126 / 252 个观测 · 六货币 · 2023 至 2025 年 · 90 条模型路径</p><p class="notice">同一份输入快照，同一批评估日期。三个窗口内均保留全部估计器、菜单与油价对照。这里只评估已实现变化的归因重构，生产口径保持不变。</p><nav><a href="#estimator">估计器</a><a href="#menu">因子菜单</a><a href="#oil">油价基准</a></nav></header>'+''.join(sections)+'<footer>页面与 SVG 可离线打开。Outfit / IBM Plex Mono 与网站一致；中文使用系统字体。字体许可证随报告保存。</footer></main></body></html>'
    (out/'report.html').write_text(html,encoding='utf-8')
    for path in runs[0]['root'].glob('OFL-*.txt'):
        (out/path.name).write_bytes(path.read_bytes())
    after = protected_hashes(repo)
    changes = sorted(k for k in before.keys() | after.keys() if before.get(k)!=after.get(k))
    manifest.update(state='complete' if not changes else 'failed',protected_files={'count':len(before),'changed':changes},
                    artifacts={p.name:digest(p.read_bytes()) for p in out.iterdir() if p.is_file() and p.name!='review.json'})
    write_json(out/'review.json',manifest)
    if changes:
        raise ValueError('protected files changed during aggregation')
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('runs',nargs=3,type=Path)
    p.add_argument('--out',required=True,type=Path)
    args = p.parse_args()
    out = build(load_runs(args.runs),args.out,Path(__file__).resolve().parents[1])
    print(out/'report.html')


if __name__=='__main__':
    main()
