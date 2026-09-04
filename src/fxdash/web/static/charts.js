// ECharts option factories. Colours are read from CSS custom properties at render
// time, so the light and dark themes share one code path.
// The price chart's Y-axis ticks are **not drawn by ECharts**: the axis labels live
// in HTML (.plot__axis), and the plot's min/max and those ticks are computed by the
// same function, so the two align exactly. The design requires that, and it was the
// root cause of the earlier misalignment (ECharts picked its own scale and the HTML
// side had no way of knowing what it had picked).
/* global echarts */

const MONO = "IBM Plex Mono, ui-monospace, Consolas, monospace";

export function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n, f) => (cs.getPropertyValue(n) || "").trim() || f;
  return {
    ink: v("--ink", "#0b0e1a"),
    panel: v("--panel", "#12152a"),
    raise: v("--raise", "#171b32"),
    text: v("--text", "#e6e8f2"),
    mute: v("--mute", "#8a8fa8"),
    dim: v("--dim", "#5c6079"),
    accent: v("--accent", "#a394ff"),
    sys: v("--sys", "#8b6cff"),
    exo: v("--exo", "#3b9eff"),
    res: v("--res", "#c8f542"),
    up: v("--up", "#4cd39a"),
    down: v("--down", "#f0647a"),
    grid: v("--chart-grid", "rgba(255,255,255,.06)"),
    axis: v("--chart-axis", "#5c6079"),
    line: v("--line-3", "rgba(255,255,255,.14)"),
  };
}

export function bucketColor(key) {
  const cs = getComputedStyle(document.documentElement);
  return (cs.getPropertyValue("--b-" + key) || "").trim() || "#8a8fa8";
}

function alpha(color, a) {
  if (!color.startsWith("#")) return color;
  let hex = color.slice(1);
  if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
  const n = parseInt(hex, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function shortDate(iso) {
  if (!iso) return "";
  const [, m, d] = String(iso).split("-");
  return `${MONTHS[+m - 1]} ${+d}`;
}

/* Price axis: work out a decent min/max and the ticks. The HTML and the plot share
   this one result. */
export function priceScale(values, count = 5) {
  const clean = values.filter((v) => v != null && isFinite(v));
  if (!clean.length) return { min: 0, max: 1, ticks: ["0", "1"], digits: 2 };
  let lo = Math.min(...clean);
  let hi = Math.max(...clean);
  if (hi === lo) { hi = lo + Math.abs(lo || 1) * 0.001; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const span = hi - lo;
  const digits = span >= 50 ? 0 : span >= 5 ? 1 : span >= 0.5 ? 2 : span >= 0.05 ? 3 : 4;
  const ticks = [];
  for (let i = count - 1; i >= 0; i--) {
    ticks.push((lo + (span * i) / (count - 1)).toFixed(digits));
  }
  return { min: lo, max: hi, ticks, digits };
}

/* The x-axis labels also live in HTML; this only supplies the candidate dates */
export function xLabels(dates, count = 6) {
  if (!dates.length) return [];
  if (dates.length <= count) return dates.map(shortDate);
  const out = [];
  for (let i = 0; i < count; i++) {
    out.push(shortDate(dates[Math.round((i * (dates.length - 1)) / (count - 1))]));
  }
  return out;
}

/* ------------------------------------------------------------------ price chart */
export function priceOption(data, scale) {
  const C = tokens();
  const color = data.direction >= 0 ? C.up : C.down;
  const digits = scale.digits;
  return {
    animation: false,
    // The plot fills the container: the HTML-side ticks divide the container height
    // evenly, which is what makes the two align exactly
    grid: { left: 0, right: 0, top: 0, bottom: 0, containLabel: false },
    tooltip: {
      trigger: "axis",
      backgroundColor: C.raise,
      borderColor: C.line,
      borderWidth: 1,
      padding: [8, 11],
      textStyle: { color: C.text, fontSize: 12, fontFamily: MONO },
      formatter: (ps) => {
        const p = ps[0];
        const val = p.data == null ? "n/a" : Number(p.data).toFixed(digits + 1);
        return `<span style="color:${C.mute}">${p.axisValue}</span>  <b>${val}</b>`;
      },
    },
    xAxis: {
      type: "category", data: data.dates, boundaryGap: false,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { show: false }, splitLine: { show: false },
      axisPointer: { lineStyle: { color: C.mute, width: 1, type: "dashed" } },
    },
    yAxis: {
      type: "value", min: scale.min, max: scale.max,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { show: false },
      splitNumber: scale.ticks.length - 1,
      splitLine: { lineStyle: { color: C.grid } },
    },
    series: [{
      type: "line", data: data.values, showSymbol: false, smooth: false,
      lineStyle: { color, width: 1.8 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: alpha(color, 0.26) },
          { offset: 1, color: alpha(color, 0) },
        ]),
      },
      markLine: {
        silent: true, symbol: "none",
        data: [{ yAxis: data.first }],
        lineStyle: { color: C.dim, type: "dashed", width: 1 },
        label: { show: false },
      },
    }],
  };
}

/* ------------------------------------------------------------------ sparkline */
export function sparkOption(data) {
  const C = tokens();
  const color = data.direction >= 0 ? C.up : C.down;
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 6, bottom: 0, containLabel: false },
    xAxis: { type: "category", data: data.dates, show: false, boundaryGap: false },
    yAxis: { type: "value", scale: true, show: false },
    series: [{
      type: "line", data: data.values, showSymbol: false, smooth: false,
      lineStyle: { color, width: 1.5 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: alpha(color, 0.22) },
          { offset: 1, color: alpha(color, 0) },
        ]),
      },
    }],
  };
}
