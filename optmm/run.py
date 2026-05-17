"""The pipeline: tape -> parquet; surfaces on the tape; historical surfaces from the trade prints; listed-chain surfaces;
the three quoters on the tape with sensitivities; the microstructure studies; the RFQ examples; results/*.json and the
tables the figures are drawn from."""
from __future__ import annotations

import datetime as dt
import json
import os
import time                 # monotonic: the recording machine's wall clock jumps

import numpy as np
import pandas as pd

from . import chains as C, data as D, marks as M, micro as X, mm, rfq as R, surface as S

RESULTS = os.environ.get("OPTMM_RESULTS") or os.path.join(D.ROOT, "results")
HIST_MONTHS_QUICK = 3


def to_json(obj, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
            return str(o)[:19]
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return str(o)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, default=default)


def split_tape():
    tick = D.load_tape("ticker"); trades = D.load_tape("trades"); index = D.load_tape("index")
    if tick.empty:
        return tick, trades, index, tick, tick
    perp = tick[tick["instrument"].str.endswith("PERPETUAL")]; opt = tick[~tick["instrument"].str.endswith("PERPETUAL")]
    return opt, trades, index, perp, tick


# ---- surfaces ---------------------------------------------------------------------------------------------------------------------
def tape_surface_study(opt: pd.DataFrame, step_s: int = 60, verbose: bool = True) -> dict:
    t0 = time.monotonic(); slices, reps = M.tape_surfaces(opt, step_s=step_s)
    if reps.empty:
        return {"n_surfaces": 0}
    os.makedirs(D.DER, exist_ok=True); slices.to_parquet(os.path.join(D.DER, "surfaces.parquet"), index=False); reps.to_parquet(os.path.join(D.DER, "surface_reports.parquet"), index=False)
    out = {"n_surfaces": int(len(reps)), "n_slices": int(len(slices)), "step_s": step_s, "hours": float((reps["ts"].max() - reps["ts"].min()) / 3600e3), "rmse_vol_points_mean": float(reps["rmse_vol_points"].mean()), "rmse_vol_points_median": float(reps["rmse_vol_points"].median()), "max_err_vol_points_median": float(reps["max_err_vol_points"].median()),
           "butterfly_violations_total": int(reps["butterfly_violations"].sum()), "calendar_ok_share": float(reps["calendar_ok"].mean()), "within_spread_share_mean": float(reps["within_spread_share"].mean()), "mark_iv_abs_diff_volpts_median": float(reps["mark_iv_abs_diff_volpts"].median()), "quotes_per_surface": float(reps["n_quotes"].mean()),
           "expiries_days": sorted(set(int(x) for x in np.round(slices["T"] * 365.25))), "atm_vol_by_expiry": {str(d): float(g["atm_vol"].mean()) for d, g in slices.groupby(np.round(slices["T"] * 365.25).astype(int))}, "rr25_by_expiry": {str(d): float(g["rr25"].mean()) for d, g in slices.groupby(np.round(slices["T"] * 365.25).astype(int))}, "seconds": round(time.monotonic() - t0, 1)}
    if verbose:
        print("tape surfaces:", {k: v for k, v in out.items() if k not in ("atm_vol_by_expiry", "rr25_by_expiry")})
    return out


