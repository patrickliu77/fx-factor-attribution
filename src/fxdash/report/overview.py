"""Daily overview page outputs/reports/index.html (SPEC_phase2 section 5).

The first page anyone looks at each day, and the mount point for the Phase 3
narrative layer. Reads only contract and status; recomputes nothing.

Sections are ordered to answer the most pressing question first: is the system
healthy -> how much did each pair move yesterday and what explains it -> which
pair's residual is abnormal -> what alerts exist -> how fresh is the data.
"""

from __future__ import annotations

import html
import json

import pandas as pd

from ..config import (
    DEFAULT_WINDOW,
    HEARTBEAT_CRIT_HOURS,
    HEARTBEAT_WARN_HOURS,
    PAIRS,
    REPORT_DIR,
)
from ..data.base import record
from ..heartbeat import humanise
from .style import THEME

STATE_LABEL = {"green": "绿 · 正常", "yellow": "黄 · 有告警", "red": "红 · 失败"}
STATE_TOKEN = {"green": "ok", "yellow": "warn", "red": "crit"}

PAGE_CSS = """
.head{display:flex; flex-wrap:wrap; align-items:baseline; gap:10px 18px; margin-bottom:4px}
h1{font-size:20px; margin:0; letter-spacing:.02em; font-weight:700}
.sub{color:var(--ink-muted); font-size:13px; margin:0 0 22px}
h2{font-size:13px; margin:28px 0 10px; text-transform:uppercase;
   letter-spacing:.09em; color:var(--ink-muted); font-weight:700}
.pill{display:inline-flex; align-items:center; gap:7px; padding:5px 13px;
      border-radius:999px; font-weight:700; font-size:13px; letter-spacing:.02em}
.pill::before{content:""; width:8px; height:8px; border-radius:50%; background:currentColor}
.pill.ok{background:var(--ok-bg); color:var(--ok)}
.pill.warn{background:var(--warn-bg); color:var(--warn)}
.pill.crit{background:var(--crit-bg); color:var(--crit)}
.band{display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
      gap:1px; background:var(--line); border:1px solid var(--line);
      border-radius:8px; overflow:hidden; margin-bottom:6px}
.band > div{background:var(--surface); padding:11px 14px}
.band dt{font-size:11px; color:var(--ink-muted); letter-spacing:.06em;
         text-transform:uppercase; margin:0 0 3px}
.band dd{margin:0; font-size:17px; font-weight:600}
.grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(272px,1fr)); gap:12px}
.card{background:var(--surface); border:1px solid var(--line); border-radius:8px;
      padding:13px 15px}
.card header{display:flex; justify-content:space-between; align-items:baseline; gap:10px}
.card .pair{font-weight:700; font-size:15px; letter-spacing:.03em}
.card .ret{font-size:15px; font-weight:600}
.bar{display:flex; height:9px; border-radius:5px; overflow:hidden;
     background:var(--surface-2); margin:11px 0 8px}
.bar span{display:block}
.legend{display:flex; flex-wrap:wrap; gap:4px 14px; font-size:11.5px;
        color:var(--ink-muted)}
.legend b{font-weight:600; color:var(--ink)}
.swatch{display:inline-block; width:8px; height:8px; border-radius:2px;
        margin-right:5px; vertical-align:baseline}
.signs{margin-top:5px; font-size:11px; color:var(--ink-muted); letter-spacing:.04em}
.meta{display:flex; flex-wrap:wrap; gap:5px 12px; margin-top:9px; font-size:11.5px;
      color:var(--ink-muted); border-top:1px solid var(--line); padding-top:8px}
.chip{display:inline-block; padding:1px 7px; border-radius:4px; font-size:11px;
      font-weight:600}
.chip.ok{background:var(--ok-bg); color:var(--ok)}
.chip.warn{background:var(--warn-bg); color:var(--warn)}
.chip.crit{background:var(--crit-bg); color:var(--crit)}
.pulse{display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 16px;
       border:1px solid var(--line); border-left:3px solid var(--ok);
       border-radius:8px; background:var(--surface); padding:10px 14px;
       margin-bottom:12px; font-size:13px}
.pulse.warn{border-left-color:var(--warn)}
.pulse.crit{border-left-color:var(--crit)}
.pulse .k{color:var(--ink-muted); font-size:11px; letter-spacing:.06em;
          text-transform:uppercase}
.two{display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px}
.panel{background:var(--surface); border:1px solid var(--line); border-radius:8px;
       padding:13px 15px}
.rank{list-style:none; margin:0; padding:0}
.rank li{display:flex; align-items:center; gap:10px; padding:5px 0;
         border-bottom:1px solid var(--line)}
.rank li:last-child{border-bottom:0}
.rank .name{width:74px; font-weight:600; font-size:13px}
.rank .track{flex:1; height:7px; background:var(--surface-2); border-radius:4px;
             position:relative; overflow:hidden}
.rank .fill{position:absolute; top:0; bottom:0; border-radius:4px}
.rank .val{width:54px; text-align:right; font-size:12.5px}
.empty{color:var(--ink-muted); font-size:13px; padding:6px 0}
.notes{margin:8px 0 0; padding-left:18px; font-size:12.5px; color:var(--ink-muted)}
.links{display:flex; flex-wrap:wrap; gap:8px; margin-top:10px}
.links a{display:inline-block; padding:7px 14px; border:1px solid var(--line);
         border-radius:6px; background:var(--surface); text-decoration:none;
         font-weight:600; font-size:13px}
.foot{margin-top:28px; padding-top:12px; border-top:1px solid var(--line);
      color:var(--ink-muted); font-size:11.5px}
"""


