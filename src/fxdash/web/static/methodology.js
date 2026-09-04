// Methodology page: the formal statement of the mathematics used across the site
// (SPEC_web §2.8, 2026-09-02 user ruling).
//
// Discipline:
// 1. Describe only what the code actually does; every number can be traced to a
//    place in src/, and the figures correspond to the prose.
// 2. Formulas use MathJax (vendor/tex-svg.js, a local file), lazy loaded on this
//    page only.
// 3. English and Chinese are each written as a whole piece, not assembled from the
//    dictionary. Labels inside the figures use language-neutral technical notation,
//    with the explanation in the caption.
// 4. Style follows research-document standards (2026-09-02 ruling): the body only
//    answers how it is computed, why, and how to read it; edge cases and past
//    rulings go in the appendix; no dashes; no anthropomorphising; comparative
//    sentences are kept only where they prevent a specific misreading.
import { getLang } from "/i18n.js";

/* ------------------------------------------------------------------ figures */
const ARROW = `<defs><marker id="mArr" viewBox="0 0 10 10" refX="9" refY="5"
  markerWidth="7" markerHeight="7" orient="auto-start-reverse">
  <path d="M0 0L10 5L0 10z" class="mk"/></marker></defs>`;

function box(x, y, w, h, lines, cls = "") {
  const text = lines.map((l, i) =>
    `<text x="${x + w / 2}" y="${y + 16 + i * 15}" text-anchor="middle">${l}</text>`
  ).join("");
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" class="bx ${cls}"/>${text}`;
}
const arrow = (x1, y1, x2, y2) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="ln" marker-end="url(#mArr)"/>`;

const FIG_PIPELINE = `
<svg viewBox="0 0 940 545" role="img" class="mfig">
  ${ARROW}
  ${box(20, 20, 270, 42, ["FX closes (6 USD pairs)", "y = daily log return"])}
  ${box(335, 20, 270, 42, ["factor sources", "yields, VIX, oil, metals, credit"])}
  ${box(650, 20, 270, 42, ["DOLLAR_LOO, CARRY_LOO", "built from the other pairs"])}
  ${arrow(155, 62, 420, 96)} ${arrow(470, 62, 470, 96)} ${arrow(785, 62, 520, 96)}
  ${box(280, 100, 380, 40, ["pair panel: y + factors, inner join, cap 8 factors"])}
  ${arrow(470, 140, 470, 172)}
  ${box(240, 176, 460, 56, ["rolling window [t-w, t-1],  w = 63 / 126 / 252",
    "standardize X in-window,  demean y  (no look-ahead)"])}
  ${arrow(350, 232, 175, 268)} ${arrow(470, 232, 470, 268)} ${arrow(590, 232, 765, 268)}
  ${box(60, 272, 230, 74, ["OLS", "least squares,", "all factors"], "lane canon")}
  ${box(355, 272, 230, 74, ["Ridge", "closed form, {\u03bb} by", "TimeSeriesSplit CV"], "lane")}
  ${box(650, 272, 230, 74, ["Lasso (wider menu)", "selects factors, then", "OLS refit on selection"], "lane")}
  ${arrow(175, 346, 420, 382)} ${arrow(470, 346, 470, 382)} ${arrow(765, 346, 520, 382)}
  ${box(280, 386, 380, 40, ["\u03b2 de-standardized back to raw units"])}
  ${arrow(470, 426, 470, 452)}
  ${box(200, 456, 540, 40, ["day t:  contribution = \u03b2 \u00b7 x_t ,   residual = y_t \u2212 \u03a3 contributions"])}
  ${arrow(320, 496, 240, 516)} ${arrow(620, 496, 700, 516)}
  ${box(60, 516, 360, 24, ["contract: 54 rows/day (6 pairs \u00d7 3 w \u00d7 3 models)"])}
  ${box(540, 516, 340, 24, ["residual_z \u2192 trigger (OLS@126) \u2192 news evidence"])}
</svg>`;

const FIG_TIMELINE = `
<svg viewBox="0 0 940 130" role="img" class="mfig">
  ${ARROW}
  <line x1="30" y1="86" x2="910" y2="86" class="ln"/>
  <rect x="120" y="66" width="560" height="40" class="bx winfill"/>
  <rect x="726" y="66" width="46" height="40" class="bx canon"/>
  <text x="400" y="90" text-anchor="middle">estimation window  [t\u2212w, \u2026, t\u22121]</text>
  <text x="749" y="90" text-anchor="middle">t</text>
  <text x="120" y="56" text-anchor="middle" class="dim">t\u2212w</text>
  <text x="680" y="56" text-anchor="middle" class="dim">t\u22121</text>
  <text x="860" y="90" class="dim">time</text>
  ${arrow(400, 66, 730, 36)}
  <text x="520" y="28" text-anchor="middle">\u03b2 estimated without day t, applied to day t</text>
</svg>`;

const FIG_LASSO = `
<svg viewBox="0 0 940 120" role="img" class="mfig">
  ${ARROW}
  ${box(20, 40, 190, 52, ["candidate menu", "baseline + optional"])}
  ${arrow(210, 66, 250, 66)}
  ${box(254, 40, 200, 52, ["Lasso at \u03bb (CV)", "zeros out factors"])}
  ${arrow(454, 66, 494, 66)}
  ${box(498, 40, 180, 52, ["selected set S", "coefficients discarded"])}
  ${arrow(678, 66, 718, 66)}
  ${box(722, 40, 198, 52, ["OLS refit on S", "\u03b2 used for attribution"], "canon")}
</svg>`;

/* ------------------------------------------------------------------ EN */
function en() {
  return `<article class="method">
<p class="method__back"><a href="#/attribution">\u2190 Attribution</a></p>
<h1>Methodology</h1>
<p class="lede">This page describes what the dashboard computes and why. Source handling,
threshold calibration, the prompt, and operational guarantees are in the appendix.</p>

<h2>What we are explaining</h2>
<p>Six dollar pairs, daily: USD/EUR, USD/JPY, USD/CAD, USD/NOK, USD/AUD, USD/MXN. All of
them are quoted dollar-first so that a rising number always means a stronger dollar, and
feeds quoted in the opposite direction are inverted at ingestion. For each pair and
each day we take the log return \\( y_t = \\ln P_t - \\ln P_{t-1} \\) and ask how much of
it came from things we measure.</p>
<p>The sense of "explain" is narrow. This is a same-day regression, today's
return on today's factor moves, and nothing in the system estimates a lead-lag
relationship or produces a forecast. Series that live in different time zones are
shifted by a fixed per-pair offset so that a factor observed at a Tokyo close lines up
with the FX day it belongs to, which is an alignment fix rather than a claim that
yesterday's factor tells you anything about today's rate.</p>
<figure class="fig">${FIG_PIPELINE}
<figcaption>The pipeline. Three estimators run on the same window; OLS at 126 days is
the specification everything downstream reads.</figcaption></figure>

<h2>What goes into the panel</h2>
<p>Every pair gets the same five factors: a dollar factor, a carry factor, changes in
the 2y and 10y yield differentials, and the change in VIX. Each pair also gets
one or two with an evident economic link, gold for the yen, WTI for the Canadian
dollar, Brent for the krone, copper and gold for the Aussie, EMB for the peso, with a
hard ceiling of eight factors per pair.</p>
<p>The dollar and carry factors are built leave-one-out. For pair \\(p\\) the dollar factor is the equal-weighted mean of the other five
pairs' returns,</p>
<p>\\[ \\mathrm{DOLLAR}^{(-p)}_t = \\tfrac{1}{5}\\sum_{q \\neq p} y^{(q)}_t , \\]</p>
<p>and the carry factor is a low-yielder basket minus a high-yielder basket with \\(p\\)
removed from whichever basket it sits in. If \\(p\\) were included it would appear on
both sides of the regression, and the dollar factor would absorb attribution that
belongs to the pair's own drivers.</p>
<p>Yield differentials are built legs-first: align each leg by its own offset,
subtract to get the spread, then difference. Doing it the other way round agrees only
when both legs share an alignment, which they do not. Rates, volatility and credit come
in as first differences, commodities and the ETF-based series as log returns.</p>
<p>US legs come from FRED constant-maturity series, foreign legs from each country's
own central bank or ministry. Two require comment. Norway publishes no daily 2y, so the
short slot uses 3y with DGS3 on the US side to match. Mexico publishes no daily
government bond YTM at all, so the long leg is solved out of the CF300 price vector
under the Bonos M convention and carries its own name, MX10Y_DERIVED, since it tracks a
7-to-10-year bucket rather than a point on the curve. Remaining source details are in Appendix A.</p>

