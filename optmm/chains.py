"""Listed-equity surfaces from the DoltHub end-of-day chains: the forward and discount factor from put-call parity across
strikes, our implied vols from the mids, one eSSVI surface per day, and the fit and arbitrage statistics over the
history; the vendor's own IV column is kept for comparison."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import bs, iv as iv_solver, surface as S


def fit_day(chain_day: pd.DataFrame, r: float = 0.0, min_strikes: int = 6, kind: str = "svi") -> tuple[S.Surface | None, list, pd.DataFrame]:
    """chain_day: one symbol, one date.  Returns the surface, per-expiry forward records and the quotes with our IVs."""
    recs = []; quotes = []
    for exp, g in chain_day.groupby("expiration"):
        T = float(g["T"].iloc[0])
        if T <= 0:
            continue
        calls = g[g["cp"] > 0].set_index("strike"); puts = g[g["cp"] < 0].set_index("strike")
        both = calls.index.intersection(puts.index)
        if len(both) < 3:
            continue
        cm = calls.loc[both, "mid"]; pm = puts.loc[both, "mid"]; sp = calls.loc[both, "spread"] + puts.loc[both, "spread"]
        ok = cm.notna() & pm.notna() & (calls.loc[both, "bid"] > 0) & (puts.loc[both, "bid"] > 0)
        if ok.sum() < 3:
            continue
        # the discount factor comes from the funding rate (end-of-day mids at two decimals cannot resolve it); the forward
        # is the spread-weighted median of the parity forwards K + (C - P) / df over the strikes quoted on both sides
        df = float(np.exp(-r * T)); Fs = both[ok].values + (cm[ok].values - pm[ok].values) / df
        w_f = 1.0 / np.maximum(sp[ok].values, 0.01); order = np.argsort(Fs); cw = np.cumsum(w_f[order]); F = float(Fs[order][np.searchsorted(cw, 0.5 * cw[-1])]); how = "parity_median"
        if not np.isfinite(F) or F <= 0:
            continue
        gg = g[(g["bid"] >= 0.05) & (g["ask"] > 0)].copy(); gg["F"] = F; gg["df"] = df; gg["K"] = gg["strike"]
        gg = gg[(gg["spread"] / gg["mid"]) <= 0.5]
        gg["iv"] = iv_solver.implied_vol(gg["mid"].values, F, gg["K"].values, T, gg["cp"].values, df)
        gg["otm"] = np.where(gg["cp"] > 0, gg["K"] >= F, gg["K"] < F)
        gg = gg[gg["iv"].notna()]
        gg["k_sig"] = np.log(gg["K"] / F) / np.maximum(gg["iv"] * np.sqrt(T), 1e-6)
        gg = gg[gg["k_sig"].abs() <= 3.0]
        vega = bs.greeks(F, gg["K"].values, T, gg["iv"].values, gg["cp"].values)["vega"]
        gg["weight"] = vega / np.maximum(gg["spread"].values, 0.01)
        gg["weight"] = gg["weight"] / max(gg["weight"].max(), 1e-12)
        fit_in = gg[gg["otm"]].drop_duplicates("K")
        recs.append({"expiration": exp, "T": T, "F": F, "df": df, "forward_method": how, "n_strikes": int(len(fit_in))})
        if len(fit_in) >= min_strikes:
            quotes.append(fit_in)
    if not quotes:
        return None, recs, pd.DataFrame()
    q = pd.concat(quotes); surf = S.fit_surface(q[["T", "F", "K", "iv", "weight"]], min_quotes=min_strikes, kind=kind)
    if surf is not None:
        q = q.copy(); q["iv_model"] = np.nan
        for sl in surf.slices:
            m = np.isclose(q["T"].values, sl.T); q.loc[m, "iv_model"] = sl.iv(q.loc[m, "K"].values)
        q["price_model"] = bs.black(q["F"].values, q["K"].values, q["T"].values, q["iv_model"].fillna(q["iv"]).values, q["cp"].values, q["df"].values)
        q["within_spread"] = (q["price_model"] >= q["bid"] - 1e-9) & (q["price_model"] <= q["ask"] + 1e-9)
        q["err_half_spreads"] = (q["price_model"] - q["mid"]) / np.maximum(0.5 * q["spread"], 0.005)
    return surf, recs, q


def fit_history(chain: pd.DataFrame, rates: pd.Series, symbol: str, verbose: bool = False, kind: str = "svi") -> tuple[pd.DataFrame, pd.DataFrame]:
    """every day of a symbol's chain: the slice table and a per-day report (fit quality, arbitrage, vendor IV comparison)"""
    slices, days = [], []
    for date, g in chain.groupby("date"):
        r = float(rates.reindex([date], method="ffill").iloc[0]) if len(rates) else 0.0
        if not np.isfinite(r):
            r = 0.0
        surf, recs, q = fit_day(g, r, kind=kind)
        if surf is None:
            days.append({"date": date, "symbol": symbol, "n_slices": 0}); continue
        rep = surf.report()
        vend = q.dropna(subset=["iv", "vol"]); vend = vend[vend["vol"] > 0]
        days.append({"date": date, "symbol": symbol, "n_slices": rep["n_slices"], "n_quotes": rep["n_quotes"], "rmse_vol_points": rep["rmse_vol_points"], "max_err_vol_points": rep["max_err_vol_points"], "butterfly_violations": rep["butterfly_violations"], "calendar_ok": rep["calendar_ok"], "calendar_min_gap": rep["calendar_min_gap"],
                     "atm_vol_front": rep["atm_vols"][0], "rr25_front": rep["skew_25"][0], "vendor_iv_abs_diff_volpts": float(np.median(np.abs(vend["iv"] - vend["vol"])) * 100) if len(vend) else np.nan, "n_expiries": int(len(recs)), "within_spread_share": float(q["within_spread"].mean()), "rmse_half_spreads": float(np.sqrt(np.mean(q["err_half_spreads"] ** 2)))})
        for sl, rec in zip(surf.slices, [x for x in recs if x["n_strikes"] >= 6]):
            slices.append({"date": date, "symbol": symbol, "expiration": rec["expiration"], "T": sl.T, "F": sl.F, "df": rec["df"], "kind": sl.kind, "params": list(sl.params), "theta": sl.theta, "rho": sl.rho, "psi": sl.psi, "n": sl.n, "rmse_vol": sl.rmse_vol, "max_err_vol": sl.max_err_vol, "butterfly_ok": sl.butterfly_ok, "g_min": sl.g_min, "atm_vol": sl.atm_vol(), "rr25": sl.skew_25()})
        if verbose and len(days) % 100 == 0:
            print("  ", symbol, len(days), "days")
    return pd.DataFrame(slices), pd.DataFrame(days)
