// SPA: #/news, #/fx, #/attribution, plus #/fx/{PAIR} detail.
// Attribution numbers all come from contract-derived values via /api; this layer
// recomputes nothing (SPEC_web §6).
//
// The "share" in News and Attribution is not a causal measure; it is an explicitly
// stated allocation rule (see the top of web/newsfeed.py). Page copy must follow
// that definition and never phrase it as "caused".
import { t, getLang, setLang } from "/i18n.js";
import * as CH from "/charts.js";
import { methodologyHtml } from "/methodology.js";

/* global echarts */

const PAIR_LABEL = {
  USDEUR: "USD/EUR", USDJPY: "USD/JPY", USDCAD: "USD/CAD",
  USDNOK: "USD/NOK", USDAUD: "USD/AUD", USDMXN: "USD/MXN",
};
const PAIR_ORDER = ["USDNOK", "USDCAD", "USDJPY", "USDAUD", "USDMXN", "USDEUR"];
const RANGES = ["1d", "5d", "1m", "6m", "ytd", "1y", "5y", "max"];
const MODEL_ORDER = ["ols", "ridge", "lasso"];

const state = {
  window: Number(localStorage.getItem("fxdash.window")) || 126,
  model: localStorage.getItem("fxdash.model") || "ols",
  meta: null,
  quotes: null,
  ranges: {},
  openPair: null,
  openStory: null,
  openExplain: null,
  openHeadline: null,
  helpOpen: false,
  opinionsOpen: false,
  pulse: null,
};
const charts = new Map();

/* ------------------------------------------------------------------ utils */
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path) {
  const res = await fetch("/api" + path, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(res.status + " " + path);
  return res.json();
}
const label = (p) => PAIR_LABEL[p] || p;
const fmtLevel = (v, d) => v == null ? "n/a"
  : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtBp = (v) => v == null ? "n/a" : (v > 0 ? "+" : "") + Math.round(v);
const fmtBp1 = (v) => v == null ? "n/a" : (v > 0 ? "+" : "") + v.toFixed(1);
const fmtPct = (v) => v == null ? "n/a" : (v > 0 ? "+" : "") + v.toFixed(2) + "%";
const fmtShare = (v) => v == null ? "n/a" : Math.round(v * 100) + "%";
const dirColor = (d) => d > 0 ? "var(--up)" : d < 0 ? "var(--down)" : "var(--mute)";
const arrow = (d) => d > 0 ? "↑" : d < 0 ? "↓" : "→";

function longDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
  });
}
const mean = (xs) => {
  const v = xs.filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

function disposeCharts() { charts.forEach((c) => c.dispose()); charts.clear(); }
function mount(el, option, key) {
  if (!el) return null;
  const c = echarts.init(el, null, { renderer: "canvas" });
  c.setOption(option);
  charts.set(key, c);
  return c;
}

/* Transmission channels are derived from the event kind; no economics is invented out of thin air */
const CHANNEL_RULES = [
  [/interven|currency operation|fx operation/i, ["FX intervention", "Not in the factor set"]],
  [/rate decision|policy|central bank|hike|cut|boj|fed|ecb/i, ["Rate differential"]],
  [/inflation|cpi|price index/i, ["Rate differential"]],
  [/oil|crude|commodity|copper|gold|metal/i, ["Terms of trade"]],
  [/risk|equity|volatility|selloff|credit/i, ["Risk appetite"]],
  [/employment|payroll|growth|gdp/i, ["Rate differential", "Risk appetite"]],
];
function channels(eventKind) {
  const hits = [];
  for (const [re, tags] of CHANNEL_RULES) {
    if (re.test(eventKind || "")) tags.forEach((x) => hits.includes(x) || hits.push(x));
  }
  return hits.length ? hits : ["Channel not classified"];
}

/* ------------------------------------------------------------------ ticker tape */
async function renderTape() {
  const tape = document.getElementById("tape");
  const session = document.getElementById("session");
  let data;
  try { data = await api("/market/ticker"); } catch (e) { data = null; }
  state.quotes = data && data.available ? data : null;

  if (data && data.available && data.session_date) {
    session.innerHTML = `<i></i>${esc(t("tape.session"))} ${
      esc(CH.shortDate(data.session_date)).toUpperCase()}`;
    session.hidden = false;
  } else {
    session.hidden = true;
  }

  // On non-trading days the whole tape is hidden (user request)
  if (!data || !data.available || !data.trading_day || !data.items.length) {
    tape.hidden = true;
    return;
  }
  const items = data.items.map((q) => `<span class="tape__item" title="${
    esc(q.label)} close ${esc(q.date)}">
      <span class="tape__name">${esc(q.label)}</span>
      <span class="tape__px">${fmtLevel(q.last, q.digits)}</span>
      <span class="tape__chg" style="color:${dirColor(q.direction)}">${
        fmtPct(q.chg_pct)} ${arrow(q.direction)}</span></span>`).join("");
  const lane = `<span class="tape__lane">${items}</span>`;
  tape.innerHTML = `<div class="tape__rail">${lane}${
    lane.replace('class="tape__lane"', 'class="tape__lane" aria-hidden="true"')}</div>`;
  tape.style.setProperty("--tape-dur", Math.max(40, data.items.length * 5.5) + "s");
  tape.hidden = false;
}

/* ------------------------------------------------------------------ pulse */
// Always in the header. The narrative layer dying silently for three days with no
// visible sign on the page is the most dangerous failure mode of an unattended system.
// The age is recomputed by the server **at read time**, not taken from the stale
// write-time value in the output file.
// Color is judged on last_run only (did it run). last_published (was a commentary
// written) is decided by the market; five quiet days are a normal state, and judging
// color on it would mean alarming every day.
async function renderPulse() {
  const el = document.getElementById("pulse");
  let s;
  try { s = await api("/narrative/status"); } catch (e) { s = null; }
  if (!s) {
    el.dataset.state = "red";
    el.innerHTML = `<i></i>${esc(t("pulse.label"))} ${esc(t("pulse.unreachable"))}`;
    el.title = t("pulse.unreachable");
    el.hidden = false;
    state.pulse = null;
    return;
  }
  state.pulse = s;
  el.dataset.state = s.state;
  el.innerHTML = `<i></i>${esc(t("pulse.label"))} ${esc(pulseAge(s))}`;
  el.title = (s.reasons || []).join("; ") || t("pulse.ok");
  el.hidden = false;
}

function pulseAge(s) {
  if (s.age_hours == null) return t("pulse.never");
  const h = s.age_hours;
  return h < 1 ? `${Math.round(h * 60)}m` : h < 48 ? `${Math.round(h)}h`
    : `${Math.round(h / 24)}d`;
}

function healthPanel() {
  const s = state.pulse;
  if (!s) return "";
  return `<div class="health">
    <div class="health__row"><i style="background:var(--${
      s.state === "green" ? "up" : s.state === "yellow" ? "res" : "down"})"></i>
      ${esc(t("pulse.label"))}
      <b>${esc(s.last_run ? s.last_run.replace("T", " ") : t("pulse.never"))}</b>
      <span>${esc(t("pulse.age"))} ${esc(pulseAge(s))}</span>
      <span>${esc(t("pulse.thresholds", { warn: s.warn_hours, crit: s.crit_hours }))}</span>
      <span>${s.days_on_record} ${esc(t("pulse.days"))}</span>
    </div>
    <div class="health__row health__row--quiet">
      ${esc(t("pulse.published"))}
      <b>${esc(s.last_published ? s.last_published.slice(0, 10) : t("pulse.nonepub"))}</b>
      <span>${esc(t("pulse.pubnote"))}</span>
    </div>
    ${(s.reasons || []).length
      ? `<div class="health__row">${s.reasons.map(esc).join("; ")}</div>` : ""}
  </div>`;
}

/* ------------------------------------------------------------------ theme */
const getTheme = () => {
  try { return localStorage.getItem("fxdash.theme") || "dark"; } catch (e) { return "dark"; }
};
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("fxdash.theme", theme); } catch (e) { /* private mode */ }
}
function renderThemeButton() {
  const btn = document.getElementById("themebtn");
  const dark = getTheme() !== "light";
  btn.textContent = dark ? "☀" : "☾";
  btn.title = dark ? t("theme.light") : t("theme.dark");
  btn.setAttribute("aria-label", btn.title);
  btn.onclick = () => { applyTheme(dark ? "light" : "dark"); renderThemeButton(); renderTape(); render(); };
}