FRESHNESS_JS = """
<script>
// Self-check of page freshness. status.json and this page are written only by
// a **successful** run, so once a run fails the page freezes on its last green
// -- exactly the scenario the heartbeat guards against, yet cannot guard for
// itself. So the check lives in the page too: even if the pipeline stops
// running entirely, opening this page recolors it on its own.
(function () {
  var root = document.getElementById("fresh");
  if (!root) return;
  var gen = Date.parse(root.getAttribute("data-generated"));
  if (isNaN(gen)) return;
  var warn = parseFloat(root.getAttribute("data-warn"));
  var crit = parseFloat(root.getAttribute("data-crit"));
  var hours = (Date.now() - gen) / 3600000;
  if (hours <= warn) return;

  var level = hours > crit ? "crit" : "warn";
  var pill = document.getElementById("statusPill");
  if (pill) {
    pill.className = "pill " + level;
    pill.textContent = (level === "crit" ? "红 · 疑似调度停摆" : "黄 · 疑似调度停摆");
  }
  var age = hours < 48
    ? hours.toFixed(1) + " 小时"
    : (hours / 24).toFixed(1) + " 天";
  var banner = document.createElement("div");
  banner.className = "pulse " + level;
  banner.innerHTML =
    '<span><span class="k">本页自查</span> <b>本页已生成 ' + age +
    '，超过 ' + warn + ' 小时未更新</b></span>' +
    '<span>下面的数字是那一刻的快照，不代表此刻的系统状态。' +
    '先确认调度任务是否还在运行。</span>';
  root.parentNode.insertBefore(banner, root);
})();
</script>
"""


def _esc(value) -> str:
    return html.escape(str(value))


def _bp(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value) * 1e4:+.1f}"


def _sign(value) -> str:
    if value is None or pd.isna(value) or float(value) == 0:
        return "0"
    return "+" if float(value) > 0 else "−"


def _z_chip(z) -> str:
    if z is None or pd.isna(z):
        return '<span class="chip">z —</span>'
    magnitude = abs(float(z))
    token = "crit" if magnitude >= 3 else "warn" if magnitude >= 2 else "ok"
    return f'<span class="chip {token}">z {float(z):+.2f}</span>'


def _latest_rows(contract: pd.DataFrame) -> pd.DataFrame:
    """Take each pair's **own** latest row, not the row at the global max date.

    The six pairs have different holiday calendars and different foreign-leg
    release rhythms, so the last trading day is naturally ragged. Slicing at
    the global max date would show "no record today" for most pairs -- both
    inaccurate and useless on most days.
    """
    block = contract[
        (contract["window"] == DEFAULT_WINDOW) & (contract["model"] == "ols")
    ]
    if block.empty:
        return block
    newest = block.sort_values("date").groupby("pair", observed=True).tail(1)
    return newest.set_index("pair")