<h2>Estimating the betas</h2>
<p>Coefficients are re-fit every day on a rolling window of the previous
\\(w \\in \\{63, 126, 252\\}\\) trading days, with 126 the default. The window for day
\\(t\\) is \\([t-w,\\, t-1]\\), and the natural question is why day \\(t\\) is excluded
when we are only decomposing it rather than predicting it.</p>
<figure class="fig">${FIG_TIMELINE}
<figcaption>The window for day t stops at t-1.</figcaption></figure>
<p>Two reasons. A day inside its own estimation window influences its own coefficients,
so a large move partly explains itself and the residual shrinks on precisely the days
of interest. And the anomaly score below standardizes the residual by its own
recent dispersion, which would then compare a day against a distribution that the day itself helped
determine. For a same-day decomposition, holding the window strictly behind \\(t\\) costs
nothing and keeps both quantities interpretable.</p>
<p>Inside the window each column of \\(X\\) is standardized by that window's own mean
and standard deviation and \\(y\\) is demeaned. Centering both sides is algebraically
identical to fitting an intercept and discarding it, so the slopes match an
explicit-intercept regression while the solvers carry no intercept term. Standardization
puts a VIX change and an oil return on the same scale, so the Ridge and Lasso penalties
act evenly across factors; coefficients are divided back by \\(\\sigma_i\\) before they reach
anything downstream.</p>
<p>Three estimators run on that window. OLS is the baseline and retains all factors:</p>
<p>\\[ \\hat\\beta = \\arg\\min_{\\beta} \\lVert y_c - Z\\beta \\rVert_2^2 . \\]</p>
<p>Its weakness here is collinearity. The dollar and carry factors are built from
overlapping baskets, and the 2y and 10y differentials move together most of the time, so
individual coefficients swing between windows even when the overall fit is steady.</p>
<p>Ridge addresses this by shrinkage:</p>
<p>\\[ \\hat\\beta_\\lambda = (Z^\\top Z + \\lambda I)^{-1} Z^\\top y_c . \\]</p>
<p>\\(\\lambda\\) is selected on a 25-point log grid by forward-chained cross-validation
inside the window, three sequential splits, never shuffled, since shuffled folds put
later observations in the training set. We refresh it every 21 trading days rather than
daily, because re-selecting daily adds its own variation to the coefficients the penalty is
meant to stabilize.</p>
<p>Lasso is used for a different question, namely which factors are active on this window
at all:</p>
<p>\\[ \\hat\\beta_\\lambda = \\arg\\min_{\\beta} \\tfrac{1}{2n}\\lVert y_c - Z\\beta
\\rVert_2^2 + \\lambda \\lVert \\beta \\rVert_1 . \\]</p>
<p>We use it only for the selection. Its coefficients are biased toward zero, so
attributing with them would understate every contribution while the identity still
closed; the error would not be visible in the output. We take the nonzero set
\\(S\\), discard the penalized coefficients, and refit plain OLS on \\(Z_S\\); those
refit coefficients are what the page shows. The Lasso menu is slightly wider, adding two
credit factors, and on windows where it selects nothing the betas are all zero, the
whole move is assigned to the residual, and the event is counted.</p>
<figure class="fig">${FIG_LASSO}
<figcaption>Selection first, then an unpenalized refit on the selected set.</figcaption></figure>
<p>The three estimators run independently; there is no averaging, and
Lasso's selection does not touch the design matrix the other two see. The output store holds
6 pairs \\(\\times\\) 3 windows \\(\\times\\) 3 estimators, 54 rows a day.</p>

<h2>Reading a day</h2>
<p>Given the betas, each factor's contribution on day \\(t\\) is its beta times that
factor's move, and whatever is left over is the residual:</p>
<p>\\[ c_{i,t} = \\beta_{i,t}\\, x_{i,t}, \\qquad
y_t = \\sum_i c_{i,t} + r_t . \\]</p>
<p>The identity holds by construction; it serves as a correctness check in the tests but
carries no information about fit. The implicit intercept from
the centered regression ends up inside \\(r_t\\); for daily returns it is near zero and
is not reported separately.</p>
<p>On the page the contributions are grouped into three buckets. Systematic is the
sum of the dollar and carry contributions, the part of the move shared with the rest of
the dollar complex. Exogenous is everything else we measure. Residual is the remainder. Alongside
them we track the window \\(R^2\\) on all factors and a second one re-estimated without
the two internal factors, which helps identify a pair whose external drivers have lost
explanatory power; that second series is monitoring only and never feeds attribution
or any parameter choice.</p>

<h2>When a day is worth a second look</h2>
<p>The residual is standardized by its own recent dispersion, lagged a day:</p>
<p>\\[ z_t = \\frac{r_t}{\\hat\\sigma_{t-1}}, \\qquad
\\hat\\sigma_{t-1} = \\mathrm{sd}\\big(r_{t-126}, \\ldots, r_{t-1}\\big) . \\]</p>
<p>A pair-day is flagged when \\( |z_t| \\ge 2 \\) and \\( |r_t| \\ge 50 \\) bp, capped
at three pairs a day. The absolute floor is there because a z-score on its own picks up
quiet-period noise: in a calm month a 12 bp residual clears \\(|z| \\ge 2\\) without being
economically meaningful. Together the two conditions fire roughly once every 4.5 to 5
trading days across the panel over the full sample.</p>

<h2>Whether the reading holds up</h2>
<p>A decomposition should not depend on the choice of estimator, so the three are
compared every day. For each pair we take the L1 distance between
three-bucket decompositions in basis points, OLS against Ridge and OLS against
post-Lasso, kept as two numbers because they move for different reasons: the first
responds to collinearity pushing coefficients around, the second to the selected set
changing. Each is scaled by the pair's trailing median absolute residual so the
magnitudes are comparable across pairs, and a state flips only after two consecutive
days past the pair's own rolling 95th percentile.</p>
<p>The badge shows one of four things: models agree, Ridge diverges, Lasso reselects,
Lasso abstains. Abstention, where Lasso keeps no factor at all, comes with a run-length
counter, since one such day is an ordinary selection outcome while fifty consecutive
days indicate that the factor menu is not capturing the pair in that period. Those days
are excluded from the quantile sample, since their distance is an artifact of the
construction rather than a disagreement between estimates, though the badge still
shows. Calibration is in Appendix B.</p>
<p>A rolling PCA of the six-pair correlation matrix runs alongside and addresses one
question: whether the whole dollar complex is moving or a single currency is. We watch
the
correlation of PC1 with the dollar factor and the projection \\(R^2\\) of carry on the
plane spanned by PC2 and PC3. The projection replaced a plain correlation with PC2
because individual components are not rotation-invariant: with close eigenvalues the
plane is stable while the basis inside it rotates, so the old indicator moved without
any change in the underlying structure. No attribution numbers come out of the PCA and the components get no economic
labels, since a statement like "PC2 contributed 40 bp" would present a basis-dependent
quantity as an economic estimate.</p>

<h2>News</h2>
<p>News enters through two separate channels with different requirements, and they share
no filters.</p>
<p>The headline rail queries Google News RSS every half hour, with a keyword
classifier folding opinion, analysis, recaps and trade setups into a collapsed section
so the main list stays close to event reporting. It keys on the title rather than the
outlet, since major outlets publish opinion and small outlets publish event reporting.
Anything a published commentary cited stays in the main list whatever its type.</p>
<p>The narrative channel starts from a flag. We retrieve that day's coverage in two
passes, same-day and previous-day reporting first, later retrospectives second, tagged
by which pass found them, and we store everything retrieved whether or not it ends up
cited. The query names the currency and its central bank and contains no causal terms,
since a query built around a suspected cause retrieves articles that support it.
The display classifier deliberately does not apply here; retrospective analysis is
useful evidence for judging the size of an event.</p>
<p>Gemini is called only after a flag, a few times a day at most and under a cost cap.
It gets a fact table and the retrieved sources and returns three short paragraphs, what
was reported, why the model could not account for the move, and what would test the
reading, plus one of four assessments running from no relevant reporting to accounts
for. It does not retrieve, compute, forecast, or decide what gets flagged. Six
mechanical checks then run on the draft, covering source provenance, cited dates,
verbatim numbers, causal language, directional forecasts, and agreement between the two
language versions; one failure discards the draft and the record is kept either way.
Appendix C covers the prompt.</p>
<p>A story on the dashboard is evidence attached to a flagged day, so the residual for
that day and pair appears once and never beside an individual headline. An earlier
design assigned each story a share of it; the design and the reasons for removing it are
in Appendix E.</p>

<h2>Appendix</h2>

<p><strong>A. Sources.</strong> The sample starts 2010-01-01 and prices are Yahoo
Finance daily closes. Foreign yield legs come from official publishers rather than an
aggregator, because an aggregator that changes a definition silently produces a break we
cannot see in the data.</p>
<div class="tblwrap"><table>
<tr><th>Pair</th><th>Source</th><th>Tenors</th><th>Notes</th></tr>
<tr><td>EUR</td><td>Bundesbank</td><td>2y / 10y</td><td>Svensson spot curve, daily.
Holiday rows carry a dot in the value column and are dropped whole.</td></tr>
<tr><td>JPY</td><td>Ministry of Finance</td><td>2y / 10y</td><td>Daily. Historical files
sit in a subdirectory and the current month overrides history; missing values are
dashes.</td></tr>
<tr><td>CAD</td><td>Bank of Canada Valet</td><td>2y / 10y</td><td>Daily, no gaps since
2001.</td></tr>
<tr><td>NOK</td><td>Norges Bank</td><td>3y / 10y</td><td>Two series spliced at
2019-01-02 with the splice-day difference set to missing. The API answers 404 rather
than returning an empty result for a range with no data.</td></tr>
<tr><td>AUD</td><td>RBA table F2</td><td>2y / 10y</td><td>History from 1995-01-03; the
2y gap from 2013-05-20 to 2013-08-30 stays missing. Published weekly on Friday, so a
value can be seven days old and is flagged stale. Files carry eleven metadata rows and
need a browser user agent.</td></tr>
<tr><td>MXN</td><td>Banxico SIE</td><td>1y / 10y</td><td>Short leg is the 364-day Cetes
rate. Long leg from the CF300 price vector, 182-day coupon periods, interest at
182/360.</td></tr>
</table></div>
<p>Other series: dVIX from the VIX close, dHY_OAS from the ICE BofA high-yield
option-adjusted spread, HY_EXCESS as HYG minus IEI in log returns, EMB for the peso, and
WTI, Brent, gold and copper as log returns. FRED clips the high-yield spread to three
rolling years, so a saved history is spliced to the live window at 2026-02-07. dBAA10Y
stood in during the clipped period and left every factor menu once the full history came
back; when a source fails we fall back through cache and local file rather than
substituting a different series, since a substituted series produces values that appear normal but measure a
different quantity.</p>

