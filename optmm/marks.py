"""Marking: snapshots of the quoting universe from the tape, our own implied vols from the quotes, the eSSVI surface per
snapshot, fair values per instrument; and the historical surfaces fitted from the trade prints (Deribit's per-trade IV)
one slice per expiry per day."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import bs, iv as iv_solver, surface as S

YEAR_MS = 365.25 * 86400e3


def last_before(df: pd.DataFrame, ts_ms: int, key: str = "instrument") -> pd.DataFrame:
    """the last row per instrument at or before ts (df sorted by ts)"""
    d = df[df["ts"] <= ts_ms]
    return d.groupby(key, sort=False).tail(1)


def snapshot_quotes(tick: pd.DataFrame, ts_ms: int, min_size: float = 0.0, max_rel_spread: float = 0.6) -> pd.DataFrame:
    """quotes at a point in time: one row per option with T, F, K, cp, bid/ask in BTC and USD, our implied vols"""
    d = last_before(tick[tick["cp"].notna()], ts_ms)
    d = d[(d["bid"] > 0) & (d["ask"] > 0) & (d["bid_size"] >= min_size) & (d["ask_size"] >= min_size)].copy()
    if d.empty:
        return d
    d["T"] = (d["expiry_ms"] - ts_ms) / YEAR_MS
    d = d[d["T"] > 1e-4]
    d["F"] = d["underlying"].astype(float); d["K"] = d["strike"].astype(float)
    d["mid_btc"] = 0.5 * (d["bid"] + d["ask"]); d["mid"] = d["mid_btc"] * d["index"]
    d["bid_usd"] = d["bid"] * d["index"]; d["ask_usd"] = d["ask"] * d["index"]
    d["rel_spread"] = (d["ask"] - d["bid"]) / d["mid_btc"]
    d = d[d["rel_spread"] <= max_rel_spread]
    d["iv"] = iv_solver.implied_vol(d["mid"].values, d["F"].values, d["K"].values, d["T"].values, d["cp"].values)
    d["iv_bid"] = iv_solver.implied_vol(d["bid_usd"].values, d["F"].values, d["K"].values, d["T"].values, d["cp"].values)
    d["iv_ask"] = iv_solver.implied_vol(d["ask_usd"].values, d["F"].values, d["K"].values, d["T"].values, d["cp"].values)
    d["otm"] = np.where(d["cp"] > 0, d["K"] >= d["F"], d["K"] < d["F"])
    # weight: tighter and deeper quotes count more; the far wings less
    d["weight"] = (1.0 / np.maximum(d["rel_spread"], 0.02)) * np.minimum(d["bid_size"] + d["ask_size"], 20.0) / 20.0
    return d


def fit_snapshot(tick: pd.DataFrame, ts_ms: int, underlying: str = "BTC") -> tuple[S.Surface | None, pd.DataFrame]:
    """the surface at ts from the OTM side of each strike, and the quotes with the model vol and fair price attached"""
    q = snapshot_quotes(tick, ts_ms)
    if q.empty:
        return None, q
    fit_in = q[q["otm"] & q["iv"].notna()].sort_values(["T", "K"]).drop_duplicates(["T", "K"])
    surf = S.fit_surface(fit_in[["T", "F", "K", "iv", "weight"]], asof=ts_ms, underlying=underlying)
    if surf is None:
        return None, q
    q = q.copy()
    q["iv_model"] = np.nan
    for sl in surf.slices:
        m = np.isclose(q["T"].values, sl.T)
        q.loc[m, "iv_model"] = sl.iv(q.loc[m, "K"].values)
    q["fair"] = bs.black(q["F"].values, q["K"].values, q["T"].values, q["iv_model"].fillna(q["iv"]).values, q["cp"].values)     # USD
    q["fair_btc"] = q["fair"] / q["index"]
    g = bs.greeks(q["F"].values, q["K"].values, q["T"].values, q["iv_model"].fillna(q["iv"]).values, q["cp"].values)
    for k in ("delta", "gamma", "vega", "theta"):
        q[f"{k}_model"] = g[k]
    q["within_spread"] = (q["fair"] >= q["bid_usd"] - 1e-9) & (q["fair"] <= q["ask_usd"] + 1e-9)
    return surf, q


def tape_surfaces(tick: pd.DataFrame, step_s: int = 60, start_ms: int | None = None, end_ms: int | None = None, verbose: bool = False):
    """fit a surface every step seconds over the tape; returns (slices table, per-snapshot report table)"""
    t0 = int(start_ms or tick["ts"].min()); t1 = int(end_ms or tick["ts"].max())
    rows, reps = [], []
    for ts in range(t0 + step_s * 1000, t1 + 1, step_s * 1000):
        surf, q = fit_snapshot(tick, ts)
        if surf is None:
            continue
        rep = surf.report(); rep["ts"] = ts; rep["n_quotes_all"] = int(len(q)); rep["within_spread_share"] = float(q["within_spread"].mean()); rep["mark_iv_abs_diff_volpts"] = float(np.nanmedian(np.abs(q["iv_model"] * 100 - q["mark_iv"]))); reps.append(rep)
        for sl in surf.slices:
            rows.append({"ts": ts, "T": sl.T, "F": sl.F, "theta": sl.theta, "rho": sl.rho, "psi": sl.psi, "n": sl.n, "rmse_vol": sl.rmse_vol, "max_err_vol": sl.max_err_vol, "rmse_price_bp": sl.rmse_price_bp, "butterfly_ok": sl.butterfly_ok, "g_min": sl.g_min, "atm_vol": sl.atm_vol(), "rr25": sl.skew_25()})
        if verbose and len(reps) % 60 == 0:
            print("  surfaces", len(reps), "rmse", round(rep["rmse_vol_points"], 3))
    return pd.DataFrame(rows), pd.DataFrame(reps)


# ---- historical surfaces from the trade prints --------------------------------------------------------------------------------------
def daily_surfaces_from_trades(trades: pd.DataFrame, min_strikes: int = 6, max_expiries: int = 6, window_hours: tuple = (6, 8)) -> pd.DataFrame:
    """One surface per day from the prints in a two-hour window before the 08:00 UTC settlement: per instrument the
    volume-weighted IV of its prints in the window (Deribit's per-trade IV, in %), the forward the median index in the
    window, the out-of-the-money side of each strike, the six expiries with the most strikes; SSVI slices with the
    calendar ordering enforced.  Returns the slice table with fit statistics."""
    tr = trades[(trades["iv"] > 0) & (trades["cp"].notna())].copy()
    t = pd.to_datetime(tr["timestamp"], unit="ms", utc=True); tr["day"] = t.dt.floor("D"); hour = t.dt.hour + t.dt.minute / 60.0
    tr = tr[(hour >= window_hours[0]) & (hour < window_hours[1])]
    tr["w_iv"] = tr["iv"] * tr["amount"]
    g_ins = tr.groupby(["day", "instrument_name"]).agg(ts=("timestamp", "max"), w_iv=("w_iv", "sum"), amount=("amount", "sum"), n=("iv", "size"), index=("index_price", "median"), expiry_ms=("expiry_ms", "last"), strike=("strike", "last"), cp=("cp", "last")).reset_index()
    g_ins["iv"] = g_ins["w_iv"] / g_ins["amount"] / 100.0
    rows = []
    for day, g in g_ins.groupby("day"):
        end_ts = int(pd.Timestamp(day).value // 10 ** 6 + window_hours[1] * 3600e3)
        g = g.copy(); g["T"] = (g["expiry_ms"] - end_ts) / YEAR_MS; g = g[g["T"] > 2e-3]
        F = float(g["index"].median())
        g = g[np.where(g["cp"] > 0, g["strike"] >= F, g["strike"] < F)]
        counts = g.groupby("expiry_ms")["strike"].nunique().sort_values(ascending=False)
        keep = [e for e in counts.index[:max_expiries] if counts[e] >= min_strikes]
        if not keep:
            continue
        q = g[g["expiry_ms"].isin(keep)].drop_duplicates(["expiry_ms", "strike"])
        q = pd.DataFrame({"T": q["T"].values, "F": F, "K": q["strike"].values, "iv": q["iv"].values, "weight": np.sqrt(q["amount"].values.astype(float)).clip(0.3, 5.0), "expiry_ms": q["expiry_ms"].values})
        surf = S.fit_surface(q, min_quotes=min_strikes, kind="ssvi")
        if surf is None:
            continue
        # robust second pass: prints more than 3 MAD (and 2 vol points) from the first fit are off-market or stale
        resid = np.full(len(q), np.nan)
        for sl in surf.slices:
            m = np.isclose(q["T"].values, sl.T); resid[m] = q["iv"].values[m] - sl.iv(q["K"].values[m])
        mad = np.nanmedian(np.abs(resid - np.nanmedian(resid))) * 1.4826
        keep = np.abs(resid) <= max(3 * mad, 0.02)
        if keep.sum() >= min_strikes and keep.sum() < len(q):
            surf2 = S.fit_surface(q[keep], min_quotes=min_strikes, kind="ssvi")
            if surf2 is not None:
                surf = surf2
        exp_of_T = dict(zip(q["T"].round(9), q["expiry_ms"]))
        for sl in surf.slices:
            rows.append({"day": day, "expiry_ms": int(exp_of_T.get(round(sl.T, 9), 0)), "T": sl.T, "F": F, "theta": sl.theta, "rho": sl.rho, "psi": sl.psi, "n": sl.n, "rmse_vol": sl.rmse_vol, "max_err_vol": sl.max_err_vol, "butterfly_ok": sl.butterfly_ok, "g_min": sl.g_min, "atm_vol": sl.atm_vol(), "rr25": sl.skew_25(), "calendar_ok": surf.calendar_ok})
    return pd.DataFrame(rows)


def calendar_stats_by_day(slices: pd.DataFrame) -> pd.DataFrame:
    """per day: is total variance monotone across the fitted expiries (numerical calendar check on the fitted slices)"""
    out = []
    for day, g in slices.groupby("day"):
        g = g.sort_values("T")
        if "calendar_ok" in g:
            ok = bool(g["calendar_ok"].all()); gap = np.nan
        else:
            sl = [S.Slice(T=r.T, F=r.F, kind="ssvi", params=(r.theta, r.rho, r.psi), k_range=(-0.5, 0.5)) for r in g.itertuples()]
            ok, gap = S.calendar_check(sl) if len(sl) >= 2 else (True, np.nan)
        out.append({"day": day, "n_slices": len(g), "calendar_ok": ok, "calendar_min_gap": gap, "rmse_vol_points": float(np.sqrt(np.average(g["rmse_vol"] ** 2, weights=g["n"])) * 100), "butterfly_violations": int((~g["butterfly_ok"]).sum())})
    return pd.DataFrame(out)