def _pair_card(pair: str, row: pd.Series | None, newest=None) -> str:
    if row is None:
        return (
            f'<article class="card"><header><span class="pair">{_esc(pair)}</span>'
            f'</header><p class="empty">尚无记录</p></article>'
        )

    # Flag a pair whose latest day lags the global newest, so readers do not
    # assume every card shows the same day.
    lag_note = ""
    row_date = pd.Timestamp(row["date"])
    if newest is not None and row_date < pd.Timestamp(newest):
        lag_note = f'<span class="chip warn">{row_date.date()}</span>'

    parts = [
        ("systematic", abs(float(row["systematic"] or 0)), "var(--accent)", "系统性"),
        ("exogenous", abs(float(row["exogenous"] or 0)), "var(--warm)", "外生"),
        ("residual", abs(float(row["residual"] or 0)), "var(--neutral)", "残差"),
    ]
    total = sum(value for _, value, _, _ in parts) or 1.0
    segments = "".join(
        f'<span style="width:{value / total:.4%}; background:{color}"></span>'
        for _, value, color, _ in parts
    )
    legend = "".join(
        f'<span><i class="swatch" style="background:{color}"></i>{name} '
        f'<b class="num">{_bp(row[key])}</b></span>'
        for key, _, color, name in parts
    )
    # Bars are drawn by absolute-value share, so signs are lost there; but the
    # sign pattern of the three buckets is diagnostic in itself -- all same
    # sign, mutual cancellation, and residual opposing are three very
    # different days -- so it gets a line of its own.
    signs = " ".join(
        f'{name}{_sign(row[key])}' for key, _, _, name in parts
    )
    stale = json.loads(row["stale_flags"] or "[]")
    flags = []
    if bool(row.get("provisional", False)):
        flags.append('<span class="chip warn">provisional</span>')
    if stale:
        flags.append(f'<span class="chip warn">stale {_esc(len(stale))}</span>')

    return f"""<article class="card">
  <header>
    <span class="pair">{_esc(pair)}</span>
    <span class="ret num">{_bp(row["y"])} bp</span>
  </header>
  <div class="bar">{segments}</div>
  <div class="legend num">{legend}</div>
  <div class="signs">方向 {signs}</div>
  <div class="meta">
    <span>{_z_chip(row["residual_z"])}</span>
    <span>r²_full <b class="num">{float(row["r2_full"]):.3f}</b></span>
    <span>r²_exog <b class="num">{float(row["r2_exog"]):.3f}</b></span>
    {lag_note}{"".join(flags)}
  </div>
</article>"""


def _residual_rank(latest: pd.DataFrame) -> str:
    if latest.empty or "residual_z" not in latest:
        return '<p class="empty">无数据</p>'
    ranked = (
        latest["residual_z"].dropna().abs().sort_values(ascending=False)
    )
    if ranked.empty:
        return '<p class="empty">残差 z 尚未就绪</p>'
    ceiling = max(float(ranked.iloc[0]), 3.0)
    items = []
    for pair, magnitude in ranked.items():
        signed = float(latest.loc[pair, "residual_z"])
        token = "crit" if magnitude >= 3 else "warn" if magnitude >= 2 else "ok"
        items.append(
            f'<li><span class="name">{_esc(pair)}</span>'
            f'<span class="track"><span class="fill" style="width:'
            f'{magnitude / ceiling:.2%}; background:var(--{token})"></span></span>'
            f'<span class="val num">{signed:+.2f}</span></li>'
        )
    return f'<ul class="rank">{"".join(items)}</ul>'


def _warnings(status: dict) -> str:
    findings = status.get("health_findings") or []
    overdue = status.get("overdue_provisional") or []
    if not findings and not overdue:
        return '<p class="empty">当前无告警。历史统计见 run_manifest.json。</p>'
    items = [
        f'<li><b>{_esc(f["check"])}</b> · {_esc(f.get("pair", ""))}'
        f'{" · " + _esc(f["state"]) if f.get("state") else ""}<br>{_esc(f["action"])}</li>'
        for f in findings
    ]
    items += [
        f'<li><b>provisional 超龄</b> · {_esc(o["pair"])}<br>'
        f'最早未回填 {_esc(o["oldest_provisional"])}，已 {_esc(o["age_days"])} 天'
        f'（上限 {_esc(o["limit_days"])}）。{_esc(o["note"])}</li>'
        for o in overdue
    ]
    return f'<ul class="notes">{"".join(items)}</ul>'


def _freshness(status: dict, manifest: dict, latest: pd.DataFrame) -> str:
    coverage = manifest.get("coverage") or {}
    as_of = status.get("source_as_of") or {}
    rows = []
    for pair in PAIRS:
        span = coverage.get(pair, {})
        provisional = (
            "是"
            if pair in latest.index and bool(latest.loc[pair].get("provisional", False))
            else "否"
        )
        lagged = ", ".join(
            f"{name.split('.')[-1]} {value}"
            for name, value in as_of.items()
            if name.startswith(pair)
        )
        rows.append(
            f'<tr><td class="l">{_esc(pair)}</td>'
            f'<td class="num">{_esc(span.get("first", "—"))}</td>'
            f'<td class="num">{_esc(span.get("last", "—"))}</td>'
            f'<td class="num">{_esc(span.get("rows", "—"))}</td>'
            f"<td>{provisional}</td><td class=\"l\">{_esc(lagged or '—')}</td></tr>"
        )
    return f"""<div class="scroll"><table>
<tr><th class="l">pair</th><th>面板起</th><th>面板止</th><th>行数</th>
<th>当日 provisional</th><th class="l">发布滞后源 as of</th></tr>
{"".join(rows)}</table></div>"""