def history_surface_study(months: list | None = None, verbose: bool = True) -> dict:
    t0 = time.monotonic(); months = months or D.trade_months()
    if not months:
        return {"n_slices": 0}, None
    # month by month: the whole history is ~25 M prints; the surfaces need the two hours before settlement and the
    # block study every block print and a tenth of the screen prints
    cols = ["timestamp", "instrument_name", "price", "iv", "index_price", "amount", "direction", "block_trade_id", "mark_price"]
    sls = []; samples = []; n_used = 0
    for mth in months:
        w = D.load_trades(months=[mth], columns=cols, where=D.SETTLEMENT_WINDOW_SQL)
        if len(w):
            w = D.add_instrument_columns(w, "instrument_name"); n_used += len(w); s_ = M.daily_surfaces_from_trades(w)
            if len(s_):
                sls.append(s_)
        b = D.load_trades(months=[mth], columns=cols, where=D.BLOCK_SAMPLE_SQL)
        if len(b):
            samples.append(b)
        if verbose and len(sls) % 12 == 0 and len(sls):
            print("  history", mth, sum(len(x) for x in sls), "slices")
    if not sls:
        return {"n_slices": 0}, None
    sl = pd.concat(sls, ignore_index=True); tr = D.add_instrument_columns(pd.concat(samples, ignore_index=True), "instrument_name") if samples else None
    days = M.calendar_stats_by_day(sl)
    sl.to_parquet(os.path.join(D.DER, "slices.parquet"), index=False); days.to_parquet(os.path.join(D.DER, "slice_days.parquet"), index=False)
    by_year = {str(y): {"days": int(len(g)), "rmse_vol_points": float(g["rmse_vol_points"].mean()), "calendar_ok_share": float(g["calendar_ok"].mean())} for y, g in days.groupby(days["day"].dt.year)}
    out = {"n_slices": int(len(sl)), "n_days": int(len(days)), "months": [months[0], months[-1]], "trades_used": int(n_used), "rmse_vol_points_mean": float(days["rmse_vol_points"].mean()), "rmse_vol_points_median": float(days["rmse_vol_points"].median()), "butterfly_violations": int((~sl["butterfly_ok"]).sum()), "calendar_ok_share": float(days["calendar_ok"].mean()), "by_year": by_year,
           "atm_vol_percentiles": {f"p{p}": float(np.percentile(sl["atm_vol"], p)) for p in (5, 25, 50, 75, 95)}, "rr25_percentiles": {f"p{p}": float(np.percentile(sl["rr25"], p)) for p in (5, 25, 50, 75, 95)}, "seconds": round(time.monotonic() - t0, 1)}
    if verbose:
        print("history surfaces:", {k: v for k, v in out.items() if k not in ("by_year", "atm_vol_percentiles", "rr25_percentiles")})
    return out, tr


def chain_study(symbols=("SPY", "AAPL", "NVDA"), verbose: bool = True) -> dict:
    t0 = time.monotonic(); rates = D.load_rates(); out = {}; all_days = []; all_slices = []
    for sym in symbols:
        ch = D.load_chains(sym)
        if ch.empty:
            continue
        sl, days = C.fit_history(ch, rates, sym, kind="svi")
        fitted = days[days["n_slices"] > 0]
        out[sym] = {"days": int(len(days)), "days_fitted": int(len(fitted)), "from": str(days["date"].min().date()), "to": str(days["date"].max().date()), "slices": int(len(sl)), "quotes": int(fitted["n_quotes"].sum()), "rmse_vol_points_mean": float(fitted["rmse_vol_points"].mean()), "rmse_vol_points_median": float(fitted["rmse_vol_points"].median()), "max_err_vol_points_median": float(fitted["max_err_vol_points"].median()),
                    "within_spread_share_mean": float(fitted["within_spread_share"].mean()), "rmse_half_spreads_median": float(fitted["rmse_half_spreads"].median()), "butterfly_violations": int((~sl["butterfly_ok"]).sum()) if len(sl) else 0, "calendar_ok_share": float(fitted["calendar_ok"].mean()), "vendor_iv_abs_diff_volpts_median": float(fitted["vendor_iv_abs_diff_volpts"].median()), "atm_vol_front_median": float(fitted["atm_vol_front"].median()), "rr25_front_median": float(fitted["rr25_front"].median())}
        all_days.append(days); all_slices.append(sl)
        if verbose:
            print("chains", sym, out[sym])
    if all_days:
        pd.concat(all_days).to_parquet(os.path.join(D.DER, "chain_days.parquet"), index=False)
        cs = pd.concat(all_slices); cs["params"] = cs["params"].astype(str); cs.to_parquet(os.path.join(D.DER, "chain_fits.parquet"), index=False)
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


