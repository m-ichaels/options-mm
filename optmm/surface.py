"""The volatility surface: slices fitted per expiry in total variance, numerical arbitrage checks (Durrleman's g(k) for
butterflies, monotone total variance in maturity for calendars), the calendar ordering across expiries, interpolation
in time, and fit reports.  Two slice models:

    SSVI (Gatheral-Jacquier 2014), three parameters (theta, rho, psi), k = ln(K/F), psi = theta*phi:
        w(k) = theta/2 * (1 + rho*phi*k + sqrt((phi*k + rho)^2 + 1 - rho^2))
        butterfly-free by construction when psi*(1+|rho|) < 4 and psi^2*(1+|rho|) <= 4*theta (applied as a clamp);
        across expiries theta and psi non-decreasing (the eSSVI ordering of Hendriks-Martini 2019)
    raw SVI (Gatheral 2004), five parameters (a, b, rho, m, sigma):
        w(k) = a + b*(rho*(k - m) + sqrt((k - m)^2 + sigma^2))
        the extra flexibility listed index smiles need; butterflies are prevented by a penalty on min_k g(k) during the fit
        and reported after it

Both are checked numerically after the fit: g(k) >= 0 on a log-moneyness grid (no butterfly arbitrage) and
w_{i+1}(k) >= w_i(k) at every k (no calendar arbitrage)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from . import bs

KGRID = np.linspace(-2.5, 2.5, 501)


# ---- slice models ---------------------------------------------------------------------------------------------------------------
def ssvi_w(k, theta, rho, psi):
    k = np.asarray(k, dtype=float); phi = psi / theta
    return 0.5 * theta * (1.0 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1.0 - rho * rho))


def ssvi_w_derivs(k, theta, rho, psi):
    k = np.asarray(k, dtype=float); phi = psi / theta
    r = np.sqrt((phi * k + rho) ** 2 + 1.0 - rho * rho)
    w = 0.5 * theta * (1.0 + rho * phi * k + r)
    w1 = 0.5 * theta * (rho * phi + phi * (phi * k + rho) / r)
    w2 = 0.5 * theta * (phi * phi / r - phi * phi * (phi * k + rho) ** 2 / r ** 3)
    return w, w1, w2


def svi_w(k, a, b, rho, m, sig):
    k = np.asarray(k, dtype=float)
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sig * sig))


def svi_w_derivs(k, a, b, rho, m, sig):
    k = np.asarray(k, dtype=float); x = k - m; r = np.sqrt(x * x + sig * sig)
    w = a + b * (rho * x + r); w1 = b * (rho + x / r); w2 = b * sig * sig / r ** 3
    return w, w1, w2


def w_derivs(kind, params, k):
    return ssvi_w_derivs(k, *params) if kind == "ssvi" else svi_w_derivs(k, *params)


def durrleman_g(kind, params, k):
    """g(k) >= 0 is the no-butterfly-arbitrage condition (the risk-neutral density is g * exp(-d2^2/2) / sqrt(2 pi w))"""
    w, w1, w2 = w_derivs(kind, params, k)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (1.0 - k * w1 / (2.0 * w)) ** 2 - 0.25 * w1 * w1 * (1.0 / w + 0.25) + 0.5 * w2


def butterfly_ok_ssvi(theta, rho, psi, margin=1e-9):
    return psi * (1 + abs(rho)) < 4 - margin and psi * psi * (1 + abs(rho)) <= 4 * theta + margin


def clamp_psi(theta, rho, psi):
    """the largest psi allowed by the SSVI butterfly conditions, applied as a clamp during the fit"""
    cap = min(4.0 / (1 + abs(rho)) - 1e-6, np.sqrt(4.0 * theta / (1 + abs(rho))) - 1e-9)
    return float(min(psi, max(cap, 1e-9)))


@dataclass
class Slice:
    T: float
    F: float
    kind: str = "ssvi"
    params: tuple = ()
    n: int = 0
    rmse_vol: float = np.nan          # in vol (0.01 = 1 vol point)
    max_err_vol: float = np.nan
    rmse_price_bp: float = np.nan     # price error in bp of the forward
    butterfly_ok: bool = True
    g_min: float = np.nan
    residuals: pd.DataFrame = field(default_factory=pd.DataFrame)
    k_range: tuple = (-2.5, 2.5)      # where the arbitrage checks ran

    # convenience accessors for the SSVI parameters (nan for SVI slices)
    @property
    def theta(self):
        return self.params[0] if self.kind == "ssvi" else float(self.total_var(0.0))

    @property
    def rho(self):
        return self.params[1] if self.kind == "ssvi" else self.params[2]

    @property
    def psi(self):
        return self.params[2] if self.kind == "ssvi" else np.nan

    def total_var(self, k):
        return ssvi_w(k, *self.params) if self.kind == "ssvi" else svi_w(k, *self.params)

    def iv(self, K):
        k = np.log(np.asarray(K, dtype=float) / self.F)
        return np.sqrt(np.maximum(self.total_var(k), 1e-12) / self.T)

    def atm_vol(self):
        return float(np.sqrt(max(float(self.total_var(0.0)), 1e-12) / self.T))

    def skew_25(self):
        """25-delta risk reversal in vol, put minus call, from the fitted smile"""
        s = self.atm_vol() * np.sqrt(self.T); kp = -0.6745 * s; kc = 0.6745 * s
        return float(np.sqrt(max(float(self.total_var(kp)), 1e-12) / self.T) - np.sqrt(max(float(self.total_var(kc)), 1e-12) / self.T))


# ---- forwards -----------------------------------------------------------------------------------------------------------------------
def forward_from_parity(K, call_mid, put_mid, T, r_prior=0.0, spread=None):
    """Implied forward and discount factor from C - P = df (F - K) across strikes: weighted least squares with a
    fallback to the prior discount factor when the regression slope is not credible (few strikes, wide spreads)."""
    K = np.asarray(K, dtype=float); y = np.asarray(call_mid, dtype=float) - np.asarray(put_mid, dtype=float)
    ok = np.isfinite(y) & np.isfinite(K)
    K, y = K[ok], y[ok]
    df_prior = float(np.exp(-r_prior * T))
    if len(K) < 2:
        F = float(np.median(K + y / df_prior)) if len(K) else np.nan
        return F, df_prior, "prior"
    w = np.ones_like(K) if spread is None else 1.0 / np.maximum(np.asarray(spread, dtype=float)[ok], 1e-6)
    A = np.column_stack([np.ones_like(K), K]); Wm = np.sqrt(w)[:, None]
    beta, *_ = np.linalg.lstsq(A * Wm, y * Wm[:, 0], rcond=None)
    df = -beta[1]; F = beta[0] / df if df > 0 else np.nan
    if not (0.5 < df < 1.0 + 1e-9) or not np.isfinite(F) or abs(df / df_prior - 1) > 0.01:
        Fs = K + y / df_prior
        F = float(np.average(Fs, weights=w)); return F, df_prior, "prior"
    return float(F), float(df), "regression"


# ---- slice fits ---------------------------------------------------------------------------------------------------------------------
def _prepare(K, iv, T, F, weights):
    K = np.asarray(K, dtype=float); iv = np.asarray(iv, dtype=float)
    ok = np.isfinite(K) & np.isfinite(iv) & (iv > 1e-4) & (iv < 5.0) & (K > 0)
    K, iv = K[ok], iv[ok]
    if weights is None:
        k0 = np.log(K / F); w = np.exp(-0.5 * (k0 / (0.5 * np.maximum(np.median(iv), 0.05) * np.sqrt(max(T, 1e-6)) * 4)) ** 2)
        w = np.maximum(w, 0.05)
    else:
        w = np.asarray(weights, dtype=float)[ok]
    return K, iv, w


def check_grid(k):
    """the log-moneyness range the arbitrage checks and penalties run on: the quoted range extended by half its width
    each side (a slice is checked where it would be quoted, not at k = +-2.5 where no strike exists)"""
    lo, hi = float(np.min(k)), float(np.max(k)); span = max(hi - lo, 0.05)
    return np.linspace(lo - 0.5 * span, hi + 0.5 * span, 161)


def _finish(kind, params, K, iv, w, T, F):
    k = np.log(K / F)
    model = np.sqrt(np.maximum(w_derivs(kind, params, k)[0], 1e-14) / T)
    err = model - iv
    price_mkt = bs.black(F, K, T, iv, 1.0); price_mod = bs.black(F, K, T, model, 1.0)
    kg = check_grid(k); g = durrleman_g(kind, params, kg)
    ok = bool(np.nanmin(g) >= -1e-10) and (kind != "ssvi" or butterfly_ok_ssvi(*params))
    return Slice(T=T, F=F, kind=kind, params=tuple(float(p) for p in params), n=len(K), rmse_vol=float(np.sqrt(np.average(err ** 2, weights=w))), max_err_vol=float(np.max(np.abs(err))), rmse_price_bp=float(np.sqrt(np.mean((price_mod - price_mkt) ** 2)) / F * 1e4),
                 butterfly_ok=ok, g_min=float(np.nanmin(g)), residuals=pd.DataFrame({"K": K, "k": k, "iv": iv, "model": model, "err": err, "w": w}), k_range=(float(kg[0]), float(kg[-1])))


def fit_slice_ssvi(K, iv, T, F, weights=None, starts=((-0.3, 0.6), (0.0, 0.3), (0.3, 0.6), (-0.7, 1.2), (0.6, 1.2))):
    K, iv, w = _prepare(K, iv, T, F, weights)
    if len(K) < 3:
        return None
    k = np.log(K / F); wmkt = iv * iv * T
    theta0 = max(float(np.interp(0.0, np.sort(k), wmkt[np.argsort(k)])), 1e-6)
    best = None
    for rho0, phi_scale in starts:
        x0 = np.array([np.log(theta0), np.arctanh(np.clip(rho0, -0.95, 0.95)), np.log(max(theta0 * phi_scale / np.sqrt(theta0), 1e-6))])

        def unpack(x):
            th = float(np.exp(np.clip(x[0], -20, 5))); rho = float(np.tanh(x[1])); psi = clamp_psi(th, rho, float(np.exp(np.clip(x[2], -20, 3))))
            return th, rho, psi

        def resid(x):
            th, rho, psi = unpack(x)
            return np.sqrt(w) * (np.sqrt(np.maximum(ssvi_w(k, th, rho, psi), 1e-14) / T) - iv)

        try:
            r = least_squares(resid, x0, method="trf", max_nfev=400, xtol=1e-12, ftol=1e-12)
        except Exception:  # noqa: BLE001
            continue
        cost = float(np.sum(r.fun ** 2))
        if best is None or cost < best[0]:
            best = (cost, unpack(r.x))
    return None if best is None else _finish("ssvi", best[1], K, iv, w, T, F)


def fit_slice_svi(K, iv, T, F, weights=None, penalty=500.0):
    """raw SVI with a butterfly penalty; starts from the SSVI fit (which is an SVI slice) and from a symmetric smile"""
    K, iv, w = _prepare(K, iv, T, F, weights)
    if len(K) < 5:
        return fit_slice_ssvi(K, iv, T, F, w)
    k = np.log(K / F); wmkt = iv * iv * T
    x0s = []
    s0 = fit_slice_ssvi(K, iv, T, F, w)
    if s0 is not None:
        th, rho, psi = s0.params; phi = psi / th
        # SSVI slice in raw-SVI form: a = theta/2 (1 - rho^2), b = theta phi / 2, m = -rho/phi, sigma = sqrt(1 - rho^2)/phi
        x0s.append([0.5 * th * (1 - rho * rho), 0.5 * th * phi, rho, -rho / phi, np.sqrt(1 - rho * rho) / phi])
    a0 = float(np.min(wmkt)) * 0.8; x0s.append([a0, max((np.max(wmkt) - a0) / max(np.max(np.abs(k)), 0.1), 1e-4), -0.3, 0.0, 0.2])
    kg = check_grid(k)[::4]                                     # 41 points are enough for the penalty; the check uses 161

    def resid(x):
        a, b, rho, m, sig = x
        rho = np.tanh(rho); b = abs(b); sig = abs(sig) + 1e-6
        wm = svi_w(k, a, b, rho, m, sig)
        r = np.sqrt(w) * (np.sqrt(np.maximum(wm, 1e-14) / T) - iv)
        g = durrleman_g("svi", (a, b, rho, m, sig), kg)
        pen = penalty * np.minimum(g, 0.0)                       # negative where arbitrage
        return np.concatenate([r, pen, [penalty * min(a + b * sig * np.sqrt(1 - rho * rho), 0.0)]])     # w >= 0

    best = None
    for x0 in x0s:
        x0 = np.array(x0, dtype=float); x0[2] = np.arctanh(np.clip(x0[2], -0.95, 0.95))
        try:
            r = least_squares(resid, x0, method="lm", max_nfev=300, xtol=1e-10, ftol=1e-10)
        except Exception:  # noqa: BLE001
            continue
        a, b, rho, m, sig = r.x; params = (float(a), float(abs(b)), float(np.tanh(rho)), float(m), float(abs(sig) + 1e-6))
        cost = float(np.sum(r.fun[:len(k)] ** 2))
        if best is None or cost < best[0]:
            best = (cost, params)
        if best is not None and np.nanmin(durrleman_g("svi", best[1], kg)) >= -1e-10:
            break                                                  # the SSVI-derived start already gives an arbitrage-free fit
    if best is None:
        return None
    params = repair_svi(best[1], check_grid(k))
    return _finish("svi", params, K, iv, w, T, F)


def repair_svi(params, kg, max_steps: int = 300):
    """a fit whose g(k) dips slightly below zero between penalty points is projected back: the wing slope b is shrunk and
    the smoothing sigma grown by small steps until g >= 0 on the full check grid (the fit error rises by a fraction of a
    vol point; the slice table records the result)"""
    a, b, rho, m, sig = params
    for _ in range(max_steps):
        if np.nanmin(durrleman_g("svi", (a, b, rho, m, sig), kg)) >= -1e-12:
            break
        b *= 0.98; sig *= 1.04
    return (a, b, rho, m, sig)


def fit_slice(K, iv, T, F, weights=None, kind="ssvi", **kw):
    return fit_slice_svi(K, iv, T, F, weights) if kind == "svi" else fit_slice_ssvi(K, iv, T, F, weights, **kw)


# ---- the surface across expiries ----------------------------------------------------------------------------------------------------
@dataclass
class Surface:
    slices: list                      # sorted by T
    asof: object = None
    underlying: str = ""
    calendar_ok: bool = True
    calendar_min_gap: float = np.nan  # min over k of w_{i+1}(k) - w_i(k)

    @property
    def expiries(self):
        return [s.T for s in self.slices]

    def iv(self, K, T):
        """vol at strike K and maturity T: linear interpolation of total variance in T between slices at fixed k
        relative to each slice's own forward; flat total-variance scaling beyond the ends"""
        K = np.asarray(K, dtype=float); Ts = np.array(self.expiries)
        if T <= Ts[0]:
            s = self.slices[0]; k = np.log(K / s.F); return np.sqrt(np.maximum(s.total_var(k) * (T / s.T), 1e-12) / T)
        if T >= Ts[-1]:
            s = self.slices[-1]; k = np.log(K / s.F); return np.sqrt(np.maximum(s.total_var(k) * (T / s.T), 1e-12) / T)
        j = int(np.searchsorted(Ts, T)); a, b = self.slices[j - 1], self.slices[j]
        ka, kb = np.log(K / a.F), np.log(K / b.F); lam = (T - a.T) / (b.T - a.T)
        w = (1 - lam) * a.total_var(ka) + lam * b.total_var(kb)
        return np.sqrt(np.maximum(w, 1e-12) / T)

    def report(self) -> dict:
        return {"n_slices": len(self.slices), "n_quotes": int(sum(s.n for s in self.slices)), "rmse_vol_points": float(np.sqrt(np.average([s.rmse_vol ** 2 for s in self.slices], weights=[s.n for s in self.slices])) * 100) if self.slices else np.nan,
                "max_err_vol_points": float(max(s.max_err_vol for s in self.slices) * 100) if self.slices else np.nan, "butterfly_violations": int(sum(not s.butterfly_ok for s in self.slices)), "calendar_ok": bool(self.calendar_ok), "calendar_min_gap": float(self.calendar_min_gap),
                "atm_vols": [round(s.atm_vol(), 4) for s in self.slices], "skew_25": [round(s.skew_25(), 4) for s in self.slices], "T": [round(s.T, 5) for s in self.slices], "kind": self.slices[0].kind if self.slices else ""}


