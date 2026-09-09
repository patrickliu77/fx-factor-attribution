"""Self-contained, offline research report and code-native summary figures."""
from __future__ import annotations

import base64
from html import escape
import json
from pathlib import Path

LABELS = {"ols_base": "OLS", "ridge_base": "Ridge", "lasso_base": "post-Lasso",
          "lasso_expanded": "post-Lasso + credit", "ols_oil_alt": "OLS / other oil",
          "ridge_oil_alt": "Ridge / other oil", "lasso_oil_alt": "post-Lasso / other oil"}
FAMILIES = {"estimator": ("01", "同菜单，比较估计器", "#2563eb"),
            "menu": ("02", "同估计器，比较候选菜单", "#0f766e"),
            "oil": ("03", "同估计器，替换油价基准", "#a16207")}


def fmt(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def font_css():
    folder = Path(__file__).resolve().parents[1] / "web/static/fonts"
    rules = []
    for family, filename in (("Outfit", "outfit-latin.woff2"), ("IBM Plex Mono", "ibm-plex-mono-latin-400.woff2")):
        path = folder / filename
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            rules.append(f"@font-face{{font-family:'{family}';src:url(data:font/woff2;base64,{data}) format('woff2');font-display:swap}}")
    return "\n".join(rules)


def difference_svg(rows, family, *, standalone=False):
    """Point estimates and 21-observation block intervals; zero is always visible."""
    _, title, color = FAMILIES[family]
    height, left, right = 90 + len(rows)*42, 350, 770
    estimates = [r["rmse_change_bp"] for r in rows]
    intervals = [next((i for i in r["intervals"] if i["block"] == 21 and i["available"]), None) for r in rows]
    bounds = [0, *estimates, *(i[k] for i in intervals if i for k in ("low_bp", "high_bp"))]
    lo, hi = min(bounds), max(bounds)
    padding = max((hi-lo)*.14, .05)
    lo, hi = lo-padding, hi+padding
    def x(v):
        return left + (v-lo)/(hi-lo)*(right-left)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 {height}" role="img" aria-labelledby="title-{family}">',
             f'<title id="title-{family}">{escape(title)}：候选减参照的 RMSE 差，单位 bp</title>',
             '<style>'+ (font_css() if standalone else '') + 'text{font-family:Outfit,"Microsoft YaHei",sans-serif;fill:#24334d;font-size:13px}</style>',
             f'<rect width="900" height="{height}" rx="16" fill="#f7f9fd"/>',
             '<text x="22" y="26">Pair / window / candidate</text>',
             '<text x="555" y="26" text-anchor="middle">Δ RMSE · candidate − reference · bp</text>']
    for j in range(5):
        v = lo+(hi-lo)*j/4
        parts.extend([f'<line x1="{x(v):.2f}" x2="{x(v):.2f}" y1="44" y2="{height-38}" stroke="#e2e8f0"/>',
                      f'<text x="{x(v):.2f}" y="{height-15}" text-anchor="middle">{v:+.2f}</text>'])
    parts.append(f'<line x1="{x(0):.2f}" x2="{x(0):.2f}" y1="42" y2="{height-37}" stroke="#64748b" stroke-width="1.5"/>')
    for j, (row, interval) in enumerate(zip(rows, intervals)):
        y = 58+j*42
        label = f'{row["pair"]} / {row["window"]} / {LABELS[row["candidate"]]}'
        parts.append(f'<text x="22" y="{y+5}">{escape(label)}</text>')
        if interval:
            a, b = x(interval["low_bp"]), x(interval["high_bp"])
            parts.extend([f'<line x1="{a:.2f}" x2="{b:.2f}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="3" opacity=".55"/>',
                          f'<path d="M {a:.2f} {y-5} v 10 M {b:.2f} {y-5} v 10" stroke="{color}"/>'])
        parts.extend([f'<circle cx="{x(row["rmse_change_bp"]):.2f}" cy="{y}" r="5" fill="{color}"/>',
                      f'<text x="870" y="{y+5}" text-anchor="end">{row["rmse_change_bp"]:+.2f}</text>'])
    return "\n".join([*parts, '</svg>'])


def table(headers, rows):
    return '<div class="table-scroll"><table><thead><tr>'+''.join(f'<th>{escape(h)}</th>' for h in headers)+\
        '</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in row)+'</tr>' for row in rows)+\
        '</tbody></table></div>'


