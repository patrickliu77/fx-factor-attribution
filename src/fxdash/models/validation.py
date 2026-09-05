"""Forward validation with preprocessing learned from each training fold."""

import numpy as np


def prepare_fold(x, y, train, test):
    """Return centred/scaled train and validation arrays in one training basis.

    The validation target uses the training target mean, which restores the
    unpenalised intercept when computing prediction errors.
    """
    xt, yt = x[train], y[train]
    mean = xt.mean(axis=0)
    scale = xt.std(axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    y_mean = yt.mean()
    return ((xt - mean) / scale, yt - y_mean,
            (x[test] - mean) / scale, y[test] - y_mean)
