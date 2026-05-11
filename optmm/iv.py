"""Implied volatility with the compiled solver when it is built (python build_ext.py build_ext --inplace) and the
numpy solver in optmm.bs otherwise; the two agree to 1e-12 (tests/test_bs.py)."""
from __future__ import annotations

import numpy as np

from . import bs

try:
    from . import _iv as _ext
    HAVE_EXT = True
except ImportError:      # pragma: no cover
    _ext = None
    HAVE_EXT = False


def implied_total_vol(beta, x, theta, tol=1e-12, max_iter=60):
    if _ext is None:
        return bs.implied_total_vol(beta, x, theta, tol=tol, max_iter=max_iter)
    beta, x, theta = np.broadcast_arrays(np.asarray(beta, dtype=float), np.asarray(x, dtype=float), np.asarray(theta, dtype=float))
    shape = beta.shape
    out = _ext.implied_total_vol(np.ascontiguousarray(beta.ravel()), np.ascontiguousarray(x.ravel()), np.ascontiguousarray(theta.ravel()), tol, max_iter)
    return np.asarray(out).reshape(shape)


def implied_vol(price, F, K, T, cp=1.0, df=1.0):
    price, F, K, T, cp = (np.asarray(a, dtype=float) for a in (price, F, K, T, cp))
    x = np.log(F / K); beta = price / df / np.sqrt(F * K)
    s = implied_total_vol(beta, x, cp)
    with np.errstate(divide="ignore", invalid="ignore"):
        return s / np.sqrt(T)