/* ------------------------------------------------------------------ top bar */
function renderNav(route) {
  const nav = document.getElementById("nav");
  const links = [["#/news", t("nav.news")], ["#/fx", t("nav.fx")],
    ["#/attribution", t("nav.attribution")]];
  nav.innerHTML = links.map(([href, text]) => {
    const active = route.startsWith(href.slice(1));
    return `<a href="${href}"${active ? ' aria-current="page"' : ""}>${esc(text)}</a>`;
  }).join("");

  const seg = document.getElementById("langseg");
  const lang = getLang();
  seg.innerHTML = [["en", "EN"], ["zh", "中"]].map(([v, n]) =>
    `<button type="button" data-lang="${v}" aria-pressed="${v === lang}">${n}</button>`).join("");
  seg.querySelectorAll("button").forEach((b) => {
    b.onclick = () => { setLang(b.dataset.lang); renderThemeButton(); render(); };
  });
  renderThemeButton();
}

function controls() {
  const meta = state.meta || {};
  const models = (meta.models || MODEL_ORDER).slice()
    .sort((a, b) => MODEL_ORDER.indexOf(a) - MODEL_ORDER.indexOf(b));
  const windows = meta.windows || [63, 126, 252];
  const seg = (name, opts, current) => `<div class="ctl" data-ctl="${name}">` +
    opts.map((v) => `<button type="button" data-v="${v}" aria-pressed="${
      String(v) === String(current)}">${esc(String(v).toUpperCase())}</button>`).join("") + "</div>";
  return `<div class="ctlrow">
    <span class="lbl">${esc(t("ctl.model"))}</span>${seg("model", models, state.model)}
    <span class="lbl">${esc(t("ctl.window"))}</span>${seg("window", windows, state.window)}
  </div>`;
}

function bindControls(root) {
  root.querySelectorAll("[data-ctl]").forEach((box) => {
    const name = box.dataset.ctl;
    box.querySelectorAll("button").forEach((b) => {
      b.onclick = (ev) => {
        ev.stopPropagation();
        const v = b.dataset.v;
        if (name === "model") { state.model = v; localStorage.setItem("fxdash.model", v); }
        else { state.window = Number(v); localStorage.setItem("fxdash.window", v); }
        render();
      };
    });
  });
}

/* ------------------------------------------------------------------ news blocks */
function metaLine(item, dir) {
  const bits = [];
  if (item.published) bits.push(`<span>${esc(item.published)}</span>`);
  if (item.source) bits.push(`<span>${esc(item.source)}</span>`);
  if (dir) bits.push(`<span style="color:${
    dir.includes("up") ? "var(--up)" : dir.includes("down") ? "var(--down)" : "var(--mute)"
  }">${esc(dir.toUpperCase())}</span>`);
  return `<div class="metaline">${bits.join("")}</div>`;
}

// Explain appears only on items that a commentary has cited (2026-09-02 ruling):
// a button that gives every item the same sentence on most days should not be
// shown permanently. Its content is the relevant passage of the commentary that
// cited it, plus that day's figures, not a restatement of internal system state.
function hasExplain(ctx) {
  return !!(ctx && (ctx.event_kind || (ctx.why_unexplained || {}).en));
}

function explainBlock(ctx, heading) {
  if (!hasExplain(ctx)) return "";
  const lang = getLang();
  const prose = (ctx.why_unexplained || {})[lang]
    || (ctx.why_unexplained || {}).en || "";
  const day = [
    ctx.date,
    ctx.residual_bp == null ? "" : `${t("res")} ${fmtBp1(ctx.residual_bp)} bp`,
    ctx.residual_z == null ? "" : `z ${ctx.residual_z.toFixed(2)}`,
  ].filter(Boolean).join("  ");
  const tags = channels(ctx.event_kind || "")
    .map((c) => `<span class="chip">${esc(c)}</span>`).join("");
  const notes = [ctx.coverage_check || "", ctx.continuity_check || ""].filter(Boolean);
  return `<div class="explain">
    <div class="explain__k">${esc(heading)}</div>
    <div class="explain__day">${esc(day)}</div>
    ${prose ? `<div class="explain__t">${esc(prose)}</div>` : ""}
    ${notes.length ? `<div class="explain__t dim">${esc(notes.join(" "))}</div>` : ""}
    <div class="chips">${tags}</div></div>`;
}

