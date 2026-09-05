// Reader-facing account of the implemented model. Equations use local MathJax.
// Figures share their source with the standalone SVGs linked from the README.
import { getLang } from './i18n.js';
import { methodologyFigure } from './methodology-figures.js';

function figure(name, lang, number, caption) {
  const open = lang === 'zh' ? '查看完整图' : 'Open full figure';
  const scroll = lang === 'zh' ? '可横向滚动查看图示' : 'Scrollable diagram';
  return `<figure class="method-figure">
    <div class="method-figure__scroll" tabindex="0" role="region" aria-label="${scroll}">${methodologyFigure(name, lang)}</div>
    <figcaption><span class="method-figure__number">${number}</span><span>${caption}</span>
      <a href="figures/${name}-${lang}.svg" target="_blank" rel="noopener">${open} ↗</a></figcaption>
  </figure>`;
}

function en() {
  return String.raw`<article class="method" lang="en">
<p class="method__back"><a href="#/attribution">← Attribution</a></p>
<header class="method__intro">
  <p class="method__eyebrow">Research notes / Methodology</p>
  <h1>Reading a daily FX move</h1>
  <p class="lede">The dashboard splits a currency's daily return into contributions from a fixed set of factors and a residual. This page follows the calculation from market closes to the numbers in the Attribution view.</p>
  <p class="method__meta">Six USD pairs <span>Daily log returns</span><span>Default: OLS, 126 days</span></p>
  <p class="method__meta">Calculation revision: 2026-09-04.fold-local-cv-pca</p>
</header>

<section class="method__section">
<h2><span>01</span> The return we measure</h2>
<p>All six pairs use USD/XXX quotes: EUR, JPY, CAD, NOK, AUD and MXN. A rise means a stronger dollar. EUR/USD and AUD/USD feeds are inverted before returns are calculated.</p>
<p>\[ y_t = \ln(P_t/P_{t-1}) \]</p>
<p>Attribution describes a completed trading day. The factors are aligned to the same market event time as that return. Multiplying a log return by 10,000 gives the basis point units used in the attribution tables. This closely approximates the percentage price change for small moves.</p>
${figure('pipeline','en','01','Each estimator produces a separate daily record. The three coloured groups are the contribution categories used on the FX page.')}
</section>

<section class="method__section">
<h2><span>02</span> Building the factor panel</h2>
<p>The dollar factor measures the return shared with other currencies. For pair \(p\), it takes the equal weighted mean of available returns from the other five pairs:</p>
<p>\[ D_t^{(-p)}=\frac{1}{|A_{p,t}|}\sum_{q\in A_{p,t}} y_t^{(q)}, \qquad A_{p,t}=\{q:q\ne p,\ y_t^{(q)}\text{ available}\}. \]</p>
<p>With all five observations present, the denominator is five. Carry takes the mean return of a fixed low yield group, JPY and EUR, less the mean return of a high yield group, MXN and AUD. The explained pair is removed from its group. The calculation uses available observations within each group.</p>
<p>Every pair also uses changes in short and long US minus foreign yield differentials and the daily change in VIX. The remaining baseline factors are specific to each pair:</p>
<div class="tblwrap"><table><caption>Baseline additions and matched yield maturities</caption>
<thead><tr><th scope="col">Pair</th><th scope="col">Additional factors</th><th scope="col">Short / long</th></tr></thead>
<tbody><tr><td>USD/EUR</td><td>Common core only</td><td>2Y / 10Y</td></tr>
<tr><td>USD/JPY</td><td>Gold</td><td>2Y / 10Y</td></tr>
<tr><td>USD/CAD</td><td>WTI crude</td><td>2Y / 10Y</td></tr>
<tr><td>USD/NOK</td><td>Brent crude</td><td>3Y / 10Y</td></tr>
<tr><td>USD/AUD</td><td>Copper, gold</td><td>2Y / 10Y</td></tr>
<tr><td>USD/MXN</td><td>EMB return</td><td>1Y / derived long yield</td></tr></tbody></table></div>
<p>Lasso can also select <code>HY_EXCESS</code>, the adjusted log return of HYG less IEI, and <code>dHY_OAS</code>, the change in the US high yield spread. AUD adds only HY_EXCESS. The candidate menu contains at most eight factors.</p>
<p>The two yield legs are aligned separately, subtracted, then differenced over time. Norway's short leg is matched to US 3Y; Mexico's CETES short leg is matched to US 1Y. Mexico's long yield is derived from an official bond price, coupon and remaining maturity series and checked against an official monthly series. Commodity and ETF inputs use log returns; yield, VIX and spread inputs use differences.</p>
</section>

<section class="method__section">
<h2><span>03</span> Fitting the coefficients</h2>
<p>Each day's coefficients use the previous 63, 126 or 252 trading observations. For day \(t\), the window is \([t-w,t-1]\). Keeping that day's move outside the fit prevents a large observation from changing the coefficients used to account for it.</p>
${figure('timeline','en','02','The window length controls how much history informs the coefficient. The contribution itself uses the factor move on day t.')}
<p>Within the window, each factor is centred and divided by its standard deviation; the FX return is centred too. After fitting, the slopes are converted back to the factor's original units. OLS uses the baseline set. Ridge uses the same set with a penalty that shrinks the coefficients. Lasso uses the candidate menu to select columns, then OLS estimates new slopes on the retained columns.</p>
${figure('lasso','en','03','The retained variables here are illustrative. The selection and the refit use the same historical window.')}
<p>Ridge and Lasso each choose their penalty using three forward time series splits within the window. They search a 25 point logarithmic grid, initially from \(10^{-4}\) to \(10^4\), with expansion at a boundary. Penalties are reselected every 21 trading observations; coefficients are refitted daily. Each estimator retains its own contribution series.</p>
<p>Each split learns the factor means, standard deviations and return mean from its training fold. Validation observations use those same values. The final daily fit then uses the full historical window.</p>
<details class="method__detail"><summary>Estimation equations</summary>
<p>Let \(Z\) be the standardised factor matrix and \(y_c\) the centred return. The solvers use:</p>
<p>\[ \begin{aligned}
\hat b_{\mathrm{OLS}}&=\arg\min_b\|y_c-Zb\|_2^2,\\
\hat b_{\mathrm{Ridge}}&=(Z^\top Z+\lambda I)^{-1}Z^\top y_c,\\
\hat b_{\mathrm{Lasso}}&=\arg\min_b\left\{\frac{1}{2n}\|y_c-Zb\|_2^2+\lambda\|b\|_1\right\}.
\end{aligned} \]</p>
<p>For Lasso, \(S=\{i:\hat b_i\ne0\}\) supplies the columns for an unpenalised OLS refit. In every case, the final coefficient in original units is \(\beta_i=b_i/\sigma_i\). An empty selection gives zero factor contributions and assigns the entire daily return to the residual.</p>
</details>
</section>

<section class="method__section">
<h2><span>04</span> From coefficients to contributions</h2>
<p>\[ c_{i,t}=\beta_{i,t}x_{i,t},\qquad r_t=y_t-\sum_i c_{i,t}. \]</p>
<p>A positive contribution points towards dollar strength; a negative contribution points towards dollar weakness. Opposing contributions can offset one another. The fitted intercept is included in the residual. The accounting identity closes by construction, so closure alone tells us nothing about the model's explanatory power.</p>
<p>The FX page groups dollar and carry as systematic, all remaining factors as exogenous, and the remainder as residual. The Attribution page separates the exogenous group into rates, risk and commodities. Choose 1, 5 or 21 trading observations to change the return period; the training window is a separate setting. Period totals include any provisional observations and carry a note when those are present. Open a pair to inspect individual contributions, coefficient history, training fit and residual z scores. Lasso also shows its selection history.</p>
<p><code>r2_full</code> describes the fit inside the training window. <code>r2_exog</code> comes from refitting that estimator after removing dollar and carry. It measures the fit of the external variables on their own. Both are sample fit statistics; neither measures the fraction of a particular day's move explained.</p>
<p>The FX card's three percentages use absolute contribution sizes as their denominator. Read their signs alongside the percentages. A small net move can contain sizeable contributions in opposite directions.</p>
</section>

<section class="method__section">
<h2><span>05</span> Comparing the estimates</h2>
<p>OLS at 126 days is the default for the FX page and the news trigger. The agreement badge compares its three contribution groups with Ridge and with the OLS refit after Lasso selection. For each comparison, the absolute differences are added and divided by the pair's median absolute OLS residual over the preceding 252 trading records.</p>
<p>A distance state starts after two consecutive days above the pair's trailing 95th percentile and ends after two consecutive days back below the threshold. Ridge divergence and Lasso reselection can appear together. Lasso abstention is shown separately with the length of the current run; those dates are excluded from the Lasso distance quantile sample. Insufficient history is reported as unavailable.</p>
<p>Agreement describes sensitivity to the estimator. A stable allocation can still omit relevant variables. The badge leaves the system health colour unchanged.</p>
<p>The research page compares saved OLS, Ridge and post-Lasso results on dates that are final and available for all three. It shows residual MAE and RMSE, absolute factor-allocation differences from OLS, and Lasso selection changes for the latest 252 shared observations and full shared history. Smaller residuals indicate closer reconstruction on those dates. Realised factors and revised historical data enter this comparison, so it does not establish forecast performance. Lasso also uses a wider factor menu.</p>
<details class="method__detail"><summary>PCA and model health</summary>
<p>Rolling PCA tracks the common structure of the six currency returns. The monitor records the absolute correlation between PC1 and dollar, the absolute correlation between PC2 and carry, and the projection \(R^2\) of carry onto the span of PC2 and PC3. Their reference thresholds in the current implementation are 0.9, 0.6 and 0.5 respectively. The older PC2 correlation flag is still recorded.</p>
<p>The projection measures how closely carry lies within that two dimensional space as the individual components rotate. PCA supplies monitoring statistics. The named dollar, carry and market factors supply the attribution.</p>
<p>The PCA eigensystem comes from the return correlation matrix. Component scores and projections use centred, standardised returns so that they share the eigensystem's units.</p>
<p>Model health checks compare rolling fit with the pair's own history and the other pairs. A separate heartbeat records when the pipeline last completed successfully. These checks help distinguish a change in fit from a missed run.</p>
</details>
</section>

<section class="method__section">
<h2><span>06</span> Residuals and the news note</h2>
<p>The residual is scaled by its own standard deviation over the preceding 126 trading records:</p>
<p>\[ z_t=\frac{r_t}{\mathrm{sd}(r_{t-126},\ldots,r_{t-1})}. \]</p>
<p>The narrative trigger requires both \(|z_t|\ge2\) and \(|r_t|\ge50\) bp. At most three pairs are selected on a date, ranked by absolute z score. The absolute floor limits writeups of small moves during quiet periods.</p>
<p>Google News RSS supplies reporting around the trigger date. Gemini receives those sources, the daily attribution, recent fit and residual statistics, and any previous note for that pair. It writes about the reporting, the model's coverage of the event, and observations that could help assess the explanation. Publication dates and event dates are treated separately.</p>
<p>Six checks cover retrieved source IDs, source dates, exact numeric strings, causal claims, directional forecasts and matching citations in English and Chinese. Failed drafts are kept for review. These checks can catch formal errors; assessing whether the reporting supports the interpretation still requires judgment.</p>
<p>The residual belongs to the currency and trading day. News links provide context for that observation. A story's individual contribution remains unmeasured.</p>
<p>The daily headline feed is available independently of narrative triggers. The local service caches it for 30 minutes. The public site captures it when the page is built and displays the fetch time.</p>
<p>A separate panel starts from the two largest factor contributions and retrieves factor-related reporting alongside a currency-context search. It retains exclusion reasons for explicit quote pages and unrelated institutional-name matches. These links are reading leads, with no new causal judgment. News publication dates have day precision; observation timestamps record the actual retrieval time.</p>
<p>The weekday text edition starts collecting at 08:50 America/New_York and freezes its input packet before 09:00. A separate model call can add source-linked event context for up to three large currency moves. Numbers, leave-one-out definitions and an evidence-checking plan are printed by code. The model reads RSS titles and snippets; its interpretation still needs scrutiny. Free-form AI outlooks remain withheld after validation exposed unsupported policy inferences.</p>
<p>At 09:00 the job publishes from the saved packet. Missing or late inputs receive a dated notice; rejected commentary leaves a numeric summary. Publication retries reuse the frozen edition. Source ids, exact excerpts, dates, bilingual citations and wording are checked, with failed drafts retained. These checks do not prove causality or verify every paraphrase. Validation previews are labelled separately. The host must be running, and the first natural morning execution remains to be observed.</p>
<p>The News archive retains the latest twenty editions for reading. Preparation, saved text and delivery are shown separately. The browser checks dates against its current New York clock, and a static build identifies the edition it contains. An unreadable archive remains labelled; earlier text is available by date. These observations cannot confirm that the host is still running or reveal a failed push after the last build.</p>
</section>

<section class="method__section">
<h2><span>07</span> Sources and data revisions</h2>
<p>Prices and ETF series come from Yahoo Finance. FRED supplies US yields, VIX and credit spreads. Foreign yield series use the official sources below.</p>
${sources('en')}
<p>Acquisition tries the online source, the last successful cache, then a supplied local file. Fallbacks and missing observations are logged. Alignment is fixed by pair; joining and differencing take place on that pair's date index. The history begins in January 2010, with the first attribution appearing after the selected training window.</p>
<p>The latest panel row is provisional until later data confirms it. Publication delays, including RBA releases, can leave additional provisional rows. Local holidays can produce stale values with no later replacement. These cases have separate flags. Regular runs freeze completed history and record provisional replacements when source dates advance.</p>
<p>The published page is a snapshot. Its build time, the pipeline's last successful run and the narrative's last run are shown separately so the age of each stage remains visible.</p>
</section>
</article>`;
}

