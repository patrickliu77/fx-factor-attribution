"""Plot styling.

Figure titles and captions are Chinese; matplotlib's default DejaVu Sans has
no CJK glyphs, so without a font setup they render as a row of boxes. Pick a
CJK font by availability; if none is found, fall back to English labels —
never leave boxes.
"""

from __future__ import annotations

import base64
import io

CJK_CANDIDATES = (
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "SimSun",
)

_STATE: dict[str, object] = {}


def setup_matplotlib() -> bool:
    """Return whether a CJK font was enabled. Repeated calls take effect once."""
    if "cjk" in _STATE:
        return bool(_STATE["cjk"])

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    available = {f.name for f in fm.fontManager.ttflist}
    chosen = [name for name in CJK_CANDIDATES if name in available]
    if chosen:
        plt.rcParams["font.sans-serif"] = chosen + ["DejaVu Sans"]
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False  # CJK fonts lack U+2212; minus would box
    plt.rcParams["figure.autolayout"] = False
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.25
    plt.rcParams["grid.linewidth"] = 0.5

    _STATE["cjk"] = bool(chosen)
    return bool(chosen)


def has_cjk() -> bool:
    return bool(_STATE.get("cjk", False))


def label(chinese: str, english: str) -> str:
    """Fall back to English when no CJK font is available; no boxes on figures."""
    return chinese if has_cjk() else english


# Tokens shared by the report suite. Overview and per-pair pages use the same
# set; colors always go through tokens. Never define a color for the first time
# inside an @media or [data-theme] block, or the unstamped default theme
# cannot see it.
THEME = """
:root{
  --ground:#f6f7f9; --surface:#ffffff; --surface-2:#eef1f5;
  --ink:#141a22; --ink-muted:#5f6b7a; --line:#e2e6ec;
  --accent:#3d6d9e;        /* systematic, the suite's existing blue */
  --warm:#e07b28;          /* exogenous */
  --neutral:#9aa5b1;       /* residual */
  --ok:#1a7f37; --warn:#b7791f; --crit:#b00020;
  --ok-bg:#e8f4ec; --warn-bg:#fbf1de; --crit-bg:#fbeaec;
}
:root:not([data-theme="light"]){
  @media (prefers-color-scheme: dark){
    --ground:#12151a; --surface:#1a1f27; --surface-2:#222833;
    --ink:#e8ecf2; --ink-muted:#94a3b4; --line:#2a323d;
    --accent:#6fa3d6; --warm:#f0a05a; --neutral:#7c8896;
    --ok:#5cc98a; --warn:#e0b357; --crit:#f08a99;
    --ok-bg:#16281d; --warn-bg:#2b2416; --crit-bg:#2c171b;
  }
}
:root[data-theme="dark"]{
  --ground:#12151a; --surface:#1a1f27; --surface-2:#222833;
  --ink:#e8ecf2; --ink-muted:#94a3b4; --line:#2a323d;
  --accent:#6fa3d6; --warm:#f0a05a; --neutral:#7c8896;
  --ok:#5cc98a; --warn:#e0b357; --crit:#f08a99;
  --ok-bg:#16281d; --warn-bg:#2b2416; --crit-bg:#2c171b;
}
*{box-sizing:border-box}
body{
  margin:0; padding:26px clamp(16px,4vw,44px);
  background:var(--ground); color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC",-apple-system,"Segoe UI",sans-serif;
  line-height:1.6; font-size:14px;
}
.num{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;
     font-variant-numeric:tabular-nums}
a{color:var(--accent)}
a:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
table{border-collapse:collapse; font-size:13px; width:100%}
th,td{border:1px solid var(--line); padding:5px 10px; text-align:right}
th{background:var(--surface-2); font-weight:600}
td.l,th.l{text-align:left}
.scroll{overflow-x:auto}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def figure_to_base64(fig) -> str:
    """Figure to base64 for inlining; report pages are self-contained (SPEC 7)."""
    import matplotlib.pyplot as plt

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
