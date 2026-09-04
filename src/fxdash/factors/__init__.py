"""Factor construction. The factor library is fixed; the per-pair sets live in config.py."""

from .build import build_pair_panel, factor_stale_names

__all__ = ["build_pair_panel", "factor_stale_names"]
