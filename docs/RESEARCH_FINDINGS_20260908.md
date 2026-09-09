# Offline research findings

These are retrospective return-reconstruction experiments, not trading backtests
or causal estimates. They were completed on September 7 using saved input tables.
The effective evaluation period was January 2, 2023 to December 30, 2025, with
778 observations for five pairs and 777 for MXN. Training used earlier observations.
This period had already informed development and is not an untouched holdout.

## Decisions retained in production

| Question | Observed result | Decision |
|---|---|---|
| Does Ridge help? | Scale-adjusted median beta change fell in all 18 same-menu pair/window comparisons; RMSE did not consistently improve. | Retain shrinkage and sensitivity comparisons. |
| Is one window best? | Ten of 24 comparison families changed the sign of their RMSE difference across 63/126/252 windows. | Keep all three; do not optimise the default on reused history. |
| Should CAD and NOK swap oil proxies? | All 18 full-period RMSE differences were positive for swaps, with 21-observation block intervals crossing zero. | Keep CAD WTI and NOK Brent; universal optimality is unproven. |
| Should Lasso CV score post-OLS refits? | Of 36 paired configurations, 35 candidate RMSE estimates rose and 25 empty-set rates rose. | Keep the current validation objective. |
| Should PCA allocate returns? | No PCR attribution arm was evaluated. Currency-panel PCA remains a structural diagnostic. | Keep its monitoring role. |

Estimator comparisons control the menu; menu comparisons change that component
separately. Paired results use shared dates and targets. Moving blocks of 5, 21 and
63 observations examine serial dependence. Intervals resample fitted paths without
refitting every bootstrap sample and have no multiple-comparison adjustment.
Related comparisons are not independent trials.

Small residual changes can coexist with large factor reallocations. NOK's expanded-menu
average factor-allocation L1 difference was roughly 15 to 18 bp across the three windows.
This supports reporting sensitivity alongside any driver narrative.

Entry points are `python -m fxdash.research --help` and
`python -m fxdash.research.lasso_cv --help`. Reports and inputs remain local.
See [research commands](../ops/README.md#offline-research-and-input-archives).
Next evidence should come from timestamped new observations and operational acceptance.
Searching the same historical period again would not create independent validation.
