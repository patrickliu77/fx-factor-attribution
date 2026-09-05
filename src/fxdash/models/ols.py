"""OLS baseline.

Solves least squares inside the standardized window. The solver sees only arrays and
knows nothing about pair or date; standardization and scaling back to original units
are handled by rolling.
"""

from __future__ import annotations

import numpy as np


def solve_ols(z: np.ndarray, y: np.ndarray, state: dict, refit: bool,
              *, cv_data=None) -> dict:
    """z is the window-standardized design matrix, y the demeaned window returns."""
    beta, *_ = np.linalg.lstsq(z, y, rcond=None)
    return {
        "beta_std": beta,
        "selected": np.ones(z.shape[1], dtype=bool),
        "lam": None,
    }