<p><strong>B. Thresholds and calibration.</strong> MX10Y_DERIVED is cross-checked
monthly against the primary-market auction average. The two carry a primary/secondary
basis, so the check subtracts the median raw deviation of the last twelve available
months (minimum six, current month excluded) and judges the residual at 15 bp; six
consecutive available months over the line fail the series, a single month past 50 bp
raw alarms immediately, and a month overshooting with the opposite sign to the basis is
recorded as basis reversion and not counted. The dHY_OAS splice was frozen after
checking the overlap: level gap at most 1 bp, correlation of differences at least 0.999.
For the robustness badge, calibration on the full sample puts each pair in a non-green
distance state 5.9 to 7.2 percent of days, in episodes with a median length of two to
three days; the tails line up with March 2020 across the panel, the 2011 and 2022 yen
interventions, and the 2024 carry unwind. On 2025-04-07, a large market day, five pairs
stayed in agreement and the single flag was a Lasso reselection episode in the krone
during the oil move, consistent with the intended target of the check, reading stability rather than
market stress.</p>

<p><strong>C. Prompt design.</strong> The prompt is the only place we speak to the
model, and the six checks cannot rescue a bad one; they catch invented numbers and
sources, not a draft in which every sentence is compliant but the whole is constructed
to force an explanation. Seven choices are deliberate.</p>
<p>The fact table arrives as preformatted strings, and those same strings are the
whitelist for the verbatim-number check, so the model never sees a float it could round
and performs no arithmetic. The task is described as reporting what is and
is not established rather than as explaining the move, since naming explanation as the
goal would make the absence of one register as failure. Judgement comes before writing: the model
picks an assessment first and the schema's property ordering fixes that sequence in
generation, because writing first produces a label chosen to fit the paragraph. The four
assessments are listed with the negative ones first, no_relevant_reporting and
does_not_account_for ahead of the two positive ones, so that the negative options do not
appear to be fallbacks. Calibration is given as a real number, the median unexplained move across
flagged days, since without an anchor "large" drifts toward the scale of
whatever the retrieved articles describe. The prompt states plainly that
reporting which does not account for the move is a complete finding, as is a model whose
own explanatory power has been falling, and that being unable to tell which is also a
legitimate answer; does_not_account_for publishes. The coverage step is written as an
action, name the kind of event, read the row listing the factors the model carries,
report what the comparison yields, with all three outcomes weighted equally, because the
earlier phrasing along the lines of "point it out if the event is not in the factor set"
supplied the expected answer, and the model produced it even for events the factor set
does carry.
Finally the banned-word list in the prompt is the verifier's list word for word; when
the two drift apart a draft is discarded for a word the prompt never prohibited, and the
natural response is to weaken the check rather than fix the prompt.</p>

<p><strong>D. Operations.</strong> Acquisition runs a fixed chain of online fetch, last
good parquet cache, then read-only local file, with every fallback logged by file name
and last date. A missing newest value is carried forward with the row flagged stale. Two
rules mark a row provisional: the newest row of every pair, since the price feed can
serve a bar that has not closed, and any row whose inputs include a carried-forward
value from a lagged source, which at present means AUD and its weekly file. Provisional
rows may be overwritten once the as-of date of their inputs advances; final rows never
change. Live runs are idempotent, re-running a date produces no duplicate rows, and
overwrites leave an audit record. Downstream consumers including this site read
versioned output files and recompute nothing.</p>

<p><strong>E. Removed designs and incidents.</strong> An earlier version split each
flagged day's residual evenly across the stories cited that day and showed a percentage
next to each. It was removed because the share was not measurable, the even split was a convention
presented as a measurement, and on the common single-flag day every story received the
same percentage. An earlier
heartbeat measured time since the last published commentary and now measures time since
the last completed run, since quiet weeks are normal and an alarm that is active most of the
time stops being informative. In August 2026 an unclosed price bar entered the store as a final row; the
frontier-provisional rule and a clock check on bar close both date from that. Known
limits: RSS dates have day precision in GMT, so items near midnight can land on a
neighbouring day; the display classifier is a word list and recaps without marker words
pass through; empty Lasso selections cluster in low-volatility periods for the yen,
consistent with its weak loading on the dollar factor.</p>
</article>`;
}

/* ------------------------------------------------------------------ ZH */
function zh() {
  return `<article class="method">
<p class="method__back"><a href="#/attribution">\u2190 \u8fd4\u56de\u5f52\u56e0</a></p>
<h1>\u65b9\u6cd5\u8bba</h1>
<p class="lede">\u672c\u9875\u56de\u7b54\u4e09\u4e2a\u95ee\u9898\uff1a\u8fd9\u4e9b\u6570\u5b57\u600e\u4e48\u7b97\u3001\u4e3a\u4ec0\u4e48\u8fd9\u6837\u7b97\u3001\u7ed3\u679c\u600e\u4e48\u8bfb\u3002\u8fd0\u884c\u7ec6\u8282\u3001\u8fb9\u754c\u60c5\u5f62\u4e0e\u8bbe\u8ba1\u51b3\u5b9a\u7684\u5386\u53f2\u5728\u9644\u5f55\u3002</p>

<h2>1. \u8303\u56f4</h2>
<p>\u88ab\u89e3\u91ca\u5bf9\u8c61\u662f\u516d\u4e2a\u7f8e\u5143\u8d27\u5e01\u5bf9\u7684\u65e5\u5ea6\u53d8\u52a8\uff1aUSD/EUR\u3001USD/JPY\u3001USD/CAD\u3001USD/NOK\u3001USD/AUD\u3001USD/MXN\u3002\u62a5\u4ef7\u7edf\u4e00\u7f8e\u5143\u5728\u524d\uff0c\u6570\u503c\u4e0a\u6da8\u5373\u7f8e\u5143\u8d70\u5f3a\uff1b\u53cd\u5411\u62a5\u4ef7\u7684\u5e02\u573a\u5e8f\u5217\uff08EURUSD\u3001AUDUSD\uff09\u5728\u63a5\u5165\u65f6\u53d6\u5012\u6570\u3002\u6bcf\u65e5\u88ab\u89e3\u91ca\u7684\u91cf\u662f\u5bf9\u6570\u6536\u76ca \\( y_t = \\ln P_t - \\ln P_{t-1} \\)\u3002</p>
<p>\u7cfb\u7edf\u53ea\u505a\u540c\u671f\u5f52\u56e0\uff1a\u628a\u4eca\u5929\u7684\u53d8\u52a8\u5206\u89e3\u5230\u4eca\u5929\u5404\u56e0\u5b50\u7684\u53d8\u52a8\u4e0a\uff0c\u4e0d\u4f30\u8ba1\u9884\u6d4b\u5173\u7cfb\uff0c\u4e0d\u505a\u9884\u6d4b\u3002\u5bf9\u9f50\u4e0d\u540c\u65f6\u533a\u5e8f\u5217\u7684\u51bb\u7ed3\u504f\u79fb\u6863\u6848\u662f\u6d4b\u91cf\u4fee\u6b63\u3002</p>
<figure class="fig">${FIG_PIPELINE}
<figcaption>\u5b8c\u6574\u7ba1\u7ebf\u3002\u4e09\u4e2a\u4f30\u8ba1\u91cf\u5728\u540c\u4e00\u7a97\u53e3\u4e0a\u5e76\u884c\uff1bOLS\uff08\u91cd\u63cf\u8fb9\u6846\uff09\u662f canonical \u53e3\u5f84\uff0c\u51e1\u53ea\u80fd\u7528\u4e00\u4e2a\u6570\u7684\u6d88\u8d39\u8005\u8bfb\u7684\u90fd\u662f\u5b83\u3002</figcaption></figure>

