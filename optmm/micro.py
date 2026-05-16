"""Microstructure of quoting: how fast the screen re-marks after the underlying moves and what a stale quote would have
lost (pick-off), spread and depth by moneyness and tenor from the tape, and how block prints sit against the screen in
the trade history."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import bs, iv as iv_solver, marks as M

YEAR_MS = M.YEAR_MS


def index_moves(index: pd.DataFrame, window_s: int = 30, bp_threshold: float = 5.0) -> pd.DataFrame:
    """index moves larger than the threshold over the window, non-overlapping"""
    ix = index.sort_values("ts")[["ts", "price"]].dropna()
    ts = ix["ts"].values; px = ix["price"].values
    out = []; last_end = -1
    j = 0
    for i in range(len(ts)):
        if ts[i] < last_end:
            continue
        while j < len(ts) and ts[j] < ts[i] + window_s * 1000:
            j += 1
        if j >= len(ts):
            break
        move = (px[j - 1] / px[i] - 1.0) * 1e4
        if abs(move) >= bp_threshold:
            out.append({"ts": int(ts[i]), "ts_end": int(ts[j - 1]), "move_bp": float(move), "px0": float(px[i]), "px1": float(px[j - 1])}); last_end = ts[j - 1] + window_s * 1000
    return pd.DataFrame(out)


def pickoff_study(tick: pd.DataFrame, index: pd.DataFrame, window_s: int = 30, bp_threshold: float = 5.0, max_lat_s: int = 120) -> tuple[pd.DataFrame, dict]:
    """For every index move: per quoted option, the time until its best bid or ask changed, and the loss a quote left at
    the old level would have suffered against the move (delta x dS, per contract, less the half-spread), in USD and in
    vol points.  Returns the per-(event, instrument) table and a summary by move-size bucket."""
    ev = index_moves(index, window_s, bp_threshold)
    if ev.empty:
        return pd.DataFrame(), {"events": 0}
    opt = tick[tick["cp"].notna() & (tick["bid"] > 0) & (tick["ask"] > 0)].sort_values("ts")
    rows = []
    by_ins = {ins: g for ins, g in opt.groupby("instrument", sort=False)}
    for e in ev.itertuples():
        for ins, g in by_ins.items():
            ts_arr = g["ts"].values
            k0 = np.searchsorted(ts_arr, e.ts, side="right") - 1
            if k0 < 0 or e.ts - ts_arr[k0] > 300e3:
                continue
            r0 = g.iloc[k0]
            T = (r0["expiry_ms"] - e.ts) / YEAR_MS
            if T <= 0 or abs(np.log(r0["strike"] / r0["underlying"])) > 0.15:
                continue
            after = g.iloc[k0 + 1:]
            after = after[after["ts"] > e.ts]
            changed = after[(after["bid"] != r0["bid"]) | (after["ask"] != r0["ask"])]
            lat = (changed["ts"].iloc[0] - e.ts) / 1000.0 if len(changed) else np.nan
            dS = e.px1 - e.px0
            dmark = r0["delta"] * dS                                                      # USD per contract, first order
            half_spread_usd = 0.5 * (r0["ask"] - r0["bid"]) * r0["index"]
            loss = max(abs(dmark) - half_spread_usd, 0.0)                                # what a picked-off stale quote gives up
            vega = max(r0["vega"], 1e-9)
            rows.append({"ts": e.ts, "move_bp": e.move_bp, "instrument": ins, "T_days": T * 365.25, "k": float(np.log(r0["strike"] / r0["underlying"])), "delta": r0["delta"], "latency_s": lat, "stale_5s": bool(np.isnan(lat) or lat > 5), "stale_30s": bool(np.isnan(lat) or lat > 30), "dmark_usd": dmark, "half_spread_usd": half_spread_usd, "pickoff_loss_usd": loss, "pickoff_loss_volpts": loss / vega})
    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"events": int(len(ev))}
    df["bucket"] = pd.cut(df["move_bp"].abs(), [0, 8, 12, 20, 1e9], labels=["5-8bp", "8-12bp", "12-20bp", ">20bp"])
    summ = {"events": int(len(ev)), "window_s": window_s, "threshold_bp": bp_threshold, "pairs": int(len(df)), "median_latency_s": float(df["latency_s"].median()), "share_stale_5s": float(df["stale_5s"].mean()), "share_stale_30s": float(df["stale_30s"].mean()),
            "mean_pickoff_loss_usd": float(df["pickoff_loss_usd"].mean()), "mean_pickoff_loss_volpts": float(df["pickoff_loss_volpts"].mean()), "share_move_exceeds_half_spread": float((df["pickoff_loss_usd"] > 0).mean()),
            "by_bucket": {str(b): {"n": int(len(g)), "median_latency_s": float(g["latency_s"].median()), "share_stale_5s": float(g["stale_5s"].mean()), "mean_loss_usd": float(g["pickoff_loss_usd"].mean()), "share_exceeds_half_spread": float((g["pickoff_loss_usd"] > 0).mean())} for b, g in df.groupby("bucket", observed=True)}}
    return df, summ


def spread_depth(tick: pd.DataFrame) -> pd.DataFrame:
    """time-weighted spread (in vol points and in % of mid) and best-level depth by tenor and moneyness bucket"""
    d = tick[tick["cp"].notna() & (tick["bid"] > 0) & (tick["ask"] > 0)].copy().sort_values(["instrument", "ts"])
    d["T"] = (d["expiry_ms"] - d["ts"]) / YEAR_MS
    d = d[d["T"] > 0]
    d["mid"] = 0.5 * (d["bid"] + d["ask"]); d["rel_spread"] = (d["ask"] - d["bid"]) / d["mid"]
    d["spread_volpts"] = np.where((d["ask_iv"] > 0) & (d["bid_iv"] > 0), d["ask_iv"] - d["bid_iv"], np.nan)
    d["k_sig"] = np.log(d["strike"] / d["underlying"]) / (np.maximum(d["mark_iv"], 1) / 100.0 * np.sqrt(d["T"]))
    d["dt"] = d.groupby("instrument")["ts"].diff().shift(-1).fillna(0).clip(0, 60e3) / 1000.0
    d["tenor"] = pd.cut(d["T"] * 365.25, [0, 2, 10, 45, 400], labels=["<2d", "2-10d", "10-45d", ">45d"])
    d["money"] = pd.cut(d["k_sig"], [-99, -1.5, -0.5, 0.5, 1.5, 99], labels=["deep put", "put", "atm", "call", "deep call"])
    def wmed(g, col):
        g = g.dropna(subset=[col]); g = g[g["dt"] > 0]
        if g.empty:
            return np.nan
        o = np.argsort(g[col].values); c = np.cumsum(g["dt"].values[o]); return float(g[col].values[o][np.searchsorted(c, 0.5 * c[-1])])
    out = []
    for (tn, mo), g in d.groupby(["tenor", "money"], observed=True):
        out.append({"tenor": str(tn), "moneyness": str(mo), "instruments": int(g["instrument"].nunique()), "spread_volpts": wmed(g, "spread_volpts"), "rel_spread_pct": 100 * wmed(g, "rel_spread"), "bid_size": wmed(g, "bid_size"), "ask_size": wmed(g, "ask_size"), "two_sided_share": float(np.average(((g["bid"] > 0) & (g["ask"] > 0)).astype(float), weights=np.maximum(g["dt"], 1e-9)))})
    return pd.DataFrame(out)


def block_trades_vs_screen(trades: pd.DataFrame, max_T_days: float = 400, screen_weight: float = 1.0) -> tuple[pd.DataFrame, dict]:
    """Block prints (flagged by Deribit) against the mark at the time of the print, compared with screen prints: the
    price difference in % of the mark and in vol points (our solver on both, forward = index), by size bucket and
    moneyness.  Positive = the print is above the mark (the buyer paid up).  screen_weight: how many screen prints
    each screen row stands for when the input is a sample of them (the shares are reweighted; the medians are not)."""
    t = trades[(trades["price"] > 0) & (trades["mark_price"] > 0) & trades["cp"].notna()].copy()
    t["is_block"] = t["block_trade_id"].notna() & (t["block_trade_id"].astype(str) != "None")
    t["T"] = (t["expiry_ms"] - t["timestamp"]) / YEAR_MS
    t = t[(t["T"] > 1e-3) & (t["T"] * 365.25 <= max_T_days)]
    t["k"] = np.log(t["strike"] / t["index_price"])
    t["diff_pct"] = (t["price"] / t["mark_price"] - 1.0) * 100.0
    # both vols from our solver on the same forward (the index), so that the forward's basis cancels in the difference
    t["iv_mark"] = iv_solver.implied_vol(t["mark_price"].values * t["index_price"].values, t["index_price"].values, t["strike"].values, t["T"].values, t["cp"].values) * 100.0
    t["iv_print"] = iv_solver.implied_vol(t["price"].values * t["index_price"].values, t["index_price"].values, t["strike"].values, t["T"].values, t["cp"].values) * 100.0
    t["diff_volpts"] = t["iv_print"] - t["iv_mark"]
    t["signed_pct"] = np.where(t["direction"] == "buy", t["diff_pct"], -t["diff_pct"])          # cost to the aggressor
    t["notional_usd"] = t["amount"] * t["index_price"]
    t["size_bucket"] = pd.cut(t["amount"], [0, 1, 5, 25, 100, 1e9], labels=["<1", "1-5", "5-25", "25-100", ">100"])
    t["money"] = pd.cut(t["k"], [-9, -0.15, -0.05, 0.05, 0.15, 9], labels=["deep put", "put", "atm", "call", "deep call"])
    def agg(g):
        return {"trades": int(len(g)), "contracts": float(g["amount"].sum()), "median_abs_diff_pct": float(g["diff_pct"].abs().median()), "median_diff_volpts": float(g["diff_volpts"].median()), "mean_diff_volpts": float(g["diff_volpts"].mean()), "median_abs_diff_volpts": float(g["diff_volpts"].abs().median()), "aggressor_cost_pct_median": float(g["signed_pct"].median())}
    wts = np.where(t["is_block"], 1.0, screen_weight)
    summ = {"all": agg(t), "block": agg(t[t["is_block"]]), "screen": agg(t[~t["is_block"]]), "block_share_of_contracts": float(t.loc[t["is_block"], "amount"].sum() / max((t["amount"] * wts).sum(), 1e-9)), "block_share_of_trades": float(t["is_block"].sum() / max(wts.sum(), 1e-9)), "screen_sample_weight": screen_weight,
            "by_size": {str(b): {"block": agg(g[g["is_block"]]), "screen": agg(g[~g["is_block"]])} for b, g in t.groupby("size_bucket", observed=True)}, "by_moneyness": {str(b): {"block": agg(g[g["is_block"]]), "screen": agg(g[~g["is_block"]])} for b, g in t.groupby("money", observed=True)}}
    return t, summ


def screen_print_stats(trades: pd.DataFrame) -> dict:
    """where prints sit against the mark on the screen (the tape): aggressor cost and direction mix"""
    t = trades[(trades["price"] > 0) & (trades["mark"] > 0)].copy()
    t["diff_pct"] = (t["price"] / t["mark"] - 1.0) * 100.0; t["signed_pct"] = np.where(t["direction"] == "buy", t["diff_pct"], -t["diff_pct"])
    return {"prints": int(len(t)), "contracts": float(t["amount"].sum()), "buy_share": float((t["direction"] == "buy").mean()) if len(t) else np.nan, "median_aggressor_cost_pct": float(t["signed_pct"].median()) if len(t) else np.nan, "block_prints": int(t["block_trade_id"].notna().sum())}
