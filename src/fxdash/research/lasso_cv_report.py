"""Offline charts for the predeclared CV-objective experiment."""
from html import escape
import json
from pathlib import Path

from .report import font_css, table


def ci21(row):
    return next((c for c in row['intervals'] if c['block']==21 and c['available']),None)


def comparison_svg(rows,menu,windows,standalone=False):
    pairs = list(dict.fromkeys(r['pair'] for r in rows))
    indexed = {(r['pair'],r['window']):r for r in rows}
    bounds = [0.,*(r['rmse_change_bp'] for r in rows)]
    bounds.extend(c[k] for r in rows if (c:=ci21(r)) for k in ('low_bp','high_bp'))
    span = max(max(abs(v) for v in bounds)*1.12,.1)
    width,height = 280+200*len(windows),115+50*len(pairs)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title-{menu}">',
        f'<title id="title-{menu}">{menu} 菜单：候选 CV 减原 CV 的 RMSE 差，单位 bp</title>',
        '<style>'+(font_css() if standalone else '')+'text{font:13px Outfit,"Microsoft YaHei",sans-serif;fill:#24334d}</style>',
        f'<rect width="{width}" height="{height}" rx="16" fill="#f5f8fd"/>',
        '<text x="20" y="30">Pair / Δ RMSE bp</text>']
    for j,w in enumerate(windows):
        center = 325+200*j
        color = {63:'#2563eb',126:'#0f766e',252:'#a16207'}[w]
        def x(v):
            return center+v/span*70
        parts.append(f'<text x="{center}" y="30" text-anchor="middle">{w} observations</text>')
        for v in (-span,0,span):
            parts.extend([f'<line x1="{x(v):.2f}" x2="{x(v):.2f}" y1="44" y2="{height-48}" stroke="{"#64748b" if v==0 else "#e2e8f0"}"/>',
                          f'<text x="{x(v):.2f}" y="{height-24}" text-anchor="middle">{v:+.2f}</text>'])
        for i,pair in enumerate(pairs):
            row,y = indexed[(pair,w)],66+50*i
            if j==0:
                parts.append(f'<text x="20" y="{y+5}">{escape(pair)}</text>')
            ci = ci21(row)
            if ci:
                parts.append(f'<line x1="{x(ci["low_bp"]):.2f}" x2="{x(ci["high_bp"]):.2f}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="3" opacity=".5"/>')
            parts.append(f'<circle cx="{x(row["rmse_change_bp"]):.2f}" cy="{y}" r="4.5" fill="{color}"/>')
    return '\n'.join([*parts,'</svg>'])