function sources(lang) {
  const zh = lang === 'zh';
  const rows = [
    ['EUR','Deutsche Bundesbank','https://www.bundesbank.de/en/statistics'],
    ['JPY',zh?'日本财务省':'Japan Ministry of Finance','https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/'],
    ['CAD','Bank of Canada Valet','https://www.bankofcanada.ca/valet/docs'],
    ['NOK','Norges Bank','https://www.norges-bank.no/en/topics/Statistics/'],
    ['AUD','Reserve Bank of Australia, F2','https://www.rba.gov.au/statistics/tables/'],
    ['MXN','Banxico SIE','https://www.banxico.org.mx/SieInternet/'],
  ];
  return `<div class="tblwrap"><table><caption>${zh?'外国收益率官方来源':'Official foreign yield sources'}</caption><thead><tr><th scope="col">${zh?'货币':'Currency'}</th><th scope="col">${zh?'发布机构':'Publisher'}</th></tr></thead><tbody>${rows.map(([p,n,u])=>`<tr><td>${p}</td><td><a href="${u}" target="_blank" rel="noopener">${n} ↗</a></td></tr>`).join('')}</tbody></table></div>`;
}

function zh() {
  return String.raw`<article class="method" lang="zh-CN">
<p class="method__back"><a href="#/attribution">← 归因</a></p>
<header class="method__intro">
  <p class="method__eyebrow">研究说明 / Methodology</p>
  <h1>一天的汇率变动，如何分解</h1>
  <p class="lede">仪表盘将每日汇率收益拆成各因子贡献与残差。这一页从数据开始，依次说明系数如何估计、贡献如何计算，以及归因页上的数字该怎样读。</p>
  <p class="method__meta">六组美元汇率 <span>日对数收益</span><span>默认：OLS，126 日</span></p>
  <p class="method__meta">计算版本：2026-09-04.fold-local-cv-pca</p>
</header>

<section class="method__section">
<h2><span>01</span> 收益的定义</h2>
<p>六组汇率统一使用 USD/XXX 报价，涵盖 EUR、JPY、CAD、NOK、AUD 和 MXN。数值上涨表示美元走强。原始 EUR/USD 与 AUD/USD 序列在计算收益前先取倒数。</p>
<p>\[ y_t=\ln(P_t/P_{t-1}) \]</p>
<p>归因针对已经结束的交易日，因子与汇率按有效市场时间对齐。归因表把对数收益乘以 10,000，以基点展示；变动较小时，这个数与价格涨跌幅换算的基点接近。</p>
${figure('pipeline','zh','01','每种估计量分别生成每日记录。底部三种颜色对应 FX 页的三组归因。')}
</section>

<section class="method__section">
<h2><span>02</span> 因子从哪里来</h2>
<p>美元因子衡量单个货币对与其他货币共享的变动。对货币对 \(p\)，从其余五对中取当日有观测值的收益，计算等权均值：</p>
<p>\[ D_t^{(-p)}=\frac{1}{|A_{p,t}|}\sum_{q\in A_{p,t}}y_t^{(q)},\qquad A_{p,t}=\{q:q\ne p,\ y_t^{(q)}\text{ available}\}. \]</p>
<p>五对数据齐全时，分母就是五。套息因子使用固定分组：低息组为 JPY 与 EUR，高息组为 MXN 与 AUD。两组各自剔除被解释的货币对，再用组内可得收益的均值作低减高。</p>
<p>所有货币对都加入短端、长端的美外利差变动，以及 VIX 的日变动。基础模型另外使用以下变量：</p>
<div class="tblwrap"><table><caption>各货币对的附加因子与期限匹配</caption>
<thead><tr><th scope="col">货币对</th><th scope="col">附加因子</th><th scope="col">短端 / 长端</th></tr></thead>
<tbody><tr><td>USD/EUR</td><td>仅使用公共基础因子</td><td>2Y / 10Y</td></tr>
<tr><td>USD/JPY</td><td>黄金</td><td>2Y / 10Y</td></tr>
<tr><td>USD/CAD</td><td>WTI 原油</td><td>2Y / 10Y</td></tr>
<tr><td>USD/NOK</td><td>Brent 原油</td><td>3Y / 10Y</td></tr>
<tr><td>USD/AUD</td><td>铜、黄金</td><td>2Y / 10Y</td></tr>
<tr><td>USD/MXN</td><td>EMB 收益</td><td>1Y / 派生长端收益率</td></tr></tbody></table></div>
<p>Lasso 的候选菜单还包含 <code>HY_EXCESS</code>，即 HYG 与 IEI 调整后收盘价的对数收益之差，以及美国高收益债利差变动 <code>dHY_OAS</code>。AUD 只增加 HY_EXCESS。每个货币对的候选因子最多八个。</p>
<p>利差的两条腿分别对齐，先算美国收益率减外国收益率，再计算时间差分。挪威短端使用 3Y，与美国 3Y 匹配；墨西哥短端使用一年期 CETES，与美国 1Y 匹配。墨西哥长端从官方债券价格、票息和剩余期限序列反推，并用官方月频数据核验。商品与 ETF 使用对数收益，利率、VIX 和利差使用一阶差分。</p>
</section>

<section class="method__section">
<h2><span>03</span> 用此前的窗口估计系数</h2>
<p>每天使用此前 63、126 或 252 个交易观测重新拟合。对 \(t\) 日，窗口为 \([t-w,t-1]\)。当天的大幅变动因此不会改变用来解释它的那组系数。</p>
${figure('timeline','zh','02','窗口长度决定系数参考多少历史。当天贡献使用 t 日的因子变动。')}
<p>窗口内，各因子减去自身均值并除以标准差，汇率收益也去均值。拟合结束后，将斜率换回原量纲。OLS 保留基础因子集；Ridge 在同一因子集上加入收缩惩罚；Lasso 从候选菜单选择列，再用 OLS 对保留列重新估计斜率。</p>
${figure('lasso','zh','03','图中的保留变量仅作示意。变量选择与重拟合使用同一个历史窗口。')}
<p>Ridge 与 Lasso 分别在窗口内进行三折顺时交叉验证，选择惩罚强度。初始搜索网格含 25 个对数间隔点，范围为 \(10^{-4}\) 至 \(10^4\)，命中边界时扩展。惩罚参数每 21 个交易观测重选一次，系数每日重拟合。每条模型路径分别保留自己的贡献序列。</p>
<p>每一折的因子均值、标准差与收益均值均由该折训练数据计算，验证数据沿用这些数值。选定惩罚参数后，再用完整历史窗口进行当天的最终拟合。</p>
<details class="method__detail"><summary>估计公式</summary>
<p>记标准化因子矩阵为 \(Z\)，去均值收益为 \(y_c\)，三种求解方式为：</p>
<p>\[ \begin{aligned}
\hat b_{\mathrm{OLS}}&=\arg\min_b\|y_c-Zb\|_2^2,\\
\hat b_{\mathrm{Ridge}}&=(Z^\top Z+\lambda I)^{-1}Z^\top y_c,\\
\hat b_{\mathrm{Lasso}}&=\arg\min_b\left\{\frac{1}{2n}\|y_c-Zb\|_2^2+\lambda\|b\|_1\right\}.
\end{aligned} \]</p>
<p>Lasso 的非零系数给出集合 \(S=\{i:\hat b_i\ne0\}\)，再在 \(Z_S\) 上进行无惩罚 OLS 重拟合。最终系数统一按 \(\beta_i=b_i/\sigma_i\) 换回原量纲。若 Lasso 选择集为空，因子贡献全部为零，当日收益全部计入残差。</p>
</details>
</section>

<section class="method__section">
<h2><span>04</span> 从系数到贡献</h2>
<p>\[ c_{i,t}=\beta_{i,t}x_{i,t},\qquad r_t=y_t-\sum_i c_{i,t}. \]</p>
<p>正贡献对应美元走强，负贡献对应美元走弱，不同因子可以相互抵消。拟合中的隐含截距并入残差。恒等式按定义闭合，模型解释力需要另外评估。</p>
<p>FX 页把美元与套息贡献归为系统性贡献，其余因子归为外生贡献，剩余部分为残差。Attribution 页将外生部分细分为利率、风险和商品。收益区间可选 1、5、21 个交易观测，训练窗口单独设置。区间内的 provisional 观测参与加总，并在页面注明。点击货币对可查看逐因子贡献、系数历史、训练拟合和残差 z 分数，Lasso 另有因子选择历史。</p>
<p><code>r2_full</code> 是训练窗口内的全模型拟合优度。<code>r2_exog</code> 在去掉美元与套息因子后，用同一种估计方法重新拟合，衡量外部变量独立使用时的拟合程度。两个指标描述样本内拟合情况，单日贡献则由系数与当天因子变动计算。</p>
<p>FX 卡片上的三组百分比按贡献绝对值占比绘制，阅读时需要同时看正负号。净变动较小的一天，也可能包含彼此抵消的大额贡献。</p>
</section>

<section class="method__section">
<h2><span>05</span> 不同估计方法下，结果相差多少</h2>
<p>FX 页和新闻触发器默认使用 126 日 OLS。稳健性徽标将它的三组归因分别与 Ridge、Lasso 后 OLS 重拟合结果比较。每次比较把三组贡献的绝对差相加，再除以该货币对此前 252 个交易记录中 OLS 残差绝对值的中位数。</p>
<p>距离连续两天超过自身历史滚动 95 分位时进入偏离状态，连续两天回到阈值内时退出。Ridge 偏离与 Lasso 换因子可以并列。Lasso 选择集为空时单列“弃权”，附当前连续天数；这些日期从 Lasso 距离的分位样本中剔除。历史不足时显示不可用。</p>
<p>一致性反映归因对估计方法的敏感程度。三路接近时，共同遗漏的变量仍可能影响结果。徽标独立展示，不改变系统健康状态的颜色。</p>
<p>研究页在三种模型共同拥有的已确认日期上比较 OLS、Ridge 与 post-Lasso。最近 252 个共有观测和完整共有历史分别列出残差 MAE、RMSE、相对 OLS 的因子分配差异，以及 Lasso 选集变动率。残差较小表示在这些日期还原波动更接近。比较使用当日实际因子和经过修订的历史数据，预测能力仍需独立检验。Lasso 还使用了更宽的候选因子菜单。</p>
<details class="method__detail"><summary>PCA 与模型健康检查</summary>
<p>滚动 PCA 用于观察六组货币收益的共同结构。当前监控记录 PC1 与美元因子的绝对相关、PC2 与套息因子的绝对相关，以及套息因子在 PC2、PC3 张成空间上的投影 \(R^2\)。代码中的参考阈值依次为 0.9、0.6 和 0.5。旧的 PC2 相关告警仍保留在记录中。</p>
<p>投影指标观察套息因子与整个二维空间的接近程度，可减少单个主成分旋转带来的干扰。PCA 输出监控统计量；归因使用显式构造的美元、套息和市场因子。</p>
<p>PCA 的特征向量来自收益相关矩阵，主成分得分与投影均使用去均值、标准化后的收益，保持计算量纲一致。</p>
<p>模型健康检查将滚动拟合程度与自身历史及其他货币对比较。运行心跳则记录管线最近一次成功完成的时间。结合两者，可以分辨拟合关系变化与任务漏跑。</p>
</details>
</section>

<section class="method__section">
<h2><span>06</span> 从异常残差到新闻短评</h2>
<p>残差用此前 126 个交易记录的标准差缩放：</p>
<p>\[ z_t=\frac{r_t}{\mathrm{sd}(r_{t-126},\ldots,r_{t-1})}. \]</p>
<p>新闻短评要求同时满足 \(|z_t|\ge2\) 与 \(|r_t|\ge50\) bp。同一天按 z 分数绝对值排序，最多选择三个货币对。绝对下限用于控制平静时期小幅变动触发的短评数量。</p>
<p>Google News RSS 提供触发日期附近的报道。Gemini 收到检索来源、当日归因、近期拟合与残差统计，以及该货币对此前的短评，再说明报道内容、模型对事件的覆盖情况和可继续核验的观测。报道发布日期与事件发生日期分别处理。</p>
<p>六项检查核对来源 ID、来源日期、数字字面值、因果断言、方向预测及中英引用集合。未通过的稿件仍保留完整记录。这些检查能拦截形式错误，报道是否足以支持解释还需要判断。</p>
<p>残差属于某个货币对的某个交易日。新闻链接提供这一观测的背景，单篇报道对应的贡献没有可用测量。</p>
<p>每日头条独立于短评触发器。本地服务缓存 30 分钟，公开站在构建时抓取快照，页面标明抓取时间。</p>
<p>另一个面板从贡献绝对值最大的两个因子出发，分别检索因子报道与货币背景。报价页面、机构同名造成的无关投资报道会留下排除原因。链接提供阅读线索，尚未加入新的因果判断。新闻发布日期仅精确到日，抓取时间记录程序实际看到报道的时刻。</p>
<p>工作日文字晨报按 America/New_York 时区运行，08:50 开始采集，在 09:00 前保存输入。独立的模型调用为最多三个大幅波动货币对补充有来源的事件背景。数字、留一法因子定义与核验路径由代码展示。模型阅读 RSS 标题和摘要，解释内容仍需审慎核对。样本验收发现模型会推演缺乏证据的政策影响，自由生成的前瞻段落暂不发布。</p>
<p>09:00 使用已保存输入发布。缺少输入或输入迟到时展示带日期的说明；解读未通过检查时保留数字摘要。重试发布沿用冻结稿件。来源编号、原文短摘录、时间、双语引用及措辞均有检查，失败稿件保留归档。这些检查无法证明因果关系，也无法核实每一句转述。验收预览单独标注。运行依赖主机在线，首次自然触发仍待观察。</p>
<p>News 页可查阅最近二十期晨报。准备、稿件与交付记录分开展示，浏览器按当前美东时间检查日期，静态构建标识其中包含的稿件版本。档案不可读时保留提示，较早稿件可按日期选择。这些记录无法确认主机仍在运行，也看不到最后一次构建之后发生的推送失败。</p>
</section>

<section class="method__section">
<h2><span>07</span> 数据来源与修订</h2>
<p>汇率、商品和 ETF 序列来自 Yahoo Finance，美国收益率、VIX 与信用利差来自 FRED。外国收益率使用以下官方来源。</p>
${sources('zh')}
<p>取数依次尝试线上源、上次成功缓存和用户提供的本地文件，回退与缺失记录进入日志。对齐偏移按货币对冻结，拼接与差分在各自日期索引上完成。原始历史从 2010 年 1 月开始，首条归因还需积累相应长度的训练窗口。</p>
<p>面板末行暂记 provisional，等待后续数据确认。RBA 等发布滞后来源还可能留下额外的 provisional 行。当地假日则可能形成没有后续替代值的 stale 观测，两类标记分别保留。正常运行冻结已完成历史，输入源日期前进时记录 provisional 覆盖审计。</p>
<p>公开页面展示构建时的快照。页面构建时间、管线最近成功时间与叙事最近运行时间分别列出，便于检查各阶段的数据年龄。</p>
</section>
</article>`;
}

export function methodologyHtml() {
  return getLang() === 'zh' ? zh() : en();
}
