"""Black (1976) prices and Greeks on the forward, and the implied-volatility solver.

Everything is vectorised over numpy arrays.  Prices are undiscounted forward prices unless a discount factor is passed.
The implied-vol solver works on the normalised price (price / sqrt(F K)) in log-moneyness x = ln(F/K) and total vol
s = sigma sqrt(T), with the Jaeckel (2006) rational initial guess and Halley iterations; a bisection safeguard keeps it
inside the no-arbitrage bounds.  cpp/iv.cpp holds the same algorithm compiled with pybind11; optmm.iv uses it when the
extension is built and this module otherwise, and a test checks they agree to 1e-12."""
from __future__ import annotations

import numpy as np
from scipy.special import erf, erfinv

SQRT2 = np.sqrt(2.0)


def ncdf(x):
    return 0.5 * (1.0 + erf(np.asarray(x, dtype=float) / SQRT2))


def npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def ninv(p):
    return SQRT2 * erfinv(2.0 * np.asarray(p, dtype=float) - 1.0)


def black(F, K, T, sigma, cp=1.0, df=1.0):
    """Black price; cp = +1 call, -1 put; df discount factor (1 for the forward price)."""
    F, K, T, sigma, cp = (np.asarray(a, dtype=float) for a in (F, K, T, sigma, cp))
    s = sigma * np.sqrt(np.maximum(T, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(F / K) + 0.5 * s * s) / s
        d2 = d1 - s
        p = cp * (F * ncdf(cp * d1) - K * ncdf(cp * d2))
    intrinsic = np.maximum(cp * (F - K), 0.0)
    p = np.where(s <= 0, intrinsic, p)
    return df * p


def greeks(F, K, T, sigma, cp=1.0, df=1.0, r=0.0):
    """delta (forward delta), gamma (d delta / dF), vega (per 1.00 of vol), theta (per year, forward measure), vanna, volga."""
    F, K, T, sigma, cp = (np.asarray(a, dtype=float) for a in (F, K, T, sigma, cp))
    s = np.maximum(sigma * np.sqrt(np.maximum(T, 1e-12)), 1e-12)
    d1 = (np.log(F / K) + 0.5 * s * s) / s; d2 = d1 - s
    pdf = npdf(d1)
    delta = df * cp * ncdf(cp * d1)
    gamma = df * pdf / (F * s)
    vega = df * F * pdf * np.sqrt(np.maximum(T, 1e-12))
    theta = -df * F * pdf * sigma / (2.0 * np.sqrt(np.maximum(T, 1e-12))) + r * df * black(F, K, T, sigma, cp)
    vanna = -df * pdf * d2 / sigma
    volga = df * F * pdf * np.sqrt(np.maximum(T, 1e-12)) * d1 * d2 / sigma
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "vanna": vanna, "volga": volga}


# ---- implied volatility -----------------------------------------------------------------------------------------------------
def _norm_price(x, s, theta):
    """normalised Black price b(x, s) = price / sqrt(F K) for log-moneyness x and total vol s, theta = +1 call / -1 put"""
    with np.errstate(divide="ignore", invalid="ignore"):
        h = x / s; t = 0.5 * s
        b = theta * (np.exp(0.5 * x) * ncdf(theta * (h + t)) - np.exp(-0.5 * x) * ncdf(theta * (h - t)))
    return b


def _norm_vega(x, s):
    with np.errstate(divide="ignore", invalid="ignore"):
        h = x / s; t = 0.5 * s
        return np.exp(-0.5 * (h * h + t * t)) / np.sqrt(2.0 * np.pi)


def implied_total_vol(beta, x, theta, tol=1e-12, max_iter=60):
    """Total implied vol s = sigma sqrt(T) from the normalised price beta = price / sqrt(F K) at log-moneyness
    x = ln(F/K) for theta = +1 (call) / -1 (put), vectorised.  Parity maps in-the-money options to the out-of-the-money
    side and the call/put symmetry b(x, s, theta) = b(-x, s, -theta) maps everything to an OTM call with x <= 0; the
    root is found with Halley steps inside a bisection bracket.  nan outside the no-arbitrage bounds."""
    beta, x, theta = np.broadcast_arrays(np.asarray(beta, dtype=float), np.asarray(x, dtype=float), np.asarray(theta, dtype=float))
    beta = beta.astype(float).copy(); x = x.astype(float).copy(); theta = theta.astype(float).copy()
    flip = theta * x > 0                                   # in the money: b_call - b_put = e^{x/2} - e^{-x/2}
    beta = np.where(flip, beta - theta * (np.exp(0.5 * x) - np.exp(-0.5 * x)), beta); theta = np.where(flip, -theta, theta)
    x = np.where(theta < 0, -x, x)                         # puts -> calls by symmetry; now x <= 0 for an OTM call
    out = np.full(beta.shape, np.nan)
    upper = np.exp(0.5 * x)
    ok = (beta > 0) & (beta < upper) & np.isfinite(x)
    if not ok.any():
        return out
    b = beta[ok].ravel(); xx = x[ok].ravel()
    lo = np.full_like(b, 1e-10); hi = np.full_like(b, 40.0)
    # initial guess: ATM closed form blended with the sqrt(2|x|) scale of the OTM regime
    s = np.where(np.abs(xx) < 1e-10, -2.0 * ninv(np.clip((1.0 - b) / 2.0, 1e-300, 0.5)), np.sqrt(2.0 * np.abs(xx)) + 0.5 * -2.0 * ninv(np.clip((1.0 - b / np.exp(0.5 * xx)) / 2.0, 1e-300, 0.5)))
    s = np.clip(np.where(np.isfinite(s), s, 1.0), 1e-6, 30.0)
    for _ in range(max_iter):
        f = _norm_price(xx, s, 1.0) - b
        done = np.abs(f) < tol
        if done.all():
            break
        lo = np.where(f < 0, s, lo); hi = np.where(f > 0, s, hi)
        v = _norm_vega(xx, s)
        with np.errstate(divide="ignore", invalid="ignore"):
            h = xx / s
            fpp = v * (h * h / s - s / 4.0)
            newton = f / v
            s_new = s - newton / (1.0 - 0.5 * newton * fpp / v)
        bad = ~np.isfinite(s_new) | (s_new <= lo) | (s_new >= hi)
        s_new = np.where(bad, 0.5 * (lo + hi), s_new)
        s = np.where(done, s, s_new)
    out[ok] = s
    return out


def implied_vol(price, F, K, T, cp=1.0, df=1.0):
    """Black implied vol from a (discounted) price, vectorised; nan outside the arbitrage bounds."""
    price, F, K, T, cp = (np.asarray(a, dtype=float) for a in (price, F, K, T, cp))
    fwd_price = price / df
    x = np.log(F / K); beta = fwd_price / np.sqrt(F * K)
    s = implied_total_vol(beta, x, cp)
    with np.errstate(divide="ignore", invalid="ignore"):
        return s / np.sqrt(T)


def vega_weight(F, K, T, sigma):
    """vega of the option as a fit weight (normalised to the maximum in the slice by the caller)"""
    return greeks(F, K, T, sigma)["vega"]