def enforce_calendar(slices: list) -> list:
    """sequential eSSVI ordering for SSVI slices: theta and psi non-decreasing in T; SVI slices are left as fitted and
    only checked (the numerical check afterwards reports what remains)"""
    out = []
    for s in sorted(slices, key=lambda z: z.T):
        if out and s.kind == "ssvi" and out[-1].kind == "ssvi":
            p = out[-1]; th, rho, psi = s.params
            if th < p.params[0]:
                th = p.params[0] * (1 + 1e-6)
            if psi < p.params[2]:
                psi = clamp_psi(th, rho, p.params[2])
            s.params = (th, rho, psi)
            # the ordering of theta and psi is necessary, not sufficient: lift theta until total variance dominates the
            # previous slice on the checked range (each step scales the whole slice by one percent)
            kg = np.linspace(p.k_range[0], p.k_range[1], 161)
            for _ in range(200):
                if float(np.min(s.total_var(kg) - p.total_var(kg))) >= -1e-12:
                    break
                th *= 1.01; psi = clamp_psi(th, rho, psi); s.params = (th, rho, psi)
        elif out and s.kind == "svi":
            # an SVI slice that dips under the previous slice's total variance anywhere on the previous slice's checked
            # range is lifted by a constant (the a parameter), which keeps its own butterfly condition intact
            p = out[-1]; kg = np.linspace(p.k_range[0], p.k_range[1], 161)
            gap = float(np.min(s.total_var(kg) - p.total_var(kg)))
            if gap < 0:
                a, b, rho, m, sig = s.params; s.params = (a - gap + 1e-9, b, rho, m, sig)
        out.append(s)
    return out


