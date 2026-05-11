"""Black prices, Greeks and the implied-volatility solver."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from optmm import bs  # noqa: E402


def test_put_call_parity_and_limits():
    F, K, T, s = 100.0, 95.0, 0.5, 0.3
    c = bs.black(F, K, T, s, 1.0); p = bs.black(F, K, T, s, -1.0)
    assert c - p == pytest.approx(F - K, abs=1e-10)
    assert bs.black(F, K, 0.0, s, 1.0) == pytest.approx(5.0)                 # intrinsic at expiry
    assert bs.black(F, K, T, 0.0, -1.0) == pytest.approx(0.0)
    assert bs.black(F, K, T, s, 1.0, df=0.9) == pytest.approx(0.9 * c)


def test_greeks_against_bumps():
    F, K, T, s = 100.0, 105.0, 0.75, 0.4
    g = bs.greeks(F, K, T, s, 1.0); h = 1e-4
    d_num = (bs.black(F + h, K, T, s) - bs.black(F - h, K, T, s)) / (2 * h)
    gam_num = (bs.black(F + h, K, T, s) - 2 * bs.black(F, K, T, s) + bs.black(F - h, K, T, s)) / h ** 2
    v_num = (bs.black(F, K, T, s + h) - bs.black(F, K, T, s - h)) / (2 * h)
    th_num = -(bs.black(F, K, T + h, s) - bs.black(F, K, T - h, s)) / (2 * h)
    assert g["delta"] == pytest.approx(d_num, rel=1e-6) and g["gamma"] == pytest.approx(gam_num, rel=1e-4)
    assert g["vega"] == pytest.approx(v_num, rel=1e-6) and g["theta"] == pytest.approx(th_num, rel=1e-5)
    assert g["vanna"] == pytest.approx((bs.greeks(F, K, T, s + h)["delta"] - bs.greeks(F, K, T, s - h)["delta"]) / (2 * h), rel=1e-5)


def test_implied_vol_round_trip_grid():
    F = 100.0
    K = np.exp(np.linspace(-1.2, 1.2, 49)) * F
    T = np.array([2 / 365, 30 / 365, 0.5, 2.0]); sig = np.array([0.08, 0.3, 0.8, 2.0])
    KK, TT, SS = np.meshgrid(K, T, sig, indexing="ij")
    for cp in (1.0, -1.0):
        p = bs.black(F, KK, TT, SS, cp); iv = bs.implied_vol(p, F, KK, TT, cp)
        intrinsic = np.maximum(cp * (F - KK), 0.0); recoverable = (p - intrinsic) > 1e-6 * F      # below that the price has no digits left for the vol
        err = np.abs(iv - SS)[recoverable]
        assert np.all(np.isfinite(iv[recoverable])) and err.max() < 1e-7        # the solver stops at 1e-12 in price; far-OTM vegas of 1e-4 turn that into 1e-8 in vol
    # discounted prices
    p = bs.black(F, 110.0, 0.3, 0.25, 1.0, df=0.97); assert bs.implied_vol(p, F, 110.0, 0.3, 1.0, df=0.97) == pytest.approx(0.25, abs=1e-10)


def test_implied_vol_out_of_bounds_is_nan():
    assert np.isnan(bs.implied_vol(-1.0, 100.0, 100.0, 0.5, 1.0))
    assert np.isnan(bs.implied_vol(101.0, 100.0, 100.0, 0.5, 1.0))            # above the forward
    assert np.isnan(bs.implied_vol(2.0, 100.0, 95.0, 0.5, 1.0))               # below intrinsic
    iv = bs.implied_vol(np.array([5.0, 1e-30, 200.0]), 100.0, np.array([100.0, 100.0, 100.0]), 0.5, 1.0)
    assert iv[0] == pytest.approx(5.0 * np.sqrt(2 * np.pi) / (100.0 * np.sqrt(0.5)), rel=1e-3) and iv[1] < 1e-6 and np.isnan(iv[2])


def test_cpp_extension_agrees_if_built():
    try:
        from optmm import _iv  # noqa: F401
    except ImportError:
        pytest.skip("C++ extension not built")
    from optmm import iv as fast
    F = 100.0; K = np.exp(np.linspace(-0.8, 0.8, 33)) * F; T = 0.25; s = 0.35
    p = bs.black(F, K, T, s, 1.0)
    a = fast.implied_vol(p, F, K, T, 1.0); b = bs.implied_vol(p, F, K, T, 1.0)
    assert np.allclose(a, b, atol=1e-10)