<h2>2. \u6570\u636e</h2>
<p>\u6837\u672c\u81ea 2010-01-01 \u8d77\u3002\u4ef7\u683c\u53d6 Yahoo Finance \u65e5\u6536\u76d8\u3002\u5229\u5dee\u56e0\u5b50\u6bcf\u5bf9\u4e24\u817f\uff1a\u7f8e\u56fd\u817f\u53d6 FRED \u56fa\u5b9a\u671f\u9650\u5e8f\u5217\uff0c\u5916\u56fd\u817f\u53d6\u5404\u56fd\u5b98\u65b9\u6e90\u3002\u6e90\u7684\u683c\u5f0f\u7ec6\u8282\u89c1\u9644\u5f55 D\u3002</p>
<div class="tblwrap"><table>
<tr><th>\u8d27\u5e01\u5bf9</th><th>\u5916\u56fd\u6e90</th><th>\u671f\u9650\uff08\u77ed / \u957f\uff09</th><th>\u8981\u70b9</th></tr>
<tr><td>EUR</td><td>\u5fb7\u56fd\u8054\u90a6\u94f6\u884c SDMX</td><td>2y / 10y</td><td>Svensson \u5373\u671f\u66f2\u7ebf\uff0c\u6bcf\u65e5\u53d1\u5e03\u3002</td></tr>
<tr><td>JPY</td><td>\u65e5\u672c\u8d22\u52a1\u7701</td><td>2y / 10y</td><td>\u6bcf\u65e5\u53d1\u5e03\u3002</td></tr>
<tr><td>CAD</td><td>\u52a0\u62ff\u5927\u592e\u884c Valet</td><td>2y / 10y</td><td>\u6bcf\u65e5\u53d1\u5e03\uff0c2001 \u5e74\u8d77\u65e0\u7f3a\u503c\u3002</td></tr>
<tr><td>NOK</td><td>\u632a\u5a01\u592e\u884c</td><td>3y / 10y</td><td>\u65e0\u65e5\u9891 2y\uff0c\u77ed\u7aef\u69fd\u4f4d\u7528 3y\uff0c\u7f8e\u56fd\u817f\u76f8\u5e94\u914d DGS3\u3002\u4e24\u6bb5\u6e90\u5e8f\u5217\u5728 2019-01-02 \u62fc\u63a5\uff0c\u62fc\u63a5\u65e5\u5dee\u5206\u7f6e\u7f3a\uff1a\u4e24\u4e2a\u4e0d\u540c\u5de5\u5177\u4e4b\u95f4\u7684\u8df3\u53d8\u6ca1\u6709\u7ecf\u6d4e\u542b\u4e49\u3002</td></tr>
<tr><td>AUD</td><td>\u6fb3\u6d32\u50a8\u5907\u94f6\u884c F2 \u8868</td><td>2y / 10y</td><td>\u5386\u53f2\u81ea 1995-01-03 \u8d77\u30022y \u5728 2013-05-20 \u81f3 2013-08-30 \u6709\u7a7a\u7a97\uff0c\u6309\u7f3a\u5931\u4fdd\u7559\u3002F2 \u6bcf\u5468\u4e94\u53d1\u5e03\uff0c\u6700\u65b0\u503c\u6700\u591a\u9648\u65e7\u4e03\u5929\uff0c\u6253 stale \u6807\u8bb0\u3002</td></tr>
<tr><td>MXN</td><td>\u58a8\u897f\u54e5\u592e\u884c SIE</td><td>1y / 10y</td><td>\u58a8\u897f\u54e5\u65e0\u65e5\u9891\u56fd\u503a YTM \u5e8f\u5217\u3002\u77ed\u817f\u7528 364 \u5929 Cetes \u5229\u7387\u3002\u957f\u817f MX10Y_DERIVED \u7531 CF300 \u4ef7\u683c\u5411\u91cf\uff08\u5168\u4ef7\u3001\u7968\u606f\u3001\u5269\u4f59\u5929\u6570\uff09\u6309 Bonos M \u60ef\u4f8b\u53cd\u63a8\uff1a182 \u5929\u4ed8\u606f\u671f\uff0c\u5229\u606f\u6309 182/360\u3002\u8be5\u5411\u91cf\u8ddf\u8e2a 7 \u81f3 10 \u5e74\u6876\u7684\u57fa\u51c6\u5238\u800c\u975e\u4e25\u683c 10 \u5e74\u70b9\uff0c\u6545\u5355\u72ec\u547d\u540d\uff0c\u5e76\u6309\u6708\u5bf9 SF30057\uff08\u4e00\u7ea7\u5e02\u573a\u62cd\u5356\u6708\u5747\uff09\u4ea4\u53c9\u6821\u9a8c\u3002\u4e24\u8005\u95f4\u5b58\u5728\u4e00\u4e8c\u7ea7\u57fa\u5dee\uff0c\u6545\u5148\u6263\u8fd1 12 \u4e2a\u53ef\u5f97\u6708\uff08\u6700\u5c11 6 \u4e2a\uff0c\u4e0d\u542b\u5f53\u6708\uff09\u539f\u59cb\u504f\u5dee\u7684\u4e2d\u4f4d\u6570\uff0c\u518d\u5bf9\u6b8b\u5dee\u5224 15 bp\uff1b\u8fde\u7eed 6 \u4e2a\u53ef\u5f97\u6708\u8d85\u9650\u5224\u5931\u8d25\uff0c\u5355\u6708\u539f\u59cb\u504f\u5dee\u8d85 50 bp \u7acb\u5373\u544a\u8b66\uff1b\u6b8b\u5dee\u4e0e\u57fa\u5dee\u53cd\u53f7\u7684\u5355\u6708\u8d85\u9650\u8bb0\u4e3a\u57fa\u5dee\u56de\u5f52\uff0c\u4e0d\u8ba1\u5165\u8fde\u7eed\u8ba1\u6570\u3002</td></tr>
</table></div>
<p>\u5176\u4f59\u5e8f\u5217\u3002dVIX \u662f VIX \u6536\u76d8\u7684\u4e00\u9636\u5dee\u5206\u3002dHY_OAS \u662f ICE BofA \u7f8e\u56fd\u9ad8\u6536\u76ca OAS \u7684\u4e00\u9636\u5dee\u5206\uff1bFRED \u628a\u8be5\u5e8f\u5217\u9497\u6210\u6eda\u52a8\u4e09\u5e74\uff0c\u5168\u53f2\u6765\u81ea\u4e00\u4efd\u5b58\u6863\uff081996-12-31 \u81f3 2026-02-06\uff09\uff0c\u4e0e FRED \u5b9e\u65f6\u7a97\u53e3\u5728\u56fa\u5b9a\u65e5 2026-02-07 \u62fc\u63a5\u3002\u62fc\u63a5\u51bb\u7ed3\u524d\u6821\u9a8c\u8fc7\u91cd\u53e0\u6bb5\uff1a\u6c34\u5e73\u5dee\u4e0d\u8d85 1 bp\uff0c\u5dee\u5206\u76f8\u5173\u4e0d\u4f4e\u4e8e 0.999\u3002\u62fc\u63a5\u65e5\u5dee\u5206\u7f6e\u7f3a\u3002dBAA10Y\uff08\u7a46\u8fea Baa \u51cf 10 \u5e74\u7f8e\u503a\uff09\u53ea\u51fa\u73b0\u5728\u8fd9\u6bb5\u5386\u53f2\u91cc\uff1aFRED \u9497\u77ed\u671f\u95f4\u5b83\u4f5c\u4e34\u65f6\u66ff\u4ee3\uff0c\u5168\u53f2\u5230\u4f4d\u540e\u9000\u51fa\u5168\u90e8\u56e0\u5b50\u83dc\u5355\u3002\u4e0d\u53ef\u5f97\u7684\u5e8f\u5217\u7edd\u4e0d\u7531\u53e6\u4e00\u6761\u5e8f\u5217\u9876\u66ff\uff0c\u83b7\u53d6\u5931\u8d25\u53ea\u8d70\u4e0b\u6587\u7684\u56de\u9000\u94fe\u3002HY_EXCESS \u662f HYG \u5bf9\u6570\u6536\u76ca\u51cf IEI \u5bf9\u6570\u6536\u76ca\u3002EMB\uff08\u7f8e\u5143\u8ba1\u4ef7\u65b0\u5174\u5e02\u573a\u4e3b\u6743\u503a ETF\uff09\u7528\u4e8e USDMXN\u3002WTI\u3001Brent\u3001\u9ec4\u91d1\u3001\u94dc\u6309\u5bf9\u6570\u6536\u76ca\u5165\u6a21\u3002</p>
<p>\u6570\u636e\u83b7\u53d6\u6309\u56fa\u5b9a\u4e09\u6b65\u56de\u9000\uff1a\u7ebf\u4e0a\u73b0\u62c9\uff1b\u5931\u8d25\u9000\u4e0a\u6b21\u6210\u529f\u7684 parquet \u7f13\u5b58\uff1b\u518d\u5931\u8d25\u9000\u53ea\u8bfb\u672c\u5730\u6587\u4ef6\u3002\u6bcf\u6b21\u56de\u9000\u90fd\u8bb0\u5f55\u6587\u4ef6\u540d\u4e0e\u5176\u672b\u65e5\u3002</p>
<p>\u5e8f\u5217\u6700\u65b0\u503c\u7f3a\u5931\u65f6\u6cbf\u7528\u524d\u503c\u5e76\u6253 stale \u6807\u8bb0\uff0c\u6807\u8bb0\u968f\u6570\u636e\u5b58\u50a8\u3002provisional \u6309\u4e24\u6761\u89c4\u5219\u6807\u8bb0\u3002\u5176\u4e00\uff0c\u6bcf\u5bf9\u7684\u6700\u65b0\u4e00\u884c\u6052\u4e3a provisional\uff0c\u56e0\u4e3a\u4ef7\u683c\u6e90\u53ef\u80fd\u7ed9\u51fa\u5c1a\u672a\u6536\u76d8\u7684 bar\uff1b\u6b64\u89c4\u5219\u6e90\u4e8e 2026 \u5e74 8 \u6708\u7684\u4e00\u6b21\u4e8b\u6545\uff08\u9644\u5f55 C\uff09\u3002\u5176\u4e8c\uff0c\u8f93\u5165\u542b\u53d1\u5e03\u6ede\u540e\u6e90\u5ef6\u7528\u503c\u7684\u884c\u4fdd\u6301 provisional\uff0c\u76ee\u524d\u6837\u672c\u91cc\u53ea\u6709 AUD \u547d\u4e2d\uff08\u5468\u9891\u6536\u76ca\u7387\u6587\u4ef6\uff09\u3002provisional \u884c\u4ec5\u5728\u8f93\u5165 as-of \u65e5\u671f\u524d\u8fdb\u65f6\u5141\u8bb8\u8986\u5199\uff1b\u7ec8\u5c40\u884c\u6c38\u4e0d\u6539\u52a8\u3002</p>
<p>\u6bcf\u6761\u5e8f\u5217\u6309\u8be5\u5bf9\u7684\u51bb\u7ed3\u504f\u79fb\uff080 \u6216 +1 \u4ea4\u6613\u65e5\uff0c\u6309\u6e90\u4e0e\u5bf9\u5b9e\u6d4b\u4e00\u6b21\uff09\u5bf9\u9f50\u540e\u5165\u8be5\u5bf9\u9762\u677f\u3002\u9762\u677f\u6309\u5bf9\u7ec4\u88c5\u800c\u975e\u5168\u5c40 join\uff0c\u4e00\u56fd\u5047\u65e5\u4e0d\u5220\u53e6\u4e00\u56fd\u7684\u884c\u3002</p>

