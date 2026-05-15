"""SSVI and SVI slices, the arbitrage conditions and their repair, the calendar ordering, interpolation, forwards."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from optmm import bs, surface as S  # noqa: E402


def smile(F, T, kind, params, n=25, width=0.6, noise=0.0, seed=0):
    K = F * np.exp(np.linspace(-width, width, n)); k = np.log(K / F)
    w = S.ssvi_w(k, *params) if kind == "ssvi" else S.svi_w(k, *params)
    iv = np.sqrt(w / T) + np.random.default_rng(seed).normal(0, noise, n)
    return K, iv


def test_ssvi_recovery_and_conditions():
    F, T = 100.0, 0.25; K, iv = smile(F, T, "ssvi", (0.04, -0.4, 0.3), noise=0.001)
    sl = S.fit_slice(K, iv, T, F)
    assert sl.kind == "ssvi" and sl.params[0] == pytest.approx(0.04, rel=0.05) and sl.params[1] == pytest.approx(-0.4, abs=0.05) and sl.params[2] == pytest.approx(0.3, rel=0.1)
    assert sl.rmse_vol < 0.002 and sl.butterfly_ok and sl.g_min > 0
    assert sl.atm_vol() == pytest.approx(np.sqrt(0.04 / T), rel=0.02) and sl.skew_25() > 0      # negative rho: puts above calls
    assert S.butterfly_ok_ssvi(0.04, -0.4, 0.3) and not S.butterfly_ok_ssvi(0.04, -0.9, 3.0)
    assert S.clamp_psi(0.04, -0.4, 3.0) < 3.0


def test_svi_recovery_and_repair():
    F, T = 100.0, 0.5; params = (0.03, 0.12, -0.5, 0.05, 0.2)
    K, iv = smile(F, T, "svi", params, noise=0.0005)
    sl = S.fit_slice(K, iv, T, F, kind="svi")
    assert sl.kind == "svi" and sl.rmse_vol < 0.001 and sl.butterfly_ok
    assert np.allclose(sl.params, params, atol=0.02)
    # an arbitrageable slice (too steep a wing) is repaired
    bad = (0.001, 0.5, -0.9, 0.0, 0.02); kg = S.check_grid(np.log(K / F))
    assert np.nanmin(S.durrleman_g("svi", bad, kg)) < 0
    fixed = S.repair_svi(bad, kg)
    assert np.nanmin(S.durrleman_g("svi", fixed, kg)) >= -1e-12


def test_svi_beats_ssvi_on_a_curved_smile():
    F, T = 100.0, 0.1; params = (0.002, 0.08, -0.6, 0.02, 0.05)
    K, iv = smile(F, T, "svi", params, width=0.3)
    a = S.fit_slice(K, iv, T, F, kind="ssvi"); b = S.fit_slice(K, iv, T, F, kind="svi")
    assert b.rmse_vol < a.rmse_vol and b.butterfly_ok


def test_surface_calendar_and_interpolation():
    F = 100.0; rows = []
    for T, th, rho, psi in ((0.05, 0.02, -0.3, 0.15), (0.25, 0.04, -0.4, 0.3), (1.0, 0.12, -0.5, 0.5)):
        K, iv = smile(F, T, "ssvi", (th, rho, psi), n=21, width=0.8, noise=0.002, seed=1)
        rows += [{"T": T, "F": F, "K": k, "iv": v} for k, v in zip(K, iv)]
    surf = S.fit_surface(pd.DataFrame(rows))
    rep = surf.report()
    assert rep["n_slices"] == 3 and rep["butterfly_violations"] == 0 and rep["calendar_ok"] and rep["calendar_min_gap"] >= -1e-9
    ivs = surf.iv(np.array([90.0, 100.0, 110.0]), 0.5)
    assert ivs[0] > ivs[2]                                       # skew preserved between slices
    # total variance is monotone in T at fixed strike across the interpolation
    w = [surf.iv(np.array([100.0]), T)[0] ** 2 * T for T in (0.05, 0.1, 0.25, 0.5, 1.0)]
    assert all(np.diff(w) >= -1e-12)
    # a deliberately inverted pair is lifted by the calendar enforcement
    a = S.fit_slice(*smile(F, 0.25, "ssvi", (0.04, -0.4, 0.3)), 0.25, F); b = S.fit_slice(*smile(F, 0.5, "ssvi", (0.03, -0.4, 0.25)), 0.5, F)
    fixed = S.enforce_calendar([a, b]); ok, gap = S.calendar_check(fixed)
    assert ok and fixed[1].params[0] >= fixed[0].params[0]


def test_forward_from_parity():
    K = np.linspace(80, 120, 21); T = 0.4; df = np.exp(-0.05 * T); Ftrue = 103.0
    C = bs.black(Ftrue, K, T, 0.3, 1.0, df); P = bs.black(Ftrue, K, T, 0.3, -1.0, df)
    F, d, how = S.forward_from_parity(K, C, P, T, r_prior=0.05)
    assert F == pytest.approx(Ftrue, abs=1e-6) and d == pytest.approx(df, abs=1e-6) and how == "regression"
    # noisy prices with two decimals: the regression slope is not credible and the prior discount factor is used
    rng = np.random.default_rng(0); F2, d2, how2 = S.forward_from_parity(K, np.round(C + rng.normal(0, 0.05, 21), 2), np.round(P + rng.normal(0, 0.05, 21), 2), T, r_prior=0.05)
    assert abs(F2 - Ftrue) < 0.5 and abs(d2 - df) < 0.01