def calendar_check(slices: list) -> tuple[bool, float]:
    """total variance must be non-decreasing in T at every fixed k (relative to each slice's own forward), checked on
    the quoted range of the shorter slice"""
    gaps = []
    for a, b in zip(slices[:-1], slices[1:]):
        kg = np.linspace(a.k_range[0], a.k_range[1], 161); gaps.append(float(np.min(b.total_var(kg) - a.total_var(kg))))
    if not gaps:
        return True, np.nan
    m = min(gaps); return m >= -1e-9, m


def fit_surface(quotes: pd.DataFrame, asof=None, underlying: str = "", min_quotes: int = 4, enforce: bool = True, kind: str = "ssvi") -> Surface | None:
    """quotes: one row per option with columns T, F, K, iv (and optionally weight); one slice per distinct T"""
    slices = []
    for T, g in quotes.groupby("T"):
        g = g.dropna(subset=["iv", "K"])
        if len(g) < min_quotes or T <= 0:
            continue
        s = fit_slice(g["K"].values, g["iv"].values, float(T), float(g["F"].iloc[0]), g["weight"].values if "weight" in g else None, kind=kind)
        if s is not None:
            slices.append(s)
    if not slices:
        return None
    slices = sorted(slices, key=lambda z: z.T)
    if enforce:
        slices = enforce_calendar(slices)
    ok, gap = calendar_check(slices)
    return Surface(slices=slices, asof=asof, underlying=underlying, calendar_ok=ok, calendar_min_gap=gap)


def quotes_from_chain(chain: pd.DataFrame, T_col="T", F_col="F", K_col="K", price_col="mid", cp_col="cp", df_col="df") -> pd.DataFrame:
    """implied vols from mid prices (discounted), keeping the out-of-the-money side of each strike when both are present"""
    c = chain.copy()
    c["iv"] = bs.implied_vol(c[price_col].values, c[F_col].values, c[K_col].values, c[T_col].values, c[cp_col].values, c[df_col].values if df_col in c else 1.0)
    c["otm"] = np.where(c[cp_col] > 0, c[K_col] >= c[F_col], c[K_col] < c[F_col])
    c = c.sort_values(["T", "K", "otm"], ascending=[True, True, False]).drop_duplicates(["T", "K"], keep="first")
    return c