<h2>3. \u56e0\u5b50</h2>
<p>\u516d\u5bf9\u5171\u4eab\u4e94\u56e0\u5b50\u57fa\u7ebf\uff1aDOLLAR_LOO\u3001CARRY_LOO\u3001d2Y_DIFF\u3001d10Y_DIFF\u3001dVIX\u3002\u5404\u5bf9\u9644\u52a0\uff1aJPY \u52a0 GOLD\uff0cCAD \u52a0 WTI\uff0cNOK \u52a0 BRENT\uff0cAUD \u52a0 COPPER \u4e0e GOLD\uff0cMXN \u52a0 EMB\uff0cEUR \u4e0d\u52a0\u3002\u6bcf\u5bf9\u786c\u4e0a\u9650 8 \u4e2a\u56e0\u5b50\u3002</p>
<p>\u4e24\u4e2a FX \u5185\u90e8\u56e0\u5b50\u6309 leave-one-out \u6784\u9020\u3002\u5bf9 pair \\(p\\)\uff0c\u7f8e\u5143\u56e0\u5b50\u662f\u5176\u4f59\u4e94\u5bf9\u6536\u76ca\u7684\u7b49\u6743\u5747\u503c\uff0c\\( \\mathrm{DOLLAR}^{(-p)}_t = \\tfrac{1}{5}\\sum_{q \\neq p} y^{(q)}_t \\)\uff1b\u5229\u5dee\u56e0\u5b50\u662f\u4f4e\u606f\u7ec4\u51cf\u9ad8\u606f\u7ec4\u7684\u7bee\u5b50\u6536\u76ca\uff0c\u4e24\u7ec4\u5404\u81ea\u5254\u9664 \\(p\\)\u3002\u5254\u9664\u81ea\u8eab\u907f\u514d\u56de\u5f52\u90e8\u5206\u5730\u7528\u81ea\u5df1\u89e3\u91ca\u81ea\u5df1\u3002</p>
<p>\u5229\u5dee\u56e0\u5b50\u5148\u817f\u540e\u5dee\uff1a\u4e24\u817f\u5404\u6309\u81ea\u8eab\u504f\u79fb\u5bf9\u9f50\u5230\u8be5\u5bf9\u4ea4\u6613\u65e5\u7d22\u5f15\uff0c\u76f8\u51cf\u5f97\u5229\u5dee\uff0c\u518d\u53d6\u4e00\u9636\u5dee\u5206\u3002\u5229\u7387\u3001\u6ce2\u52a8\u7387\u3001\u4fe1\u7528\u7c7b\u53d6\u4e00\u9636\u5dee\u5206\uff0c\u5546\u54c1\u4e0e\u80a1\u6743\u7c7b\u53d6\u5bf9\u6570\u6536\u76ca\u3002</p>
<p>OLS \u4e0e Ridge \u6c38\u8fdc\u7528\u57fa\u7ebf\u8bbe\u8ba1\u77e9\u9635\u3002Lasso \u7684\u5019\u9009\u83dc\u5355\u66f4\u5bbd\uff0c\u53e6\u63d0\u4f9b\u4fe1\u7528\u56e0\u5b50 HY_EXCESS \u4e0e dHY_OAS\u3002\u4e0a\u9650\u5728 AUD \u5904\u7ed1\u7d27\uff1a\u5176\u83dc\u5355\u53ea\u63d0\u4f9b HY_EXCESS\uff0c\u56e0\u57fa\u7ebf\u4e94\u4e2a\u52a0 COPPER\u3001GOLD\u3001HY_EXCESS \u5df2\u6ee1\u516b\u4e2a\u3002Lasso \u7684\u9009\u62e9\u4e0d\u53cd\u54fa\u53e6\u5916\u4e24\u4e2a\u4f30\u8ba1\u91cf\u3002</p>

<h2>4. \u4f30\u8ba1</h2>
<p>\u5168\u90e8\u7cfb\u6570\u6bcf\u65e5\u91cd\u4f30\uff0c\u7a97\u53e3\u53d6\u6700\u8fd1 \\(w \\in \\{63, 126, 252\\}\\) \u4e2a\u4ea4\u6613\u65e5\uff0c\u9ed8\u8ba4 126\u3002\u7b2c \\(t\\) \u65e5\u7684\u7a97\u53e3\u662f \\([t-w,\\, t-1]\\)\uff1b\u7528\u5728\u7b2c \\(t\\) \u65e5\u7684 beta \u7684\u4f30\u8ba1\u4e0d\u542b\u7b2c \\(t\\) \u65e5\u3002</p>
<figure class="fig">${FIG_TIMELINE}
<figcaption>\u4f30\u8ba1\u4e0e\u5e94\u7528\u4e0d\u91cd\u53e0\uff1a\u622a\u81f3 \\(t-1\\) \u62df\u5408\u7684\u7cfb\u6570\u53ea\u7528\u4e8e\u7b2c \\(t\\) \u65e5\u3002</figcaption></figure>
<p>\u7a97\u53e3\u5185\u9010\u5217\u6807\u51c6\u5316\u8bbe\u8ba1\u77e9\u9635\uff08\u5747\u503c\u6807\u51c6\u5dee\u53ea\u7528\u672c\u7a97\u53e3\uff09\uff0c\u88ab\u89e3\u91ca\u53d8\u91cf\u53bb\u5747\u503c\u3002\u4e24\u8fb9\u4e2d\u5fc3\u5316\u5728\u4ee3\u6570\u4e0a\u7b49\u4ef7\u4e8e\u62df\u5408\u622a\u8ddd\u518d\u4e22\u5f03\uff1a\u659c\u7387\u4e0e\u663e\u5f0f\u5e26\u622a\u8ddd\u56de\u5f52\u4e00\u81f4\uff0c\u6c42\u89e3\u5668\u4e0d\u643a\u5e26\u622a\u8ddd\u9879\u3002\u6807\u51c6\u5316\u4f7f Ridge \u4e0e Lasso \u7684\u60e9\u7f5a\u5bf9\u4e0d\u540c\u91cf\u7eb2\u7684\u56e0\u5b50\u4e00\u81f4\u3002\u7f5a\u56de\u5f52\u7cfb\u6570\u6362\u56de\u539f\u91cf\u7eb2 \\( \\beta_i = \\beta^{\\mathrm{std}}_i / \\sigma_i \\) \u540e\u624d\u8fdb\u5165\u5f52\u56e0\u3002</p>
<p>\u4e09\u4e2a\u4f30\u8ba1\u91cf\u5728\u540c\u4e00\u7a97\u53e3\u4e0a\u5e76\u884c\u3002</p>
<p><strong>OLS\u3002</strong>\\( \\hat\\beta = \\arg\\min_{\\beta} \\lVert y_c - Z\\beta \\rVert_2^2 \\)\uff0c\u5168\u90e8\u56e0\u5b50\u4fdd\u7559\u3002\u56e0\u5b50\u76f8\u5173\u65f6\u5355\u4e2a\u7cfb\u6570\u4e0d\u7a33\uff0cRidge \u4e0e Lasso \u4e24\u8def\u9488\u5bf9\u6b64\u95ee\u9898\u3002</p>
<p><strong>Ridge\u3002</strong>\\( \\hat\\beta_\\lambda = (Z^\\top Z + \\lambda I)^{-1} Z^\\top y_c \\)\u3002\\(\\lambda\\) \u5728 \\(10^{-4}\\) \u81f3 \\(10^{4}\\) \u7684 25 \u70b9\u5bf9\u6570\u7f51\u683c\u4e0a\u7531\u7a97\u53e3\u5185\u524d\u8fdb\u5f0f\u4ea4\u53c9\u9a8c\u8bc1\u9009\u51fa\uff1a\u4e09\u6298\u987a\u65f6\u5207\u5206\uff0c\u7edd\u4e0d shuffle\uff0c\u56e0\u4e3a\u6253\u4e71\u987a\u5e8f\u7b49\u4e8e\u8ba9\u6a21\u578b\u5728\u672a\u6765\u4e0a\u8bad\u7ec3\u3002\u6bcf 21 \u4e2a\u4ea4\u6613\u65e5\u91cd\u9009\u4e00\u6b21\uff1b\u9009\u5230\u7f51\u683c\u8fb9\u754c\u5219\u5411\u8be5\u65b9\u5411\u6269\u4e24\u4e2a\u6570\u91cf\u7ea7\u91cd\u9009\u5e76\u8bb0\u65e5\u5fd7\u3002</p>
<p><strong>Lasso \u52a0\u91cd\u62df\u3002</strong>\\( \\hat\\beta_\\lambda = \\arg\\min_{\\beta} \\tfrac{1}{2n}\\lVert y_c - Z\\beta \\rVert_2^2 + \\lambda \\lVert \\beta \\rVert_1 \\)\uff0c\\(\\lambda\\) \u9009\u6cd5\u4e0e Ridge \u76f8\u540c\u3002\u53ea\u4fdd\u7559\u975e\u96f6\u96c6 \\(S\\)\uff0c\u6536\u7f29\u540e\u7684\u7cfb\u6570\u4e22\u5f03\uff1a\u6536\u7f29\u628a\u91cf\u7ea7\u7cfb\u7edf\u6027\u538b\u5411\u96f6\uff0c\u636e\u6b64\u5f52\u56e0\u4f1a\u4f4e\u4f30\u8d21\u732e\u3002\u5728 \\(Z_S\\) \u4e0a\u91cd\u8dd1\u666e\u901a OLS\uff0c\u9875\u9762\u5c55\u793a\u7684\u662f\u91cd\u62df\u7cfb\u6570\u3002Lasso \u4e00\u4e2a\u4e0d\u9009\u65f6 beta \u5168\u96f6\uff0c\u5f53\u65e5\u5168\u989d\u8ba1\u5165\u6b8b\u5dee\uff0c\u4e8b\u4ef6\u8ba1\u6570\u3002</p>
<figure class="fig">${FIG_LASSO}
<figcaption>Lasso \u4e24\u6bb5\u5f0f\uff1a\u5148\u9009\u62e9\uff0c\u518d\u5728\u9009\u4e2d\u96c6\u4e0a\u65e0\u60e9\u7f5a\u91cd\u62df\u3002\u53ea\u6709\u91cd\u62df\u7cfb\u6570\u4e0a\u9875\u9762\u3002</figcaption></figure>
<p>\u65e0 ensemble\uff0c\u4f30\u8ba1\u91cf\u95f4\u65e0\u5e73\u5747\u3002\u6bcf\u8def\u5404\u81ea\u4ea7\u51fa\u5b8c\u6574\u7684 beta\u3001\u8d21\u732e\u4e0e\u6b8b\u5dee\u3002\u4ea7\u51fa\u5e93\u6bcf\u65e5 6 \u5bf9 \\(\\times\\) 3 \u7a97\u53e3 \\(\\times\\) 3 \u4f30\u8ba1\u91cf = 54 \u884c\u3002</p>

