// Display-only helpers. Values and alert thresholds retain the saved contract's
// convention; sorting never changes the underlying factor contribution values.
export function rankedContributions(values, limit = 2) {
  return Object.entries(values || {})
    .filter(([, value]) => Number.isFinite(value) && value !== 0)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]) || a[0].localeCompare(b[0]))
    .slice(0, limit);
}

export function residualFlag(row) {
  return Number.isFinite(row?.residual) && Number.isFinite(row?.residual_z)
    && Math.abs(row.residual) * 1e4 >= 50 && Math.abs(row.residual_z) >= 2;
}