function actions(item, key, openKey, ctx) {
  const open = openKey === key;
  return `<div class="actions">
      <a class="btn" href="${esc(item.url)}" target="_blank" rel="noopener">${
        esc(t("news.readfull"))} ↗</a>
      ${hasExplain(ctx) ? `<button class="btn" type="button" data-explain="${
        esc(key)}" aria-pressed="${open}">${esc(t("news.explain"))}</button>` : ""}
    </div>`;
}

/* ------------------------------------------------------------------ News */
// Expand and collapse always mutate the DOM **in place**, never a full render():
// a full repaint flashes Loading first, then disposes and rebuilds every chart
// (the user explicitly rejected that behaviour). A full render is reserved for
// route, language, theme and model/window changes, where the data really changed.
function bindExplainButtons(scope, htmlOf) {
  scope.querySelectorAll("[data-explain]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const k = b.dataset.explain;
      const host = b.closest(".expand") || b.closest(".pairnews__item");
      if (!host) return;
      const existing = host.querySelector(".explain");
      const wasOpen = state.openExplain === k;
      if (existing) existing.remove();
      state.openExplain = null;
      host.querySelectorAll("[data-explain]").forEach((x) =>
        x.setAttribute("aria-pressed", "false"));
      if (!wasOpen) {
        state.openExplain = k;
        b.setAttribute("aria-pressed", "true");
        host.insertAdjacentHTML("beforeend", htmlOf(k));
      }
    };
  });
}

// The evidence line keeps only the date and the pair (2026-09-02 ruling): a
// residual printed beside each story reads as if that story contributed that many
// bp on its own, which uses layout to re-imply the causality that check 4 forbids
// in the prose. The day's residual is shown once on the flagged-day heading row
// and once in the sidebar list.
function evidenceLine(e) {
  return t("news.evline", { pair: label(e.pair), date: e.date });
}

function storyExpandHtml(s, key) {
  return `<div class="expand">
    <div class="expand__top">
      <div class="expand__text">
        ${metaLine(s, (s.latest && s.latest.y_bp != null)
          ? (s.latest.y_bp > 0 ? "usd up" : "usd down") : null)}
        <div class="summary">${esc(s.summary || t("news.nosummary"))}</div>
        ${(s.evidence || []).map((e) =>
          `<div class="hint">${esc(evidenceLine(e))}</div>`).join("")}
        ${(s.duplicates || []).length ? `<div class="hint">${esc(t("news.alsoby"))}: ${
          esc(s.duplicates.map((d) => d.source || d.title).join(", "))}</div>` : ""}
      </div>
      ${actions(s, key, state.openExplain, (s.context || {})[(s.pairs || [])[0]])}
    </div>
    ${state.openExplain === key
      ? explainBlock((s.context || {})[(s.pairs || [])[0]], t("explain.head"))
      : ""}
  </div>`;
}

function storyRowHtml(s, key, ordinal) {
  const open = state.openStory === key;
  const tags = (s.pairs || []).map((p) =>
    `<span class="ptag">${esc(label(p))}</span>`).join("");
  const lat = s.latest || {};
  return `<div class="story">
    <button class="storyrow" type="button" data-story="${esc(key)}" aria-expanded="${open}">
      <div class="rank">${ordinal}</div>
      <div class="body">
        <div class="title">${esc(s.title)}</div>
      </div>
      <div class="pairtags">${tags}</div>
      <div class="share">
        <div class="mono nowrap">${esc(lat.date || "")}</div>
        ${s.cited ? `<div class="citedtag">${esc(t("news.cited"))}</div>` : ""}
      </div>
    </button>
    ${open ? storyExpandHtml(s, key) : ""}
  </div>`;
}

function headlineExpandHtml(h, key) {
  return `<div class="expand" style="padding-left:112px">
    <div class="expand__top">
      <div class="expand__text">
        ${metaLine(h, h.direction)}
        <div class="summary">${esc(h.summary || t("news.nosummary"))}</div>
      </div>
      ${actions(h, key, state.openExplain, null)}
    </div>
  </div>`;
}

async function pageNews(view) {
  const news = await api("/news");
  const pairs = (state.meta.pairs || []).slice()
    .sort((a, b) => PAIR_ORDER.indexOf(a) - PAIR_ORDER.indexOf(b));
  const stories = news.week.items;
  const fb = news.fallback;
  const fbStories = fb ? fb.items : [];

  // Exception rule (2026-09-02 ruling 4): a source cited by that day's commentary
  // is always kept whatever its kind, because it is part of the commentary's
  // evidence chain; only uncited opinion pieces go into the fold (by construction
  // the current list is entirely cited).
  const keepInline = (st) => st.cited || st.kind !== "opinion";
  // Flagged-day list for the sidebar and the heading row: each pair-day appears
  // once, and these are the only two slots where the day's residual is shown
  // (heading row + sidebar); it no longer appears on the list rows.
  const flaggedRows = [];
  const seenFlag = new Set();
  [...stories, ...fbStories].forEach((st) => (st.evidence || []).forEach((e) => {
    const k = e.date + e.pair;
    if (!seenFlag.has(k)) { seenFlag.add(k); flaggedRows.push(e); }
  }));
  flaggedRows.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  const flaggedOpinions = [...stories, ...fbStories].filter((st) => !keepInline(st));

  const byKey = new Map();
  stories.forEach((s) => byKey.set("s:" + s.url, s));
  fbStories.forEach((s) => byKey.set("f:" + s.url, s));

  // With no story inside the week window, fall back to the most recent flagged
  // day, but **always with its date**, never passing it off as this week
  const inlineStories = stories.filter(keepInline);
  const storyRows = inlineStories.length
    ? inlineStories.map((s, i) => storyRowHtml(s, "s:" + s.url, i + 1)).join("")
    : `<p class="empty">${esc(t("news.emptyweek"))}</p>` + (fb ? `
      <div class="col gap14" style="margin-top:16px">
        <div class="between">
          <h2 class="sec">${esc(t("news.fallback.title", { date: fb.date }))}</h2>
          <div class="hint nowrap">${esc(flaggedRows
            .filter((e) => e.date === fb.date)
            .map((e) => t("news.dayres", {
              pair: label(e.pair), bp: fmtBp1(e.residual_bp),
              z: e.residual_z == null ? "n/a" : e.residual_z.toFixed(2),
            })).join("; "))}</div>
        </div>
        <p class="stack-note">${esc(t("news.fallback.note"))}</p>
        <div class="col">${fbStories.filter(keepInline).map((s, i) =>
          storyRowHtml(s, "f:" + s.url, i + 1)).join("")}</div>
      </div>` : "");

  const heads = news.today.items;
  const earlier = (news.earlier && news.earlier.items) || [];
  const opinions = [
    ...(((news.opinions || {}).items) || []),
    ...flaggedOpinions.map((st) => ({
      url: st.url, title: st.title, source: st.source,
      published: (st.latest || {}).date || st.published, pairs: st.pairs,
    })),
  ];
  const isLive = news.today.mode === "live";
  const headlineRow = (h, key) => {
    const open = state.openHeadline === key;
    const chips = (h.pairs || []).map((p) =>
      `<span class="ptag">${esc(label(p))}</span>`).join("");
    return `<div class="hline">
      <button class="hrow" type="button" data-headline="${esc(key)}" aria-expanded="${open}">
        <div class="when">${esc(h.published || "")}</div>
        <div class="t">${esc(h.title)}${chips ? ` <span class="hchips">${chips}</span>` : ""}</div>
        <div class="src">${esc(h.source || "")}</div>
      </button>
      ${open ? headlineExpandHtml(h, key) : ""}
    </div>`;
  };
  // The empty state distinguishes the cause: the feeds could not be read; they
  // were read but nothing carries today's date; or nothing was fetched at all
  const emptyMsg = (news.today.errors || []).length ? t("news.feedfail")
    : isLive ? t("news.notodayyet") : t("news.empty");
  const headRows = heads.length
    ? heads.map((h, i) => headlineRow(h, "h:" + i)).join("")
    : `<p class="empty">${esc(emptyMsg)}</p>`;
  const earlierRows = earlier.map((h, i) => headlineRow(h, "e:" + i)).join("");
  const opinionRows = opinions.map((h, i) => headlineRow(h, "o:" + i)).join("");

  const minis = pairs.map((p) => `<button class="mini" type="button" data-mini="${p}">
      <div class="mini__head">
        <div class="mini__name">${esc(label(p))}</div>
        <div class="mini__chg" data-chg="${p}"></div>
      </div>
      <div class="mini__px" data-px="${p}"></div>
      <div class="mini__spark" data-spark="${p}"></div>
    </button>`).join("");

  const todayHint = [news.today.date, `${heads.length} ${t("news.items")}`]
    .filter(Boolean).join(", ");
  const liveChip = isLive ? `<span class="tag tag--live">${esc(t("news.live"))}</span>` : "";

  view.innerHTML = `
    <div class="headrow"><div class="dateline">${esc(longDate(news.as_of))}</div></div>
    <div class="news">
      <section class="news__main">
        <div class="col gap20">
          <div>
            <h1 class="page">${esc(t("news.week.title"))}</h1>
            <p class="lede">${esc(t("news.week.blurb"))}</p>
            ${news.week_start ? `<div class="hint">${esc(t("news.week.window",
              { start: news.week_start, end: news.week_end }))}</div>` : ""}
          </div>
          <div class="col">
            <div class="storyhead"><div>#</div><div>${esc(t("news.col.story"))}</div>
              <div>${esc(t("news.col.pairs"))}</div><div class="r">${esc(t("news.col.flagged"))}</div></div>
            ${storyRows}
          </div>
          ${earlier.length ? `<div class="col gap14" style="margin-top:16px">
            <div class="between"><h2 class="sec">${esc(t("news.earlier"))} ${liveChip}</h2>
              <div class="hint">${esc(news.earlier.start)}, ${earlier.length} ${esc(t("news.items"))}</div></div>
            <div class="col">${earlierRows}</div>
            <p class="stack-note">${esc(t("news.earliernote"))}</p>
          </div>` : ""}
        </div>
        <div class="col gap14">
          <div class="between"><h2 class="sec">${esc(t("news.today.title"))} ${liveChip}</h2>
            <div class="hint">${esc(todayHint)}</div></div>
          <div class="col">${headRows}</div>
          ${isLive ? `<p class="stack-note">${esc(t("news.livenote"))}</p>` : ""}
          ${opinions.length ? `
          <button class="opfold" type="button" id="opfold" aria-expanded="${state.opinionsOpen}">
            <i>${state.opinionsOpen ? "\u25be" : "\u25b8"}</i>
            ${esc(t("news.opinions"))} (${opinions.length})
          </button>
          <div class="col" id="opbody" ${state.opinionsOpen ? "" : "hidden"}>
            ${opinionRows}
            <p class="stack-note">${esc(t("news.opinionsnote"))}</p>
          </div>` : ""}
        </div>
        ${healthPanel()}
      </section>
      <aside class="news__side">
        <div class="col gap14">
          <h3 class="side">${esc(t("news.side.pairs"))}</h3>
          <div class="minigrid">${minis}</div>
        </div>
        <div class="col gap14">
          <h3 class="side">${esc(t("news.side.flagged"))}</h3>
          ${flaggedRows.length ? flaggedRows.slice(0, 6).map((e) => `
            <div class="between hint"><span>${esc(e.date)} ${esc(label(e.pair))}</span>
              <span style="color:${(e.residual_bp || 0) > 0 ? "var(--up)" : "var(--down)"}">${
                fmtBp1(e.residual_bp)} bp</span></div>`).join("")
            : `<p class="empty">${esc(t("news.side.noflag"))}</p>`}
          <p class="stack-note">${esc(t("news.side.note"))}</p>
        </div>
      </aside>
    </div>`;

  const explainHtmlFor = (k) => {
    const s = byKey.get(k);                    // headlines have no Explain button; a miss is empty
    if (!s) return "";
    return explainBlock((s.context || {})[(s.pairs || [])[0]], t("explain.head"));
  };

  view.querySelectorAll("[data-story]").forEach((b) => {
    b.onclick = () => {
      const key = b.dataset.story;
      const wasOpen = state.openStory === key;
      view.querySelectorAll(".story > .expand").forEach((n) => n.remove());
      view.querySelectorAll("[data-story]").forEach((x) =>
        x.setAttribute("aria-expanded", "false"));
      state.openStory = null;
      state.openExplain = null;
      if (!wasOpen) {
        state.openStory = key;
        b.setAttribute("aria-expanded", "true");
        b.insertAdjacentHTML("afterend", storyExpandHtml(byKey.get(key), key));
        bindExplainButtons(b.parentElement, explainHtmlFor);
      }
    };
  });
  const headlineByKey = (key) =>
    (key[0] === "o" ? opinions : key[0] === "e" ? earlier : heads)[Number(key.slice(2))];
  const fold = view.querySelector("#opfold");
  if (fold) {
    fold.onclick = () => {
      state.opinionsOpen = !state.opinionsOpen;
      const body = view.querySelector("#opbody");
      body.hidden = !state.opinionsOpen;
      fold.setAttribute("aria-expanded", String(state.opinionsOpen));
      fold.querySelector("i").textContent = state.opinionsOpen ? "\u25be" : "\u25b8";
    };
  }
  view.querySelectorAll("[data-headline]").forEach((b) => {
    b.onclick = () => {
      const key = b.dataset.headline;
      const wasOpen = state.openHeadline === key;
      view.querySelectorAll(".hline > .expand").forEach((n) => n.remove());
      view.querySelectorAll("[data-headline]").forEach((x) =>
        x.setAttribute("aria-expanded", "false"));
      state.openHeadline = null;
      state.openExplain = null;
      if (!wasOpen) {
        state.openHeadline = key;
        b.setAttribute("aria-expanded", "true");
        b.insertAdjacentHTML("afterend", headlineExpandHtml(headlineByKey(key), key));
        bindExplainButtons(b.parentElement, explainHtmlFor);
      }
    };
  });
  // After a full repaint (language or theme switch) that arrives with a block
  // already expanded, Explain has to be rebound too
  bindExplainButtons(view, explainHtmlFor);
  view.querySelectorAll("[data-mini]").forEach((b) => {
    b.onclick = () => { location.hash = "#/fx"; };
  });

  pairs.forEach(async (p) => {
    const q = (state.quotes && state.quotes.items.find((x) => x.pair === p)) || null;
    const chg = view.querySelector(`[data-chg="${p}"]`);
    const px = view.querySelector(`[data-px="${p}"]`);
    if (q) {
      chg.textContent = fmtPct(q.chg_pct);
      chg.style.color = dirColor(q.direction);
      px.textContent = fmtLevel(q.last, q.digits);
    }
    try {
      const data = await api(`/market/series/${p}?range=5d`);
      if (data.available) mount(view.querySelector(`[data-spark="${p}"]`),
        CH.sparkOption(data), "spark:" + p);
    } catch (e) { /* no sparkline: leave it blank, main column unaffected */ }
  });
}

/* ------------------------------------------------------------------ FX */
function composeRead(daily) {
  const movers = (daily.facts && daily.facts.movers) || [];
  if (!movers.length) return t("read.unavailable");
  const top = movers[0];
  const parts = [
    t("read.lead", { pair: label(top.pair), move: fmtBp(top.y == null ? null : top.y * 1e4) + " bp" }),
    t("read.split", {
      sys: fmtShare(mean(movers.map((m) => m.shares.systematic))),
      exo: fmtShare(mean(movers.map((m) => m.shares.exogenous))),
      res: fmtShare(mean(movers.map((m) => m.shares.residual))),
    }),
  ];
  const widest = movers.slice().sort((a, b) =>
    Math.abs(b.residual_z || 0) - Math.abs(a.residual_z || 0))[0];
  parts.push(widest && Math.abs(widest.residual_z || 0) >= 1.5
    ? t("read.residual", { pair: label(widest.pair), z: widest.residual_z.toFixed(2) })
    : t("read.quiet"));
  return parts.join(" ");
}

// Robustness chips: all three agree / Ridge diverges / Lasso reselects / Lasso
// abstains. When Ridge and Lasso cross the line at the same time both chips are
// shown side by side, never merged (2026-09-03 ruling 5).
// A chip is not an alert and does not feed the status colour; with too little
// data nothing is rendered, rather than faking agreement.
function robustnessChips(st) {
  if (!st || !st.available) return "";
  const num = (v) => (v == null ? "n/a" : v.toFixed(2));
  const tip = t("robust.tip", { r: num(st.d_ridge_n1), l: num(st.d_lasso_n1) });
  if (st.agree) {
    return `<span class="rchip rchip--ok" title="${esc(tip)}">${esc(t("robust.agree"))}</span>`;
  }
  return (st.states || []).map((k) => {
    if (k === "ridge_diverge") {
      return `<span class="rchip rchip--ridge" title="${esc(tip)}">${esc(t("robust.ridge"))}</span>`;
    }
    if (k === "lasso_reselect") {
      return `<span class="rchip rchip--lasso" title="${esc(tip)}">${esc(t("robust.lasso"))}</span>`;
    }
    const run = st.abstain_run_days > 1 ? ` ${st.abstain_run_days}d` : "";
    return `<span class="rchip rchip--abstain" title="${esc(tip)}">${esc(t("robust.abstain"))}${esc(run)}</span>`;
  }).join("");
}

function meterOf(row) {
  const s = Math.abs(row?.systematic || 0), e = Math.abs(row?.exogenous || 0),
    r = Math.abs(row?.residual || 0);
  const total = s + e + r;
  return total ? [s / total, e / total, r / total] : [null, null, null];
}

function cardHtml(pair, row, counts, robust) {
  const q = (state.quotes && state.quotes.items.find((x) => x.pair === pair)) || null;
  const [ms, me, mr] = meterOf(row);
  const open = state.openPair === pair;
  const n = counts[pair] || 0;
  const range = state.ranges[pair] || "6m";
  return `<div class="card" role="button" tabindex="0" data-card="${pair}"
    aria-expanded="${open}">
    <div class="card__head">
      <div class="card__name">${esc(label(pair))}</div>
      ${robustnessChips(robust)}
      ${row && row.provisional ? `<span class="tag">${esc(t("quote.provisional"))}</span>` : ""}
      <div class="card__news">${n ? `${n} ${esc(t("fx.stories"))}` : esc(t("fx.nostories"))}</div>
    </div>
    <div class="card__px">
      <div class="card__last">${q ? fmtLevel(q.last, q.digits) : "n/a"}</div>
      <div class="card__chg" style="color:${dirColor(q ? q.direction : 0)}">${
        q ? fmtBp(q.chg_bp) + " bp " + fmtPct(q.chg_pct) : ""}</div>
      <div class="card__arrow" style="color:${dirColor(q ? q.direction : 0)}">${
        q ? arrow(q.direction) : ""}</div>
    </div>
    <div class="col gap12">
      <div class="meter">
        <i style="width:${(ms || 0) * 100}%;background:var(--sys)"></i>
        <i style="width:${(me || 0) * 100}%;background:var(--exo)"></i>
        <i style="flex:1;background:var(--res)"></i>
      </div>
      <div class="split">
        <div><i style="background:var(--sys)"></i>${esc(t("sys")).toUpperCase()} <b>${fmtShare(ms)}</b></div>
        <div><i style="background:var(--exo)"></i>${esc(t("exo")).toUpperCase()} <b>${fmtShare(me)}</b></div>
        <div><i style="background:var(--res)"></i>${esc(t("res")).toUpperCase()} <b>${fmtShare(mr)}</b></div>
      </div>
      <div class="hint nowrap">r2 ${row && row.r2_full != null ? row.r2_full.toFixed(3) : "n/a"}
        ${esc(t("fx.onmodel"))} ${esc(((state.meta && state.meta.default_model) || "ols").toUpperCase())} ${
          (state.meta && state.meta.default_window) || 126}</div>
    </div>
    <div class="plot">
      <div class="plot__main">
        <div class="plot__box" data-chart="${pair}"></div>
        <div class="plot__x" data-xaxis="${pair}"></div>
      </div>
      <div class="plot__axis" data-yaxis="${pair}"></div>
    </div>
    <div class="ranges" data-ranges="${pair}">${RANGES.map((r) =>
      `<button type="button" data-r="${r}" aria-pressed="${r === range}">${
        esc(t("range." + r))}</button>`).join("")}</div>
  </div>`;
}

function pairNewsHtml(pair, feed) {
  const items = feed.items || [];
  const body = items.length ? items.map((n, i) => {
    const key = `p:${pair}:${i}`;
    return `<div class="pairnews__item">
      <div class="expand__top">
        <div class="expand__text">
          ${metaLine(n, n.direction)}
          <div class="title" style="font-size:17px;font-weight:500">${esc(n.title)}</div>
          <div class="summary">${esc(n.summary || t("news.nosummary"))}</div>
          ${(n.evidence || []).map((e) =>
            `<div class="hint">${esc(evidenceLine(e))}</div>`).join("")}
        </div>
        ${actions(n, key, state.openExplain, n.context)}
      </div>
      ${state.openExplain === key
        ? explainBlock(n.context, t("explain.headpair", { pair: label(pair) }))
        : ""}
    </div>`;
  }).join("") : `<p class="empty">${esc(t("fx.nonewsforpair"))}</p>`;
  const headlines = feed.headlines || [];
  const todayBlock = headlines.length ? `
    <div class="pairnews__today">
      <div class="between"><h3 class="side">${esc(t("news.recent"))}</h3>
        <span class="tag tag--live">${esc(t("news.live"))}</span></div>
      ${headlines.map((h) => `<a class="headline" href="${esc(h.url)}"
          target="_blank" rel="noopener">
        <span class="when">${esc(h.published || "")}</span>
        <span class="t">${esc(h.title)}</span>
        <span class="src">${esc(h.source || "")} ↗</span></a>`).join("")}
    </div>` : "";
  return `<div class="pairnews">
    <div class="pairnews__head">
      <h2 class="sec">${esc(t("fx.newsmoving", { pair: label(pair) }))}</h2>
      <div class="hint">${items.length} ${esc(t("fx.storiesweek"))}</div>
    </div>
    <div class="col">${body}</div>
    ${todayBlock}
  </div>`;
}

function helpGridHtml() {
  return `<div class="helpgrid">
    <div style="border-top:2px solid var(--sys)">
      <div class="k" style="color:var(--sys)">${esc(t("sys")).toUpperCase()}</div>
      <div class="t">${esc(t("legend.sys"))}</div></div>
    <div style="border-top:2px solid var(--exo)">
      <div class="k" style="color:var(--exo)">${esc(t("exo")).toUpperCase()}</div>
      <div class="t">${esc(t("legend.exo"))}</div></div>
    <div style="border-top:2px solid var(--res)">
      <div class="k" style="color:var(--res)">${esc(t("res")).toUpperCase()}</div>
      <div class="t">${esc(t("legend.res"))}</div></div>
    <div style="border-top:2px solid var(--b-rates)">
      <div class="k" style="color:var(--b-rates)">${esc(t("ctl.window"))} 63 / 126 / 252</div>
      <div class="t">${esc(t("legend.window"))}</div></div>
    <div style="border-top:2px solid var(--b-risk)">
      <div class="k" style="color:var(--b-risk)">${esc(t("ctl.model"))} OLS / RIDGE / LASSO</div>
      <div class="t">${esc(t("legend.model"))}</div></div>
  </div>`;
}

async function pageFX(view) {
  // The FX page is pinned to the canonical basis OLS@126 (2026-09-02 user ruling:
  // the switches stay on Attribution only)
  const win = (state.meta && state.meta.default_window) || 126;
  const model = (state.meta && state.meta.default_model) || "ols";
  const qs = `?window=${win}&model=${model}`;
  const [overview, daily, attribution] = await Promise.all([
    api("/overview" + qs), api("/narrative/daily" + qs), api("/attribution/weekly" + qs),
  ]);
  const counts = attribution.story_counts || {};
  const byPair = {};
  overview.pairs.forEach((p) => { byPair[p.pair] = p; });
  const pairs = PAIR_ORDER.filter((p) => byPair[p]).length
    ? PAIR_ORDER.filter((p) => byPair[p]) : overview.pairs.map((p) => p.pair);

  let feed = null;
  if (state.openPair) {
    try { feed = await api(`/pairs/${state.openPair}/news`); }
    catch (e) { feed = { items: [] }; }
  }

  const robust = overview.robustness || {};
  const grid = [];
  pairs.forEach((p, i) => {
    grid.push(cardHtml(p, byPair[p], counts, robust[p]));
    const endOfRow = i % 2 === 1 || i === pairs.length - 1;
    const rowHasOpen = state.openPair &&
      (pairs[i] === state.openPair || (i % 2 === 1 && pairs[i - 1] === state.openPair));
    if (endOfRow && rowHasOpen && feed) grid.push(pairNewsHtml(state.openPair, feed));
  });

  view.innerHTML = `
    <div class="headrow">
      <div class="dateline">${esc(longDate(overview.as_of))}</div>
    </div>
    <div class="col gap32">
      <div class="read">
        <div class="read__head">
          <div class="read__title">${esc(t("read.title"))}</div>
          <span class="tag">${esc(t("read.pending"))}</span>
        </div>
        <p class="read__body">${esc(composeRead(daily))}</p>
        <div class="read__note">${esc(t("read.note"))}</div>
      </div>
      <div class="cards">${grid.join("")}</div>
      <div class="helpwrap">
        <button class="helpbtn" type="button" id="helpbtn" aria-pressed="${state.helpOpen}">
          <i>?</i>${esc(t("legend.open"))}</button>
        ${state.helpOpen ? helpGridHtml() : ""}
      </div>
    </div>`;

  // Expand, legend and Explain all add and remove DOM in place, never a full
  // render(): a full repaint flashes Loading and disposes and rebuilds all six
  // price charts (the user explicitly rejected that behaviour)
  let currentFeed = feed;
  function bindPairNewsExplain() {
    const panel = view.querySelector(".pairnews");
    if (!panel) return;
    bindExplainButtons(panel, (k) => {
      const n = ((currentFeed || {}).items || [])[Number(k.split(":")[2])];
      return explainBlock(n && n.context,
        t("explain.headpair", { pair: label(state.openPair) }));
    });
  }
  async function openPairPanel(pair) {
    view.querySelectorAll(".pairnews").forEach((n) => n.remove());
    view.querySelectorAll("[data-card]").forEach((c) =>
      c.setAttribute("aria-expanded", String(!!pair && c.dataset.card === pair)));
    if (!pair) return;
    let f;
    try { f = await api(`/pairs/${pair}/news`); } catch (e) { f = { items: [] }; }
    if (state.openPair !== pair) return; // clicked elsewhere meanwhile; drop it
    currentFeed = f;
    const i = pairs.indexOf(pair);
    const anchorPair = (i % 2 === 0 && i + 1 < pairs.length) ? pairs[i + 1] : pairs[i];
    const anchor = view.querySelector(`[data-card="${anchorPair}"]`);
    if (!anchor) return;
    anchor.insertAdjacentHTML("afterend", pairNewsHtml(pair, f));
    bindPairNewsExplain();
  }

  view.querySelector("#helpbtn").onclick = () => {
    state.helpOpen = !state.helpOpen;
    const btn = view.querySelector("#helpbtn");
    btn.setAttribute("aria-pressed", String(state.helpOpen));
    const grid = view.querySelector(".helpgrid");
    if (grid) grid.remove();
    if (state.helpOpen) btn.insertAdjacentHTML("afterend", helpGridHtml());
  };
  const toggleCard = (c, ev) => {
    // The plot area belongs to the chart's own interactions; a click on the chart
    // is not a toggle (flashing the panel away on a chart click is too abrupt)
    if (ev.target.closest("[data-ranges]") || ev.target.closest(".actions")
      || ev.target.closest(".plot")) return;
    const p = c.dataset.card;
    state.openPair = state.openPair === p ? null : p;
    state.openExplain = null;
    openPairPanel(state.openPair);
  };
  view.querySelectorAll("[data-card]").forEach((c) => {
    c.onclick = (ev) => toggleCard(c, ev);
    c.onkeydown = (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggleCard(c, ev); }
    };
  });
  view.querySelectorAll("[data-ranges]").forEach((box) => {
    const pair = box.dataset.ranges;
    box.querySelectorAll("button").forEach((b) => {
      b.onclick = (ev) => {
        ev.stopPropagation();
        state.ranges[pair] = b.dataset.r;
        box.querySelectorAll("button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
        drawPrice(pair);
      };
    });
  });
  // After a full repaint that arrives with a panel already expanded (model,
  // window or language switch), Explain has to be rebound too
  bindPairNewsExplain();
  pairs.forEach(drawPrice);
}

async function drawPrice(pair) {
  const box = document.querySelector(`[data-chart="${pair}"]`);
  const yaxis = document.querySelector(`[data-yaxis="${pair}"]`);
  const xaxis = document.querySelector(`[data-xaxis="${pair}"]`);
  if (!box) return;
  const range = state.ranges[pair] || "6m";
  let data;
  try { data = await api(`/market/series/${pair}?range=${range}`); }
  catch (e) { data = { available: false, reason: "no_cache" }; }

  const old = charts.get("px:" + pair);
  if (old) { old.dispose(); charts.delete("px:" + pair); }
  box.innerHTML = "";
  if (yaxis) yaxis.innerHTML = "";
  if (xaxis) xaxis.innerHTML = "";

  if (!data.available) {
    const msg = data.reason === "intraday_pending" ? t("chart.intraday")
      : data.reason === "too_short" ? t("chart.short") : t("chart.nocache");
    box.innerHTML = `<p class="plot__msg">${esc(msg)}</p>`;
    return;
  }
  // Ticks and the plot's min/max come from the same function, so they align exactly
  const scale = CH.priceScale(data.values);
  if (yaxis) yaxis.innerHTML = scale.ticks.map((v) => `<span>${esc(v)}</span>`).join("");
  if (xaxis) xaxis.innerHTML = CH.xLabels(data.dates).map((v) => `<span>${esc(v)}</span>`).join("");
  mount(box, CH.priceOption(data, scale), "px:" + pair);
}

/* ------------------------------------------------------------------ Attribution */
function divergingRow(row, order, half) {
  const segs = order.map((k) => ({
    key: k,
    value: k === "residual" ? row.residual_bp : (row.buckets || {})[k] || 0,
  })).filter((s) => s.value);
  let neg = 0, pos = 0;
  const html = segs.map((s) => {
    const w = (Math.abs(s.value) / (half * 2)) * 100;
    let left;
    if (s.value < 0) { neg += Math.abs(s.value); left = 50 - (neg / (half * 2)) * 100; }
    else { left = 50 + (pos / (half * 2)) * 100; pos += s.value; }
    return `<i style="left:${left}%;width:${w}%;background:var(--b-${s.key})"
      title="${esc(s.key)} ${fmtBp1(s.value)} bp"></i>`;
  }).join("");
  return `<div class="diverge"><div class="diverge__zero"></div>${html}</div>`;
}

async function pageAttribution(view) {
  const qs = `?window=${state.window}&model=${state.model}`;
  const data = await api("/attribution/weekly" + qs);
  const order = data.bucket_order;
  const rows = data.pairs.slice()
    .sort((a, b) => PAIR_ORDER.indexOf(a.pair) - PAIR_ORDER.indexOf(b.pair));

  let half = 1;
  rows.forEach((r) => {
    let neg = 0, pos = 0;
    order.forEach((k) => {
      const v = k === "residual" ? r.residual_bp : (r.buckets || {})[k] || 0;
      if (v < 0) neg += -v; else pos += v;
    });
    half = Math.max(half, neg, pos);
  });
  half = Math.ceil(half / 20) * 20;

  const legend = order.map((k) =>
    `<div><i style="background:var(--b-${k})"></i>${esc(data.bucket_labels[k])}</div>`).join("");

  const robust = data.robustness || {};
  const body = rows.map((r) => `<div class="attrrow">
      <div class="name">${esc(label(r.pair))}</div>
      <div class="num r" style="color:${dirColor(r.y_bp)}">${fmtBp1(r.y_bp)}</div>
      ${divergingRow(r, order, half)}
      <div class="num r">${fmtBp1(r.residual_bp)}</div>
      <div class="rcell">${robustnessChips(robust[r.pair]) || '<span class="hint">n/a</span>'}</div>
    </div>`).join("");

  const m = data.matrix;
  // Column order matches the rest of the site, not the backend's alphabetical order
  const colOrder = PAIR_ORDER.filter((p) => m.pairs.includes(p));
  const colIndex = colOrder.map((p) => m.pairs.indexOf(p));
  // The residual is a property of the trading day, not of a single story
  // (2026-09-02 ruling): the cells carry only the cited mark, and the residual is
  // shown once per "date + pair" on the group's residual row
  const gridCols = `grid-template-columns:minmax(240px,1.2fr) repeat(${
    colOrder.length}, minmax(0,1fr))`;
  const groups = m.groups || [];
  const matrixHtml = groups.length ? groups.map((g) => `
    <div class="matgroup">
      <div class="matgroup__head">${esc(g.date)}</div>
      <div class="matrix" style="${gridCols}">
        <div></div>
        ${colOrder.map((p) => `<div class="colhead">${esc(label(p))}</div>`).join("")}
        <div class="rowhead dim">${esc(t("attr.dayresidual"))}</div>
        ${colOrder.map((p) => {
          const r = (g.residuals || {})[p];
          if (!r) return '<div class="cell"></div>';
          return `<div class="cell cell--res" title="${esc(
            t("attr.restip", { date: g.date, pair: label(p) }))}">${
            fmtBp1(r.residual_bp)} bp<span>z ${
            r.residual_z == null ? "n/a" : r.residual_z.toFixed(2)}</span></div>`;
        }).join("")}
        ${g.rows.map((row) => `<div class="rowhead" title="${esc(row.title)}">${
          esc(row.title)}</div>` +
          colIndex.map((idx) => {
            const c = row.cells[idx];
            return c && c.cited
              ? '<div class="cell cell--hit">\u2713</div>'
              : '<div class="cell"></div>';
          }).join("")).join("")}
      </div>
    </div>`).join("") : `<p class="empty">${esc(t("attr.nomatrix"))}</p>`;

  view.innerHTML = `
    <div class="headrow">
      <div class="dateline">${esc(t("attr.week", {
        start: rows[0] ? rows[0].start : "", end: rows[0] ? rows[0].end : "" }))}</div>
      <div class="ctlrow">
        ${controls()}
        <a class="iconbtn" href="#/methodology" title="${esc(t("attr.methodology"))}"
          aria-label="${esc(t("attr.methodology"))}">ⓘ</a>
      </div>
    </div>
    <div class="col gap40">
      <div>
        <h1 class="page">${esc(t("attr.title"))}</h1>
        <p class="lede">${esc(t("attr.blurb"))}</p>
      </div>
      <div class="legend">${legend}</div>
      <div class="col">
        <div class="attrhead"><div>${esc(t("attr.pair"))}</div>
          <div class="r">${esc(t("attr.move"))}</div>
          <div>${esc(t("attr.decomp"))}</div>
          <div class="r">${esc(t("res"))}</div>
          <div>${esc(t("attr.robust"))}</div></div>
        ${body}
        <div class="attrscale"><div></div><div></div>
          <div class="axis"><span>${-half} bp</span><span>0</span><span>+${half} bp</span></div>
          <div></div><div></div></div>
      </div>
      <div class="col gap16">
        <div class="between">
          <h2 class="sec">${esc(t("attr.matrix"))}</h2>
          <div class="hint">${esc(m.note)}</div>
        </div>
        ${matrixHtml}
      </div>
    </div>`;
  bindControls(view);
}

/* ------------------------------------------------------------- Methodology */
let mathjaxLoading = null;
function ensureMathJax() {
  if (window.MathJax && window.MathJax.typesetPromise) return Promise.resolve();
  if (!mathjaxLoading) {
    window.MathJax = {
      tex: { inlineMath: [["\\(", "\\)"]], displayMath: [["\\[", "\\]"]] },
      svg: { fontCache: "global" },
      startup: { typeset: false },
    };
    mathjaxLoading = new Promise((resolve, reject) => {
      const sc = document.createElement("script");
      sc.src = "/vendor/tex-svg.js";
      sc.onload = resolve;
      sc.onerror = reject;
      document.head.appendChild(sc);
    });
  }
  return mathjaxLoading;
}

async function pageMethodology(view) {
  view.innerHTML = methodologyHtml();
  try {
    await ensureMathJax();
    await window.MathJax.startup.promise;
    await window.MathJax.typesetPromise([view]);
  } catch (e) {
    // If MathJax fails, keep the raw TeX text: readable, just not pretty. A
    // rendering failure must never blank the page.
  }
}

/* ------------------------------------------------------------------ router */
async function render() {
  const view = document.getElementById("view");
  const route = (location.hash || "#/fx").slice(1);
  disposeCharts();
  renderNav(route);
  view.innerHTML = `<p class="empty">${esc(t("loading"))}</p>`;
  try {
    if (!state.meta) state.meta = await api("/meta");
    if (state.meta.windows && !state.meta.windows.includes(state.window)) {
      state.window = state.meta.default_window || state.meta.windows[0];
    }
    if (state.meta.models && !state.meta.models.includes(state.model)) {
      state.model = state.meta.default_model || state.meta.models[0];
    }
    if (route.startsWith("/news")) await pageNews(view);
    else if (route.startsWith("/attribution")) await pageAttribution(view);
    else if (route.startsWith("/methodology")) await pageMethodology(view);
    else await pageFX(view);
  } catch (err) {
    view.innerHTML = `<p class="empty">${esc(t("error"))} <code>${esc(err.message)}</code></p>`;
  }
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => charts.forEach((c) => c.resize()), 140);
});
window.addEventListener("hashchange", render);

(async function start() {
  setLang(getLang());
  applyTheme(getTheme());
  await renderTape();
  await renderPulse();
  await render();
  setInterval(() => { renderTape(); renderPulse(); }, 5 * 60 * 1000);
})();