# ---- market making ---------------------------------------------------------------------------------------------------------------------
def mm_study(opt, trades, index, perp, quick: bool = False, verbose: bool = True) -> dict:
    t0 = time.monotonic(); results = {}; paths = {}; fills = {}
    runs = [("market", mm.MMConfig(quoter="market")), ("surface", mm.MMConfig(quoter="surface")), ("aware", mm.MMConfig(quoter="aware"))]
    if not quick:
        runs += [("surface_pure", mm.MMConfig(quoter="surface", blend=0.0)), ("aware_pure", mm.MMConfig(quoter="aware", blend=0.0)), ("aware_wide", mm.MMConfig(quoter="aware", spread_mult=1.5)), ("aware_tight", mm.MMConfig(quoter="aware", spread_mult=0.7)), ("aware_lean_half", mm.MMConfig(quoter="aware", skew_max=0.0075)), ("aware_lean_double", mm.MMConfig(quoter="aware", skew_max=0.03)), ("aware_nofees", mm.MMConfig(quoter="aware", fees=False)), ("market_nofees", mm.MMConfig(quoter="market", fees=False)),
                 ("aware_slow", mm.MMConfig(quoter="aware", requote_bp=0.0)), ("aware_noprot", mm.MMConfig(quoter="aware", requote_bp=0.0, pull_bp=0.0, vega_hard=0.0)), ("market_noprot", mm.MMConfig(quoter="market", requote_bp=0.0, pull_bp=0.0, vega_hard=0.0))]
    for name, cfg in runs:
        r = mm.run_mm(opt, trades, index, perp, cfg)
        results[name] = r["summary"] | {"config": {k: v for k, v in cfg.__dict__.items()}}
        paths[name] = r["path"].assign(quoter=name); fills[name] = r["fills"].assign(quoter=name)
        if verbose:
            s = r["summary"]; print(f"  mm {name:14s} fills {s['fills']:4d} pnl {s['pnl_usd']:9.0f} spread {s['spread_capture_usd']:8.0f} markout5 {s['markout_5m_usd']:8.0f} hedged {s['markout_dh_5m_usd']:8.0f} fees {s['fees_usd']:7.0f} hedge {s['hedge_pnl_usd']:7.0f} vega_max {s['max_abs_vega']:6.0f} pulls {s['n_pulls']}")
    if paths:
        pd.concat(paths.values()).to_parquet(os.path.join(D.DER, "mm_pnl.parquet"), index=False)
        f = pd.concat(fills.values())
        if len(f):
            f.to_parquet(os.path.join(D.DER, "mm_fills.parquet"), index=False)
    m, s_, a = results.get("market"), results.get("surface"), results.get("aware")
    comp = {}
    if m and s_ and a:
        comp = {"pnl_market": m["pnl_usd"], "pnl_surface": s_["pnl_usd"], "pnl_aware": a["pnl_usd"], "pnl_per_contract_market": m["pnl_per_contract_usd"], "pnl_per_contract_surface": s_["pnl_per_contract_usd"], "pnl_per_contract_aware": a["pnl_per_contract_usd"],
                "adverse_selection_5m_market": m["adverse_selection_5m_usd"], "adverse_selection_5m_surface": s_["adverse_selection_5m_usd"], "adverse_selection_5m_aware": a["adverse_selection_5m_usd"], "fills_market": m["fills"], "fills_surface": s_["fills"], "fills_aware": a["fills"], "max_abs_vega_market": m["max_abs_vega"], "max_abs_vega_aware": a["max_abs_vega"],
                "hours": a["hours"]}
    return {"runs": results, "comparison": comp, "seconds": round(time.monotonic() - t0, 1)}


# ---- microstructure and RFQ ------------------------------------------------------------------------------------------------------------
def micro_study(opt, trades, index, hist_trades: pd.DataFrame | None, verbose: bool = True) -> dict:
    t0 = time.monotonic(); out = {}
    df, summ = X.pickoff_study(opt, index); out["pickoff"] = summ
    if len(df):
        df.to_parquet(os.path.join(D.DER, "pickoff.parquet"), index=False)
    sd = X.spread_depth(opt); sd.to_parquet(os.path.join(D.DER, "spread_depth.parquet"), index=False); out["spread_depth"] = sd.to_dict(orient="records")
    out["screen_prints"] = X.screen_print_stats(trades)
    if hist_trades is not None and len(hist_trades):
        t, bs_ = X.block_trades_vs_screen(hist_trades, screen_weight=D.SCREEN_SAMPLE_WEIGHT); out["blocks"] = bs_
        t[["timestamp", "instrument_name", "is_block", "amount", "diff_pct", "diff_volpts", "signed_pct", "size_bucket", "money", "T"]].to_parquet(os.path.join(D.DER, "block_prints.parquet"), index=False)
    out["seconds"] = round(time.monotonic() - t0, 1)
    if verbose:
        print("micro:", {k: v for k, v in out["pickoff"].items() if k != "by_bucket"}, "| blocks:", {k: out.get("blocks", {}).get(k) for k in ("block_share_of_contracts", "block_share_of_trades")})
    return out