<h2>5. \u5f52\u56e0\u6052\u7b49\u5f0f</h2>
<p>\\[ c_{i,t} = \\beta_{i,t}\\, x_{i,t}, \\qquad r_t = y_t - \\sum_i c_{i,t}, \\qquad y_t = \\sum_i c_{i,t} + r_t . \\]</p>
<p>\u6052\u7b49\u5f0f\u6309\u6784\u9020\u7cbe\u786e\u6210\u7acb\uff0c\u6d4b\u8bd5\u5957\u4ef6\u4ee5\u6b64\u4f5c\u6b63\u786e\u6027\u68c0\u9a8c\u3002\u4e2d\u5fc3\u5316\u62df\u5408\u7684\u9690\u5f0f\u622a\u8ddd\u5e76\u5165\u6b8b\u5dee\uff1a\u65e5\u6536\u76ca\u4e0b\u5b83\u8fd1\u4e8e\u96f6\uff0c\u4e0d\u5355\u72ec\u5c55\u793a\u3002</p>
<p>\u8d21\u732e\u62a5\u4e09\u6866\u3002systematic \u662f DOLLAR_LOO \u4e0e CARRY_LOO \u4e4b\u548c\uff0c\u5373\u4e0e\u7f8e\u5143\u5927\u76d8\u5171\u4eab\u7684\u90e8\u5206\uff1bexogenous \u662f\u5176\u4f59\u56e0\u5b50\u4e4b\u548c\uff1bresidual \u662f\u4f59\u91cf\u3002\u53e6\u8ffd\u8e2a\u4e24\u6761\u7a97\u53e3 \\(R^2\\)\uff1a\u5168\u56e0\u5b50\u7684 r2_full\uff0c\u4e0e\u5254\u9664\u4e24\u4e2a\u5185\u90e8\u56e0\u5b50\u91cd\u4f30\u7684 r2_exog\u3002r2_exog \u53ea\u505a\u76d1\u63a7\uff0c\u4e0d\u8fdb\u5f52\u56e0\u4e0d\u53c2\u4e0e\u53c2\u6570\u9009\u62e9\u3002</p>

<h2>6. \u5f02\u5e38\u5206\u6570\u4e0e\u89e6\u53d1</h2>
<p>\\[ z_t = \\frac{r_t}{\\hat\\sigma_{t-1}}, \\qquad \\hat\\sigma_{t-1} = \\mathrm{sd}\\big(r_{t-126}, \\ldots, r_{t-1}\\big), \\] \u5c3a\u5ea6\u6ede\u540e\u4e00\u65e5\u3002\u89e6\u53d1\u6761\u4ef6\uff1a\\( |z_t| \\ge 2 \\) \u4e14 \\( |r_t| \\ge 50 \\) bp\uff0c\u6bcf\u65e5\u6700\u591a\u4e09\u5bf9\u3002\u7edd\u5bf9\u4e0b\u9650\u9632\u7684\u662f\u5b89\u9759\u671f\u5185 z \u5206\u6570\u628a\u566a\u58f0\u6807\u6210\u5f02\u5e38\u300216 \u5e74\u6837\u672c\u4e0a\u8054\u5408\u89c4\u5219\u5168\u76d8\u7ea6\u6bcf 4.5 \u81f3 5 \u4e2a\u4ea4\u6613\u65e5\u89e6\u53d1\u4e00\u6b21\uff0c\u9891\u7387\u7531\u9605\u8bfb\u8d1f\u8377\u800c\u975e\u7edf\u8ba1\u5224\u636e\u5b9a\u4e0a\u9650\u3002</p>

<h2>7. Canonical \u53e3\u5f84</h2>
<p>\u51e1\u53ea\u80fd\u7528\u4e00\u4e2a\u6570\u7684\u5730\u65b9\uff0c\u8bfb\u7684\u90fd\u662f 126 \u65e5\u7a97\u53e3\u7684 OLS\uff1a\u65b0\u95fb\u89e6\u53d1\u5668\u3001API \u9ed8\u8ba4\u503c\u3001FX \u9875\u3002OLS \u62c5\u6b64\u89d2\u8272\u56e0\u5176\u96f6\u8c03\u53c2\u3001\u659c\u7387\u65e0\u504f\u3002Ridge \u4e0e post-Lasso \u7ed9 canonical \u8bfb\u6570\u4f5c\u6ce8\uff1a\u4e09\u8def\u4e00\u81f4\uff0c\u8bfb\u6570\u6210\u7acb\uff1bRidge \u5206\u6b67\uff0c\u5171\u7ebf\u6027\u5728\u626d\u66f2\u5355\u56e0\u5b50\u5f52\u5c5e\uff08\u603b\u91cf\u5728\u6bcf\u8def\u4ecd\u95ed\u5408\uff09\uff1bLasso \u8e22\u6389\u67d0\u4e2a OLS \u91cd\u8d4f\u7684\u56e0\u5b50\uff0c\u90a3\u4efd\u8d21\u732e\u53ef\u80fd\u6765\u81ea\u76f8\u5173\u90bb\u5c45\u3002</p>

<h2>8. \u7a33\u5065\u6027\u68c0\u67e5</h2>
<p>\u4e0a\u8ff0\u5bf9\u7167\u81ea\u52a8\u8fd0\u884c\u3002\u4e09\u79cd\u76d1\u63a7\u5404\u7ba1\u4e00\u4e2a\u95ee\u9898\uff1a\u5065\u5eb7\u68c0\u67e5\u770b\u89e3\u91ca\u529b\u6c34\u5e73\uff0cbenchmark \u5bf9\u7167\u770b\u4e0e\u51bb\u7ed3\u5386\u53f2\u57fa\u7ebf\u7684\u4e00\u81f4\u6027\uff0c\u7a33\u5065\u6027\u68c0\u67e5\u770b\u540c\u4e00\u5929\u7684\u5f52\u56e0\u5728\u4e0d\u540c\u4f30\u8ba1\u91cf\u4e0b\u662f\u5426\u7ad9\u5f97\u4f4f\u3002</p>
<p>\u5ea6\u91cf\u662f\u4e09\u6866\u5206\u89e3\u7684 L1 \u8ddd\u79bb\uff08bp \u7a7a\u95f4\uff09\uff0cOLS \u5bf9 Ridge \u4e0e OLS \u5bf9 post-Lasso \u5206\u5f00\u7b97\uff08\u4e24\u8005\u5206\u6b67\u539f\u56e0\u4e0d\u540c\uff1a\u5171\u7ebf\u4e0b\u7684\u7cfb\u6570\u4e0d\u7a33\uff1b\u56e0\u5b50\u9009\u62e9\u53d8\u5316\uff09\uff0c\u6c38\u4e0d\u5408\u5e76\u3002\u5404\u9664\u4ee5\u8be5\u5bf9\u8fd1 252 \u4ea4\u6613\u65e5 |residual| \u4e2d\u4f4d\u6570\uff08\u6ede\u540e\u4e00\u65e5\uff09\u3002\u8fde\u7eed\u4e24\u65e5\u8d85\u8fc7\u8be5\u5bf9\u81ea\u8eab\u6eda\u52a8 95 \u5206\u4f4d\u624d\u8fdb\u5165\u72b6\u6001\uff0c\u8fde\u7eed\u4e24\u65e5\u56de\u7ebf\u624d\u9000\u51fa\uff0c\u5355\u65e5\u5c16\u5cf0\u7ffb\u4e0d\u52a8\u5fbd\u6807\u3002</p>
<p>\u56db\u4e2a\u72b6\u6001\uff1a\u4e09\u8def\u4e00\u81f4\u3001Ridge \u504f\u79bb\u3001Lasso \u6362\u56e0\u5b50\u3001Lasso \u5f03\u6743\u3002\u5f03\u6743\uff08Lasso \u4e00\u4e2a\u56e0\u5b50\u4e0d\u7559\uff09\u5355\u72ec\u6210\u7c7b\u5e76\u9644\u5f53\u524d\u5f03\u6743\u6bb5\u5929\u6570\uff1a\u5355\u65e5\u5f03\u6743\u662f\u4e00\u6b21\u9009\u62e9\u7ed3\u679c\uff0c\u4e94\u5341\u5929\u8fde\u7eed\u5f03\u6743\u8bf4\u660e\u56e0\u5b50\u83dc\u5355\u5728\u8be5\u65f6\u671f\u6491\u4e0d\u4f4f\u8fd9\u4e00\u5bf9\u3002\u5f03\u6743\u65e5\u5254\u51fa\u5206\u4f4d\u6837\u672c\uff08\u5176\u8ddd\u79bb\u662f\u6784\u9020\u7269\u800c\u975e\u4e24\u4e2a\u8bfb\u6570\u7684\u5206\u6b67\uff09\uff0c\u4f46\u5fbd\u6807\u7167\u5e38\u663e\u793a\u3002\u5fbd\u6807\u662f\u8bca\u65ad\u4e0d\u662f\u544a\u8b66\uff0c\u4e0d\u8fdb status \u989c\u8272\u3002\u5168\u6837\u672c\u6821\u51c6\u4e0b\u6bcf\u5bf9\u5904\u4e8e\u8ddd\u79bb\u6001\u7684\u65e5\u5b50\u5360 5.9 \u81f3 7.2%\uff0c\u4ee5\u4e2d\u4f4d 2 \u81f3 3 \u5929\u7684\u4e8b\u4ef6\u6bb5\u51fa\u73b0\u3002</p>