def render(out,result,manifest,operations):
    out = Path(out)
    windows = manifest['plan']['windows']
    sections = []
    for menu in ('base','expanded'):
        rows = [r for r in result['comparisons'] if r['menu']==menu and r['period']=='all']
        title = 'Baseline 菜单' if menu=='base' else 'Expanded 菜单'
        svg = comparison_svg(rows,menu,windows)
        (out/f'{menu}.svg').write_text(comparison_svg(rows,menu,windows,True),encoding='utf-8')
        values = [[r['pair'],r['window'],f'{r["rmse_change_bp"]:+.3f}',f'{r["mae_change_bp"]:+.3f}',
            f'{r["empty_reference_pct"]:.2f}',f'{r["empty_candidate_pct"]:.2f}',f'{r["empty_change_pp"]:+.2f}',
            f'{r["lambda_disagreement_pct"]:.1f}',f'{r["selection_disagreement_pct"]:.1f}',f'{r["allocation_l1_bp"]:.3f}'] for r in rows]
        annual = []
        for r in result['comparisons']:
            if r['menu']!=menu:
                continue
            cis = {c['block']:f'[{c["low_bp"]:+.3f}, {c["high_bp"]:+.3f}]' if c['available'] else 'n/a' for c in r['intervals']}
            annual.append([r['pair'],r['window'],r['period'],r['observations'],f'{r["rmse_change_bp"]:+.3f}',
                           f'{r["empty_change_pp"]:+.2f}',*(cis[b] for b in (5,21,63))])
        sections.append(f'<section id="{menu}"><h2>{title}</h2><div class="figure-scroll">{svg}</div><p>同一菜单内只更改验证折的评分系数。候选减参照，负值代表候选重构 RMSE 较低；线段为 21 区块的点对点 95% 区间。</p>'+table(
            ['Pair','Window','Δ RMSE bp','Δ MAE bp','原空集 %','候选空集 %','空集差 pp','λ 不同 %','选集不同 %','贡献 L1 bp'],values)+
            '<details><summary>全部年度结果与区块长度敏感性</summary>'+table(
                ['Pair','Window','Period','n','Δ RMSE bp','空集差 pp','CI / 5','CI / 21','CI / 63'],annual)+'</details></section>')
    metrics_rows = []
    for r in result['summaries']:
        if r['period']!='all':
            continue
        sel = r['selection']
        metrics_rows.append([r['pair'],r['window'],r['menu'],r['objective'],f'{r["rmse_bp"]:.3f}',
            f'{r["residual_abs_p95_bp"]:.3f}',f'{r["median_beta_step_scaled_bp"]:.3f}' if r['median_beta_step_scaled_bp'] is not None else 'n/a',
            f'{100*sel["switch_fraction"]:.2f}' if sel['switch_fraction'] is not None else 'n/a',f'{100*sel["empty_fraction"]:.2f}'])
    sections.append('<section id="stability"><h2>绝对误差与选择稳定性</h2><p>空集更少并不自动意味着误差更低或归因更可信。β 变动按前一期训练标准差调整尺度，逐因子选中频率及按年指标保存在 JSON / CSV。</p>'+table(
        ['Pair','Window','Menu','CV objective','RMSE bp','|e| p95 bp','β step bp','Switch %','Empty %'],metrics_rows)+'</section>')
    audit_rows = [[r['pair'],r['window'],r['menu'],r['objective'],r['full_reselections'],r['grid_expansions'],r['final_boundary_count']] for r in result['tuning_audits']]
    sections.append('<section><h2>λ 选择与复现</h2><p>两方案使用相同初始网格与扩展规则；评分不同可能触发不同扩展路径。tuning/ 保存每次重选的各折误差、完整网格、选中列数和训练截止日。下表只汇总 full 分支，exog 诊断日志也完整保存。</p>'+table(
        ['Pair','Window','Menu','CV objective','重选次数','网格扩展次数','最终边界次数'],audit_rows)+
        f'<p>{len(result["baseline_reproduction"])} 条参照路径已与旧实验逐值核对，β、λ、选集、贡献、残差等完全一致。新实验与旧产物分别存档。</p></section>')
    sections.append('<section><h2>实验具体改变了什么</h2>'+table(['步骤','原 CV','候选 CV'],[
        ['训练折预处理','仅训练折 fit 标准化与 y 均值','相同'],['训练折选列','Lasso','相同'],
        ['验证折评分','使用惩罚后的系数','选中列上 OLS 重估后评分'],['完整窗口最终拟合','Lasso 选列，再 OLS','相同'],
        ['归因计算','截至前一观测的 β × 当日已实现 X','相同']])+
        '<p>验证残差仍通过训练 y 均值处理截距，归因阶段的截距仍留在残差。本轮只检验 OLS 重估步骤，不同时调整其他目标。</p><p>这是重复使用历史样本的探索性研究。区间条件于已拟合路径，没有逐次重训或多重比较校正。三个窗口相互相关，不能当作独立重复实验。重构误差不代表提前预测收益，也不识别因果关系。</p></section>')
    sections.append('<section><h2>真实运行记录</h2><p>只读观察不增加自然运行天数。本轮没有启动或修改任务，没有生成新闻、音频或部署网站。</p><details><summary>已有状态与档案</summary><pre>'+escape(json.dumps(operations,ensure_ascii=False,indent=2))+'</pre></details></section>')
    css = '''
*{box-sizing:border-box}body{margin:0;background:#eef2f8;color:#20304b;font:16px/1.75 Outfit,"Microsoft YaHei",sans-serif}main{max-width:1220px;margin:auto;padding:32px 24px 60px}header{padding:20px 0}h1{font-size:38px;line-height:1.3}h2{font-size:25px}a{color:#245bba}section{padding:28px;background:white;border:1px solid #dce3ef;border-radius:18px;margin:24px 0}nav{display:flex;gap:24px;flex-wrap:wrap}.notice{background:#e6effc;border-left:4px solid #4277cf;padding:16px 22px}.figure-scroll,.table-scroll{max-width:100%;overflow-x:auto;margin:20px 0}svg{display:block;width:100%;min-width:850px}table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #e4eaf3}th{background:#edf2fa}td{font-variant-numeric:tabular-nums}summary{cursor:pointer;color:#245bba;padding:12px 0}pre{font:12px/1.6 "IBM Plex Mono",monospace;white-space:pre-wrap;overflow-wrap:anywhere}footer{font-size:13px;color:#59708e}@media(max-width:620px){main{padding:16px 12px}h1{font-size:29px}section{padding:20px 14px}}
'''
    plan = manifest['plan']
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FX · Lasso 验证目标研究</title><style>'+font_css()+css+'</style></head><body><main><header><p>FX ATTRIBUTION / LASSO CV OBJECTIVE</p><h1>换一种验证目标，选出来的因子会更好吗？</h1>'
    html += f'<p>{escape(plan["start"])} 至 {escape(plan["end"])} · {len(plan["pairs"])} 个货币 · 窗口 {escape(str(windows))} · baseline / expanded</p>'
    html += '<p class="notice">相同数据、相同菜单、相同最终 OLS 重估。只更改时间验证折的评分系数，保留全部对照。生产模型与网站保持不变。</p><nav><a href="#base">Baseline</a><a href="#expanded">Expanded</a><a href="#stability">稳定性</a></nav></header>'+''.join(sections)+'<footer>页面与 SVG 可离线打开。字体沿用网站 Outfit / IBM Plex Mono，中文使用系统字体。字体许可证随报告保存。</footer></main></body></html>'
    (out/'report.html').write_text(html,encoding='utf-8')
    fonts = Path(__file__).resolve().parents[1]/'web/static/fonts'
    for path in fonts.glob('OFL-*.txt'):
        (out/path.name).write_bytes(path.read_bytes())