def rfq_examples(opt) -> dict:
    ts = int(opt["ts"].max()); surf, q = M.fit_snapshot(opt, ts)
    if surf is None:
        return {}
    idx = float(q["index"].iloc[-1]); F0 = surf.slices[0].F
    def half(K, T):
        # the screen's half-spread in vol at the nearest quoted strike/expiry
        qq = q.dropna(subset=["iv_bid", "iv_ask"]); qq = qq.iloc[(np.abs(np.log(qq["K"] / K)) + 10 * np.abs(qq["T"] - T)).argsort()[:3]]
        return float(np.clip(0.5 * (qq["iv_ask"] - qq["iv_bid"]).median(), 0.004, 0.08)) if len(qq) else 0.01
    Ts = [s.T for s in surf.slices]; T1 = Ts[min(1, len(Ts) - 1)]; T2 = Ts[-1]
    K_atm = float(round(F0 / 1000) * 1000); Kp = K_atm * 0.95; Kc = K_atm * 1.05
    ex = {"asof": ts, "index": idx, "expiries_days": [round(T * 365.25, 2) for T in Ts]}
    for name, legs in (("straddle", R.structure("straddle", K_atm, T1)), ("strangle", R.structure("strangle", round(Kp / 1000) * 1000, round(Kc / 1000) * 1000, T1)), ("risk_reversal", R.structure("risk_reversal", round(Kp / 1000) * 1000, round(Kc / 1000) * 1000, T1)), ("butterfly", R.structure("butterfly", round(Kp / 1000) * 1000, K_atm, round(Kc / 1000) * 1000, T1)), ("calendar", R.structure("calendar", K_atm, T1, T2))):
        p = R.price_structure(surf, legs, half, size=10.0, index=idx); ex[name] = {"quote": R.describe(p), **{k: v for k, v in p.items() if k != "legs"}, "legs": p["legs"]}
    return ex


# ---- everything --------------------------------------------------------------------------------------------------------------------------
def run_all(quick: bool = False, verbose: bool = True) -> dict:
    t0 = time.monotonic(); os.makedirs(RESULTS, exist_ok=True); os.makedirs(D.DER, exist_ok=True)
    tape_stats = D.build_tape(verbose=verbose)
    opt, trades, index, perp, tick = split_tape()
    out = {"quick": quick, "tape": {"hours": D.tape_hours(), "ticker_rows": int(len(opt)), "instruments": int(opt["instrument"].nunique()) if len(opt) else 0, "prints": int(len(trades)), "index_rows": int(len(index)), "from": str(pd.to_datetime(opt["ts"].min(), unit="ms", utc=True)) if len(opt) else None, "to": str(pd.to_datetime(opt["ts"].max(), unit="ms", utc=True)) if len(opt) else None, "tape_hours_span": float((opt["ts"].max() - opt["ts"].min()) / 3600e3) if len(opt) else 0.0}}
    if len(opt):
        out["tape_surfaces"] = tape_surface_study(opt, step_s=300 if quick else 60, verbose=verbose)
    months = D.trade_months(); hist_tr = None
    if months:
        hs, hist_tr = history_surface_study(months[-HIST_MONTHS_QUICK:] if quick else months, verbose=verbose); out["history_surfaces"] = hs
    out["chains"] = chain_study(("SPY",) if quick else ("SPY", "AAPL", "NVDA"), verbose=verbose)
    if len(opt) and len(perp):
        out["mm"] = mm_study(opt, trades, index, perp, quick=quick, verbose=verbose)
        out["micro"] = micro_study(opt, trades, index, hist_tr, verbose=verbose)
        out["rfq"] = rfq_examples(opt)
    out["seconds"] = round(time.monotonic() - t0, 1)
    to_json(out, os.path.join(RESULTS, "run.json"))
    con = D.connect(); con.close()
    if verbose:
        print("done in", out["seconds"], "s")
    return out