<h2>9. PCA \u76d1\u63a7</h2>
<p>\u516d\u5bf9\u76f8\u5173\u77e9\u9635\u7684\u6eda\u52a8 PCA \u56de\u7b54\u4e00\u4e2a\u95ee\u9898\uff1a\u662f\u7f8e\u5143\u5927\u76d8\u5728\u52a8\uff0c\u8fd8\u662f\u5355\u4e2a\u8d27\u5e01\u5728\u52a8\u3002\u4e24\u9053\u8b66\u6212\uff1aPC1 \u4e0e\u7f8e\u5143\u56e0\u5b50\u7684\u76f8\u5173\uff08\u4f4e\u4e8e 0.9 \u62a5\u8b66\uff09\uff0c\u5229\u5dee\u56e0\u5b50\u5bf9 span{PC2, PC3} \u5e73\u9762\u7684\u6295\u5f71 \\(R^2\\)\uff08\u4f4e\u4e8e 0.6 \u62a5\u8b66\uff09\u3002\u6295\u5f71\u53d6\u4ee3\u5355\u4e00 PC2 \u76f8\u5173\uff0c\u56e0\u5355\u4e2a\u4e3b\u6210\u5206\u975e\u65cb\u8f6c\u4e0d\u53d8\uff1a\u7279\u5f81\u503c\u63a5\u8fd1\u65f6\u5e73\u9762\u7a33\u5b9a\u800c\u5176\u5185\u57fa\u53ef\u4efb\u610f\u65cb\u8f6c\u3002PCA \u4e0d\u4ea7\u51fa\u5f52\u56e0\u6570\u5b57\uff0c\u4e3b\u6210\u5206\u4e0d\u6302\u7ecf\u6d4e\u6807\u7b7e\uff1a\u300cPC2 \u8d21\u732e 40 bp\u300d\u4f1a\u628a\u4f9d\u8d56\u57fa\u9009\u62e9\u7684\u4eba\u5de5\u7269\u53d8\u6210\u7ecf\u6d4e\u65ad\u8a00\u3002</p>

<h2>10. \u65b0\u95fb</h2>
<p>\u65b0\u95fb\u7ecf\u4e24\u6761\u5206\u5f00\u7684\u901a\u9053\u8fdb\u5165\uff0c\u9700\u6c42\u4e0d\u540c\uff1a\u5c55\u793a\u901a\u9053\u8981\u53ef\u8bfb\uff0c\u53d9\u4e8b\u901a\u9053\u8981\u5b8c\u6574\uff0c\u4e24\u8005\u4e0d\u5171\u7528\u8fc7\u6ee4\u5668\u3002</p>
<p><strong>\u5c55\u793a\u901a\u9053\u3002</strong>\u5934\u6761\u680f\u6bcf\u534a\u5c0f\u65f6\u6309\u5404\u5bf9\u68c0\u7d22\u8bcd\u67e5 Google News RSS\uff0c\u539f\u6837\u5c55\u793a\u3002\u89c4\u5219\u5206\u7c7b\u5668\u628a\u6807\u9898\u5206\u4e3a\u4e8b\u4ef6\u4e0e\u89c2\u70b9\uff1a\u542b Opinion\u3001Analysis\u3001Commentary\u3001Setups\u3001Outlook\u3001Price Action\u3001Forecast \u7b49\u6807\u8bb0\u8bcd\u3001\u6280\u672f\u4f4d\u8bcd\u6c47\uff08support \u77ed\u8bed\u3001resistance\u3001key levels\u3001pivot\u3001retracement\u3001fibonacci\uff09\u6216\u4ee5\u95ee\u53f7\u7ed3\u5c3e\u7684\uff0c\u6298\u53e0\u6536\u8d77\u3002\u8bcd\u8868\u6309\u5185\u5bb9\u4e0d\u6309\u6765\u6e90\uff0c\u96c6\u4e2d\u5728\u4ee3\u7801\u4e00\u5904\u53ef\u5ba1\u8ba1\u3002\u88ab\u5df2\u53d1\u5e03\u77ed\u8bc4\u5f15\u7528\u7684\u62a5\u9053\u65e0\u8bba\u7c7b\u578b\u7559\u5728\u4e3b\u5217\u5e76\u6807\u5df2\u5f15\u7528\uff1a\u90a3\u662f\u77ed\u8bc4\u7684\u8bc1\u636e\u3002</p>
<p><strong>\u53d9\u4e8b\u901a\u9053\u3002</strong>pair-day \u89e6\u53d1\u540e\uff0c\u7cfb\u7edf\u5206\u4e24\u6b21\u68c0\u7d22\u5f53\u65e5\u65b0\u95fb\uff1a\u5f53\u65e5\u4e0e\u524d\u4e00\u65e5\u62a5\u9053\u5728\u524d\uff0c\u4e8b\u540e\u590d\u76d8\u5728\u540e\uff0c\u9010\u6761\u6807\u6ce8\u9636\u6bb5\u3002\u68c0\u7d22\u8bcd\u53ea\u5199\u8d27\u5e01\u4e0e\u5176\u592e\u884c\uff0c\u4e0d\u5e26\u56e0\u679c\u63d0\u95ee\uff1a\u9884\u8bbe\u56e0\u679c\u7684\u68c0\u7d22\u62c9\u56de\u7684\u662f\u5370\u8bc1\u3002\u68c0\u7d22\u5230\u7684\u5168\u90e8\u7559\u6863\uff0c\u65e0\u8bba\u662f\u5426\u88ab\u5f15\u7528\u3002\u5c55\u793a\u5c42\u5206\u7c7b\u5668\u4e0d\u78b0\u8fd9\u6761\u901a\u9053\uff1a\u4e8b\u540e\u5206\u6790\u5728\u6b64\u662f\u6709\u7528\u8bc1\u636e\uff0c\u5b9e\u5f39\u6848\u4f8b\u91cc\u51e0\u7bc7 Analysis \u63d0\u4f9b\u4e86\u5e72\u9884\u91cf\u7ea7\u7684\u4f30\u8ba1\u3002</p>
<p><strong>\u8bed\u8a00\u6a21\u578b\u505a\u4ec0\u4e48\u3002</strong>Gemini \u53ea\u5728\u89e6\u53d1\u540e\u88ab\u8c03\u7528\uff0c\u6bcf\u65e5\u81f3\u591a\u51e0\u6b21\uff0c\u53d7\u6210\u672c\u4e0a\u9650\u7ea6\u675f\u3002\u5b83\u6536\u5230\u4e8b\u5b9e\u8868\uff08\u5f53\u65e5\u6570\u5b57\u7684\u56fa\u5b9a\u5b57\u7b26\u4e32\u300121 \u4e0e 252 \u4ea4\u6613\u65e5\u8d8b\u52bf\u4e0a\u4e0b\u6587\u3001\u5217\u660e\u6a21\u578b\u80fd\u627f\u8f7d\u54ea\u4e9b\u56e0\u5b50\u7684\u8986\u76d6\u884c\u3001\u7a33\u5065\u6027\u72b6\u6001\u3001\u82e5\u6709\u5219\u52a0\u4e0a\u4e00\u6b21\u89e6\u53d1\u65e5\uff09\u4e0e\u68c0\u7d22\u6765\u6e90\uff0c\u8fd4\u56de\u4e09\u6bb5\u7ed3\u6784\u5316\u8f93\u51fa\uff1a\u53d1\u751f\u4e86\u4ec0\u4e48\uff0c\u6a21\u578b\u4e3a\u4f55\u89e3\u91ca\u4e0d\u4e86\uff0c\u4ec0\u4e48\u80fd\u68c0\u9a8c\u8fd9\u4e2a\u8bfb\u6cd5\uff1b\u5916\u52a0\u56db\u6001 assessment\uff1ano relevant reporting\u3001does not account for\u3001partially accounts for\u3001accounts for\u3002does not account for \u7167\u5e38\u53d1\u5e03\uff1a\u5927\u6b8b\u5dee\u914d\u4e0a\u627f\u8f7d\u4e0d\u4e86\u5b83\u7684\u62a5\u9053\uff0c\u662f\u5b8c\u6574\u7ed3\u8bba\u800c\u975e\u5931\u8d25\u3002</p>
<p><strong>\u5b83\u4e0d\u505a\u4ec0\u4e48\u3002</strong>\u4e0d\u68c0\u7d22\uff08\u68c0\u7d22\u5728\u8c03\u7528\u524d\u5b8c\u6210\uff09\uff0c\u4e0d\u7b97\u6570\uff08\u6240\u6709\u6570\u5b57\u4ee5\u5b57\u7b26\u4e32\u7ed9\u5165\uff09\uff0c\u4e0d\u9884\u6d4b\uff0c\u4e0d\u51b3\u5b9a\u89e6\u53d1\u3002</p>
<p><strong>\u6821\u9a8c\u3002</strong>\u516d\u9053\u673a\u68b0\u68c0\u67e5\u9010\u7a3f\u8fd0\u884c\uff1a\u5f15\u7528\u6765\u6e90\u5fc5\u987b\u51fa\u81ea\u68c0\u7d22\u96c6\uff1b\u5f15\u7528\u65e5\u671f\u5fc5\u987b\u843d\u5728\u4e8b\u4ef6\u7a97\u53e3\u5185\uff1b\u6bcf\u4e2a\u6570\u5b57\u5fc5\u987b\u5728\u4e8b\u5b9e\u8868\u6216\u88ab\u5f15\u6765\u6e90\u4e2d\u9010\u5b57\u51fa\u73b0\uff1b\u7981\u56e0\u679c\u65ad\u8a00\uff1b\u7981\u65b9\u5411\u9884\u6d4b\uff1b\u53cc\u8bed\u7248\u672c\u5fc5\u987b\u5f15\u7528\u540c\u4e00\u6279\u6765\u6e90\u3002\u4e00\u9053\u4e0d\u8fc7\u6574\u7a3f\u4e22\u5f03\uff0c\u5b8c\u6574\u8bb0\u5f55\u4fdd\u7559\u3002</p>
<p>\u9875\u9762\u4e0a\u7684\u62a5\u9053\u662f\u6302\u5728\u89e6\u53d1\u65e5\u4e0a\u7684\u8bc1\u636e\uff1a\u65e5\u671f\u3001\u8d27\u5e01\u5bf9\u3001\u8be5\u65e5\u6b8b\u5dee\u3002\u4e0d\u628a\u6b8b\u5dee\u7684\u4efb\u4f55\u4efd\u989d\u5206\u7ed9\u67d0\u6761\u62a5\u9053\uff0c\u5f53\u65e5\u6b8b\u5dee\u6bcf\u65e5\u53ea\u663e\u793a\u4e00\u6b21\u800c\u975e\u6302\u5728\u6bcf\u6761\u62a5\u9053\u65c1\uff1a\u5355\u7bc7\u4efd\u989d\u4e0d\u53ef\u6d4b\uff0c\u6302\u5728\u6807\u9898\u65c1\u7684\u6570\u5b57\u4f1a\u88ab\u8bfb\u6210\u56e0\u679c\u3002</p>