def render_report(out, result, run, operations):
    out = Path(out)
    comparisons, summaries = result["comparisons"], result["summaries"]
    sections = []
    for family, (num, title, _) in FAMILIES.items():
        rows = [r for r in comparisons if r["family"] == family and r["period"] == "all"]
        if not rows:
            continue
        (out / f"{family}.svg").write_text(difference_svg(rows, family, standalone=True), encoding="utf-8")
        body = f'<section id="{family}"><span class="number">{num}</span><h2>{title}</h2>'
        if family == "oil":
            body += '<p>CAD：WTI → Brent。NOK：Brent → WTI。其余因子和日期固定，贡献差先将两种油价映射为 OIL。</p>'
        elif family == "menu":
            body += '<p>只扩展 post-Lasso 的候选菜单。新增 HY_EXCESS、dHY_OAS；AUD 只新增 HY_EXCESS。八列上限不变。</p>'
        else:
            body += '<p>OLS 为参照。Ridge 和 post-Lasso 使用完全相同的 baseline 因子，各自独立选择 λ。</p>'
        body += '<div class="figure-scroll">'+difference_svg(rows, family)+'</div>'
        body += '<p class="caption">点在零线左侧代表候选残差 RMSE 较低。线段为区块长度 21 的点对点 95% 区间；下表给出 5 / 21 / 63 的敏感性。正负方向只涉及重构误差。</p>'
        detailed = []
        for row in [r for r in comparisons if r["family"] == family]:
            ci = {i["block"]: f'[{i["low_bp"]:+.2f}, {i["high_bp"]:+.2f}]' if i["available"] else 'n/a' for i in row["intervals"]}
            detailed.append([row["pair"],row["window"],row["period"], LABELS[row["candidate"]], row["observations"],
                             fmt(row["rmse_change_bp"]),fmt(row["mae_change_bp"]),fmt(row["allocation_l1_bp"]),
                             *(ci.get(b, 'n/a') for b in (5,21,63))])
        body += '<details><summary>逐年结果与区块长度敏感性</summary>'+table(
            ['Pair','Window','Period','Candidate','n','Δ RMSE bp','Δ MAE bp','Contribution L1 bp','CI / 5','CI / 21','CI / 63'], detailed)+'</details></section>'
        sections.append(body)
    metrics_rows = []
    for r in summaries:
        selection = r["selection"]
        metrics_rows.append([r['pair'],r['window'],r['period'],LABELS[r['arm']],r['observations'],
                             fmt(r['mae_bp']),fmt(r['rmse_bp']),fmt(r['residual_abs_p95_bp']),fmt(r['zero_rmse_bp']),
                             fmt(r['median_beta_step_scaled_bp']),fmt(r['mean_training_r2'],3),
                             fmt(100*selection['empty_fraction']) if selection else 'n/a',
                             fmt(100*selection['switch_fraction']) if selection and selection['switch_fraction'] is not None else 'n/a'])
    sections.append('<section id="metrics"><h2>绝对误差与稳定性</h2><p>零贡献参照取残差等于当日收益。β step 为各列 |Δβ| 乘前一期训练 σ 后求和，再取中位数，单位为 bp。R² 来自训练窗口。空集率与切换率仅用于 post-Lasso。</p>'+table(
        ['Pair','Window','Period','Arm','n','MAE bp','RMSE bp','|e| p95 bp','Zero RMSE bp','β step bp','Train R²','Empty %','Switch %'],metrics_rows)+'</section>')
    audit_rows = [[p, a['training_anchor'], a['evaluation_first'],a['evaluation_last'],a['eligible_observations'],a['excluded_provisional'],
                   result['input_audits'][p]['production_join_loss'],result['input_audits'][p]['alternative_oil_join_loss']]
                  for p,a in result['audits'].items()]
    sections.append('<section id="audit"><h2>样本与复现</h2>'+table(
        ['Pair','Training anchor','First evaluated','Last evaluated','n','Provisional excluded','Full-history join loss','Oil extra loss'],audit_rows)+
        '<p>Join loss 统计截断前完整输入历史；训练和评估实际采用表内起点。每个货币使用自己的共同完整日历，不同货币的 n 可以不同。历史数据库可能经过修订，缺失筛选也会改变样本。这份快照不构成当时实时可获得数据的证明。</p>'+
        '<p>输入 parquet、逐日 β / λ / 选集 / 贡献、summary.json、CSV 和源码哈希均在同目录。重放使用 inputs/ 快照，输出到一个新目录。</p>'+
        '<details><summary>运行清单</summary><pre>'+escape(json.dumps(run,ensure_ascii=False,indent=2))+'</pre></details></section>')
    sections.append('<section id="operations"><h2>真实运行证据</h2><p>以下是只读快照，记录已有文件和日志的证据。它不能证明连续一周自然运行。缓存状态的 age_hours 反映生成时刻，不作为当前年龄使用。9 月 7 日晨间主动暂停应单独标注，不能直接判作调度失败。</p><details><summary>状态与日志清单</summary><pre>'+escape(json.dumps(operations,ensure_ascii=False,indent=2))+'</pre></details></section>')
    css = """
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eef2f8;color:#20304b;font:16px/1.75 Outfit,"Microsoft YaHei",sans-serif}main{max-width:1180px;margin:auto;padding:40px 28px 80px}header{padding:34px 0}h1{font-size:40px;line-height:1.25;margin:12px 0}h2{font-size:25px;margin:6px 0 14px}p{max-width:90ch}a{color:#245bba}nav{display:flex;flex-wrap:wrap;gap:10px 24px;margin:24px 0}nav a{text-decoration:none}section{background:white;border:1px solid #dce3ef;border-radius:20px;padding:30px;margin:24px 0;box-shadow:0 5px 20px #213d6810}.eyebrow,.number,.caption{font-size:13px;color:#59708e}.number{font-family:'IBM Plex Mono',monospace;letter-spacing:.12em}.notice{border-left:4px solid #4277cf;padding:12px 20px;background:#e7effc;border-radius:0 12px 12px 0}.table-scroll,.figure-scroll{overflow-x:auto;max-width:100%;margin:22px 0}svg{width:100%;min-width:700px;display:block}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th{background:#edf2fa;text-align:left}th,td{padding:9px 12px;border-bottom:1px solid #e5ebf3}tr:hover td{background:#f4f7fd}td{font-variant-numeric:tabular-nums}summary{cursor:pointer;color:#245bba;padding:12px 0}pre{font:12px/1.6 'IBM Plex Mono',monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;border-radius:12px;padding:18px}footer{font-size:13px;color:#59708e}@media(max-width:620px){main{padding:20px 14px}h1{font-size:30px}section{padding:20px 16px;border-radius:15px}}@media print{body{background:white}main{max-width:none;padding:0}section{box-shadow:none;break-inside:avoid}details>*{display:block}nav{display:none}svg{min-width:0}table{font-size:9px}th,td{padding:4px}}
"""
    plan = run['plan']
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FX Attribution · 离线研究</title><style>'+font_css()+css+'</style></head><body><main>'
    html += '<header><div class="eyebrow">FX ATTRIBUTION / RESEARCH NOTE / '+escape(run['created_at'])+'</div><h1>模型变化，究竟改变了什么？</h1>'
    html += f'<p>{escape(plan["start"])} 至 {escape(plan["end"])} · {len(plan["pairs"])} 个货币 · 窗口 {escape(str(plan["windows"]))} · 本地缓存重建</p>'
    html += '<div class="notice">本报告属于回顾性探索。β 只使用前一观测及更早的数据，当日贡献使用已实现的因子变化。误差衡量归因重构质量，不代表未来收益预测，也不证明因果关系。生产模型与网站均未切换。</div>'
    html += '<nav><a href="#estimator">估计器</a><a href="#menu">菜单</a><a href="#oil">油价</a><a href="#metrics">误差与稳定性</a><a href="#audit">复现</a><a href="#operations">运行记录</a></nav></header>'
    html += ''.join(sections)+f'<section><h2>区间能告诉我们什么</h2><p>同一组移动区块索引同时抽取两个模型的残差，连续区块不首尾环绕。{plan["bootstrap_repetitions"]} 次重采样给出 RMSE 差的百分位区间。区间条件于这次已拟合的路径，没有在每次抽样中重新训练或调参，也没有校正多重比较。应结合不同区块长度、年度差异及经济意义一起阅读。</p></section>'
    html += '<footer>字体沿用网站的 Outfit / IBM Plex Mono；中文由系统字体补全。字体采用 SIL Open Font License，副本随报告保存。此页面与 SVG 均可离线打开。</footer></main></body></html>'
    (out / 'report.html').write_text(html,encoding='utf-8')
    fonts = Path(__file__).resolve().parents[1] / 'web/static/fonts'
    for path in fonts.glob('OFL-*.txt'):
        (out / path.name).write_bytes(path.read_bytes())