def build_overview(contract: pd.DataFrame, status: dict, manifest: dict) -> str:
    latest = _latest_rows(contract)
    state = status.get("state", "green")
    token = STATE_TOKEN.get(state, "ok")
    as_of_date = status.get("contract_last_date") or "—"

    newest = latest["date"].max() if not latest.empty else None
    cards = "".join(
        _pair_card(
            pair, latest.loc[pair] if pair in latest.index else None, newest=newest
        )
        for pair in PAIRS
    )
    merge = manifest.get("merge", {})
    overwrites = len(manifest.get("provisional_overwrites") or [])
    mode = status.get("mode", "")

    # The frozen-kept cell shows only in live mode: a backfill with
    # --rewrite-history recomputes everything, so the count is 0 -- yet the
    # field exists to prove history was NOT rewritten, so a 0 after a backfill
    # would be read backwards.
    frozen_cell = (
        f'<div><dt>冻结保留</dt><dd class="num">'
        f'{int(merge.get("frozen_kept", 0) or 0):,}</dd></div>'
        if mode == "live"
        else ""
    )

    pulse = status.get("heartbeat") or {}
    pulse_token = STATE_TOKEN.get(pulse.get("state", "green"), "ok")
    # Attach generation time and thresholds to the DOM for the page's own
    # freshness self-check script.
    pulse_html = (
        f'<div class="pulse {pulse_token}" id="fresh"'
        f' data-generated="{_esc(status.get("generated_at", ""))}"'
        f' data-warn="{pulse.get("warn_hours", HEARTBEAT_WARN_HOURS)}"'
        f' data-crit="{pulse.get("crit_hours", HEARTBEAT_CRIT_HOURS)}">'
        f'<span><span class="k">最近一次成功 live</span> '
        f'<b class="num">{_esc(pulse.get("last_live_success") or "—")}</b></span>'
        f'<span><span class="k">距今</span> '
        f'<b class="num">{_esc(humanise(pulse.get("age_hours")))}</b></span>'
        f'<span><span class="k">调度</span> {_esc(pulse.get("note", ""))}</span>'
        f"</div>"
    )

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX 归因每日概览</title>
<style>{THEME}{PAGE_CSS}</style></head>
<body>
<div class="head">
  <h1>FX 因子归因 · 每日概览</h1>
  <span class="pill {token}" id="statusPill">{_esc(STATE_LABEL.get(state, state))}</span>
</div>
<p class="sub">截至 {_esc(as_of_date)} · {_esc(mode)} 模式 ·
窗口 {DEFAULT_WINDOW} 天 · 模型 OLS · 生成于
<span class="num">{_esc(status.get("generated_at", ""))[:19]}</span> ·
contract schema <span class="num">{_esc(status.get("schema_version", "—"))}</span></p>

{pulse_html}

<dl class="band">
  <div><dt>contract 行数</dt><dd class="num">{int(status.get("rows", 0) or 0):,}</dd></div>
  <div><dt>provisional 行</dt><dd class="num">{int(status.get("provisional_rows", 0) or 0):,}</dd></div>
  <div><dt>本次覆盖</dt><dd class="num">{overwrites:,}</dd></div>
  {frozen_cell}
  <div><dt>schema</dt><dd class="num">{_esc(status.get("schema_version", "—"))}</dd></div>
</dl>

<h2>昨日三栏分解</h2>
<div class="grid">{cards}</div>
<p class="notes">systematic 是 DOLLAR_LOO 与 CARRY_LOO 之和，量的是这个 pair 的变动里
有多大比例是系统性的；exogenous 才是外生经济因子的解释力。两者分栏报告，不可混为一谈。
条形按三者绝对值的占比绘制，数字为带符号的实际贡献。</p>

<div class="two" style="margin-top:20px">
  <section class="panel">
    <h2 style="margin-top:0">残差 z 排行</h2>
    {_residual_rank(latest)}
    <p class="notes">残差放大说明发生了模型之外的 pair specific 事件，
    这是 Phase 3 叙事层的触发信号。</p>
  </section>
  <section class="panel">
    <h2 style="margin-top:0">告警</h2>
    {_warnings(status)}
    <p class="notes">颜色只由当前状态决定，历史统计只进 manifest
    （SPEC_phase2 2.5）。</p>
  </section>
</div>

<h2>数据新鲜度与覆盖</h2>
{_freshness(status, manifest, latest)}

<h2>逐 pair 报告</h2>
<nav class="links">
{"".join(f'<a href="{p}.html">{p}</a>' for p in PAIRS)}
</nav>

<p class="foot">下游只读 outputs/contract/ 与 outputs/status.json，这一契约不变。
本页静态生成，只读 contract 与 status，不重算任何数字。</p>
{FRESHNESS_JS}
</body></html>"""


def write_overview(contract: pd.DataFrame, status: dict, manifest: dict) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "index.html"
    path.write_text(build_overview(contract, status, manifest), encoding="utf-8")
    record("overview_written", path=str(path))
    return str(path)