<h2>\u9644\u5f55</h2>
<p><strong>A. \u8fd0\u884c\u671f\u4fdd\u969c\u3002</strong>live \u8fd0\u884c\u5e42\u7b49\uff1a\u91cd\u8dd1\u540c\u4e00\u65e5\u671f\u4e0d\u4ea7\u751f\u91cd\u590d\u884c\u3002\u7ec8\u5c40\u884c\u6c38\u4e0d\u6539\u52a8\uff1bprovisional \u884c\u4ec5\u5728\u8f93\u5165 as-of \u524d\u8fdb\u65f6\u8986\u5199\uff0c\u7559\u5ba1\u8ba1\u3002\u4e0b\u6e38\u6d88\u8d39\u8005\uff08\u542b\u672c\u7ad9\uff09\u8bfb\u5e26\u7248\u672c\u53f7\u7684\u4ea7\u51fa\u6587\u4ef6\uff0c\u4e0d\u91cd\u7b97\u4efb\u4f55\u6570\u5b57\u3002</p>
<p><strong>B. \u7a33\u5065\u6027\u68c0\u67e5\u7684\u9a8c\u8bc1\u951a\u70b9\u3002</strong>16 \u5e74\u6837\u672c\u7684\u8ddd\u79bb\u5c3e\u90e8\u4e0e\u5df2\u77e5\u4e8b\u4ef6\u5bf9\u5f97\u4e0a\uff1a2020-03 \u5168\u76d8\u30012011 \u4e0e 2022 \u7684\u65e5\u5143\u5e72\u9884\u30012024 \u5957\u606f\u5e73\u4ed3\u30022025-04-07 \u5f53\u65e5\u4e94\u5bf9\u4e09\u8def\u4e00\u81f4\u3001Ridge \u8ddd\u79bb\u5168\u90e8\u5fae\u5c0f\uff0c\u552f\u4e00\u6302\u724c\u662f\u514b\u6717\u5728\u6cb9\u4ef7\u53d8\u52a8\u4e2d\u7684 Lasso \u6362\u56e0\u5b50\u6bb5\u3002\u8be5\u5ea6\u91cf\u54cd\u5e94\u8bfb\u6570\u4e0d\u7a33\uff0c\u4e0d\u54cd\u5e94\u5927\u5e45\u6ce2\u52a8\u3002</p>
<p><strong>C. \u5df2\u79fb\u9664\u8bbe\u8ba1\u4e0e\u4e8b\u6545\u3002</strong>\u65e9\u5148\u7248\u672c\u628a\u89e6\u53d1\u65e5\u6b8b\u5dee\u5728\u88ab\u5f15\u7528\u62a5\u9053\u95f4\u5747\u5206\u5e76\u5c55\u793a\u5355\u7bc7\u767e\u5206\u6bd4\uff0c\u5df2\u79fb\u9664\uff1a\u5747\u5206\u662f\u4ee5\u6d4b\u91cf\u5916\u89c2\u5448\u73b0\u7684\u7ea6\u5b9a\uff0c\u4e14\u5728\u6700\u5e38\u89c1\u7684\u5355\u89e6\u53d1\u65e5\u9000\u5316\u4e3a\u5168\u5458\u540c\u5360\u6bd4\u3002\u65e9\u5148\u5fc3\u8df3\u4ee5\u6700\u8fd1\u4e00\u7bc7\u77ed\u8bc4\u8ba1\u65f6\uff0c\u5df2\u6539\u4e3a\u4ee5\u6700\u8fd1\u4e00\u6b21\u5b8c\u6210\u8fd0\u884c\u8ba1\u65f6\uff1a\u5b89\u9759\u5468\u662f\u5e38\u6001\uff0c\u957f\u671f\u5904\u4e8e\u89e6\u53d1\u6001\u7684\u544a\u8b66\u4f1a\u88ab\u5ffd\u7565\u30022026 \u5e74 8 \u6708\u4e00\u6839\u672a\u6536\u76d8 bar \u4ee5\u7ec8\u5c40\u884c\u5165\u5e93\uff0c\u7b2c 2 \u8282\u7684\u524d\u6cbf provisional \u89c4\u5219\u4e0e\u6536\u76d8\u65f6\u949f\u5224\u636e\u7531\u6b64\u800c\u6765\u3002</p>
<p><strong>D. \u5df2\u77e5\u9650\u5236\u4e0e\u6e90\u7684\u7ec6\u8282\u3002</strong>RSS \u65e5\u671f\u53ea\u6709\u5929\u7ea7\u7cbe\u5ea6\u4e14\u6309 GMT \u8bb0\uff0c\u96f6\u70b9\u9644\u8fd1\u53ef\u80fd\u504f\u4e00\u5929\u3002\u5c55\u793a\u5206\u7c7b\u5668\u662f\u8bcd\u8868\uff0c\u65e0\u6807\u8bb0\u8bcd\u7684\u884c\u60c5\u590d\u8ff0\u4f1a\u6f0f\u8fc7\u3002Lasso \u7a7a\u9009\u62e9\u96c6\u4e2d\u5728\u65e5\u5143\u4f4e\u6ce2\u52a8\u671f\uff0c\u4e0e\u5176\u5728\u7f8e\u5143\u56e0\u5b50\u4e0a\u7684\u5f31\u8f7d\u8377\u4e00\u81f4\u3002\u6e90\u7ec6\u8282\uff1a\u5fb7\u56fd\u8054\u90a6\u94f6\u884c\u5047\u65e5\u884c\u503c\u5217\u5199\u70b9\u53f7\u5e76\u9644\u5907\u6ce8\uff0c\u6574\u884c\u5254\u9664\uff1b\u65e5\u672c\u8d22\u52a1\u7701\u5386\u53f2\u6587\u4ef6\u5728\u5b50\u76ee\u5f55\uff0c\u5f53\u6708\u8986\u76d6\u5386\u53f2\uff0c\u7f3a\u5931\u8bb0\u77ed\u6a2a\u7ebf\uff1b\u632a\u5a01\u592e\u884c API \u5bf9\u65e0\u6570\u636e\u533a\u95f4\u8fd4\u56de 404 \u800c\u975e\u7a7a\u7ed3\u679c\uff0c\u6309\u7a7a\u5904\u7406\uff1bRBA \u6587\u4ef6\u6570\u636e\u524d\u6709\u5341\u4e00\u884c\u5143\u6570\u636e\uff0c\u9700\u6d4f\u89c8\u5668 UA\u3002</p>
</article>`;
}

export function methodologyHtml() {
  return getLang() === "zh" ? zh() : en();
}
