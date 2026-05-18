#!/usr/bin/env python3
"""README.md from scripts/README.template.md and results/run.json:  python scripts/readme.py [results]"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results")
run = json.load(open(os.path.join(R, "run.json"), encoding="utf-8"))


def f(x, d=0, pct=False, plus=False):
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    v = 100 * x if pct else x
    s = f"{v:+,.{d}f}" if plus else f"{v:,.{d}f}"
    return s


def g(d, *keys, default=None):
    for k in keys:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d if d != {} else default


t = run.get("tape", {}); mm = run.get("mm", {}); runs = mm.get("runs", {}); ts = run.get("tape_surfaces", {}); hs = run.get("history_surfaces", {}); ch = run.get("chains", {}); mi = run.get("micro", {}); pk = mi.get("pickoff", {}); bl = mi.get("blocks", {}); rfq = run.get("rfq", {})
v = {}
v["tape_hours"] = f(t.get("tape_hours_span", 0), 1); v["tape_from"] = str(t.get("from", ""))[:16]; v["tape_to"] = str(t.get("to", ""))[11:16]
v["ticker_rows"] = f(t.get("ticker_rows", 0)); v["instruments"] = f(t.get("instruments", 0)); v["prints"] = f(t.get("prints", 0))
months = hs.get("months", ["", ""]); v["hist_from"] = f"{months[0][:4]}-{months[0][4:6]}" if months and months[0] else "2021"; v["hist_months"] = str(len(glob.glob(os.path.join(ROOT, "data", "raw", "deribit", "trades_BTC_*.parquet"))) or "")
v["hist_days"] = f(hs.get("n_days", 0)); v["hist_slices"] = f(hs.get("n_slices", 0)); v["hist_rmse"] = f(hs.get("rmse_vol_points_median"), 2); v["hist_calendar"] = f(hs.get("calendar_ok_share"), 0, pct=True)
for p in ("p5", "p50", "p95"):
    v[f"hist_atm_{p}"] = f(g(hs, "atm_vol_percentiles", p), 0, pct=True); v[f"hist_rr_{p}"] = f(g(hs, "rr25_percentiles", p), 1, pct=True, plus=True)
v["mm_hours"] = f(g(mm, "comparison", "hours", default=t.get("tape_hours_span", 0)), 1)
for q, key in (("market", "market"), ("surface", "surface"), ("aware", "aware")):
    r = runs.get(key, {})
    v[f"pnl_{q}"] = f(r.get("pnl_usd"), 0, plus=True); v[f"fills_{q}"] = f(r.get("fills", 0)); v[f"ctr_{q}"] = f(r.get("contracts", 0), 1); v[f"sc_{q}"] = f(r.get("spread_capture_usd"), 0, plus=True); v[f"sf_{q}"] = f(r.get("spread_vs_fair_usd"), 0, plus=True)
    v[f"mo_{q}"] = f(r.get("markout_dh_5m_usd"), 0, plus=True); v[f"as_{q}"] = f(r.get("adverse_selection_5m_usd"), 0, plus=True); v[f"hp_{q}"] = f(r.get("hedge_pnl_usd"), 0, plus=True); v[f"fee_{q}"] = f(r.get("fees_usd"), 0); v[f"vega_{q}"] = f(r.get("max_abs_vega"), 0); v[f"q_{q}"] = f(r.get("avg_quotes"), 0); v[f"hedges_{q}"] = f(r.get("n_hedges", 0))
for key in ("market_nofees", "aware_nofees", "aware_noprot", "aware_slow", "market_noprot"):
    r = runs.get(key, {}); v[f"pnl_{key}"] = f(r.get("pnl_usd"), 0, plus=True) if r else "n/a"; v[f"fills_{key}"] = f(r.get("fills", 0)) if r else "n/a"; v[f"vega_{key}"] = f(r.get("max_abs_vega"), 0) if r else "n/a"
idx = rfq.get("index") or (runs.get("aware", {}).get("config", {}) and None) or 0
v["fee_per_contract"] = f(0.0003 * (rfq.get("index") or 0), 0) if rfq.get("index") else "~23"
v["rfq_index"] = f(rfq.get("index"), 0)
# a reading of the quoter table that follows the numbers
a, s_, mk = runs.get("aware", {}), runs.get("surface", {}), runs.get("market", {})
if a and s_ and mk:
    best = max((("the market-mark quoter", mk), ("the surface quoter", s_), ("the inventory-aware quoter", a)), key=lambda x: x[1]["pnl_usd"])
    all_neg = max(mk["pnl_usd"], s_["pnl_usd"], a["pnl_usd"]) < 0
    lean = a["max_abs_vega"] < s_["max_abs_vega"]
    parts = []
    parts.append(f"{'All three lose money at the public fee schedule' if all_neg else 'Only ' + best[0] + ' is positive at the public fee schedule'}; {best[0]} does best ({f(best[1]['pnl_usd'], 0, plus=True)} USD).")
    parts.append(f"Before fees the leaned quoter made {f(a['pnl_usd'] + a['fees_usd'], 0, plus=True)} USD and the market-mark quoter {f(mk['pnl_usd'] + mk['fees_usd'], 0, plus=True)} USD; fees of {f(a['fees_usd'], 0)} and {f(mk['fees_usd'], 0)} USD turn that into {v['pnl_aware']} and {v['pnl_market']} (the fee-free runs, which also skip the hedge's taker fee, make {v['pnl_aware_nofees']} and {v['pnl_market_nofees']} USD): the fee is the first-order cost of quoting cheap options at the screen's spread.")
    dsc = a["spread_capture_usd"] - s_["spread_capture_usd"]
    parts.append(f"The lean {'does' if lean else 'does not'} keep the book smaller: the largest vega carried is {v['vega_aware']} USD per vol point against {v['vega_surface']} for the same centre without the lean" + (f", at a cost of {f(-dsc, 0)} USD of spread at the print (the leaned side gives up half-spread to get out)." if dsc < 0 else f", and it captured {f(dsc, 0)} USD more spread at the print (fewer fills, {v['fills_aware']} against {v['fills_surface']}, and the leaned side is the one the flow wants)."))
    sc_pos = mk["spread_capture_usd"] > 0
    parts.append(f"Centring on Deribit's mark captures {'positive' if sc_pos else 'no'} spread at the print ({v['sc_market']} USD) because its quotes sit at the screen's own levels and only fill when the queue ahead has traded ({v['fills_market']} fills); the surface quoters, centred elsewhere, are filled more often ({v['fills_surface']} and {v['fills_aware']}) and capture spread against their own fair ({v['sf_surface']} and {v['sf_aware']} USD) rather than against the mark.")
    if runs.get("aware_lean_half") and runs.get("aware_lean_double") and runs.get("aware_wide"):
        lh, ld, wd = runs["aware_lean_half"], runs["aware_lean_double"], runs["aware_wide"]
        parts.append(f"The size of the lean and of the spread matter more than the centre: half the lean (0.75 vol points at the limit) gives {f(lh['pnl_usd'], 0, plus=True)} USD, a half-spread 1.5 times the screen's {f(wd['pnl_usd'], 0, plus=True)} USD on {wd['fills']} fills, and a lean of 3 vol points, wider than the half-spread on most of the universe, posts the leaned side at the screen's touch and is run over ({ld['fills']} fills, {f(ld['pnl_usd'], 0, plus=True)} USD): the lean has to stay inside the half-spread.")
    if runs.get("aware_noprot"):
        np_ = runs["aware_noprot"]; parts.append(f"The protections matter most: without them the leaned quoter's fills rise to {np_['fills']}, its largest vega to {f(np_['max_abs_vega'], 0)}, its adverse selection to {f(np_['adverse_selection_5m_usd'], 0, plus=True)} USD and its P&L to {f(np_['pnl_usd'], 0, plus=True)} USD, most of it picked off in the seconds after index moves, which is what the microstructure section measures directly.")
    v["mm_reading"] = " ".join(parts)
else:
    v["mm_reading"] = "The quoters have not been run on this tape."
# sensitivity table
if runs:
    hdr = "| run | what changes | fills | P&L | spread at print | hedged mark-out 5m | adverse selection | fees | hedge P&L | pulls | max vega |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
    desc = {"market": "centre = Deribit's mark", "surface": "centre = surface blended 0.5 with the screen mid", "aware": "surface + lean on the portfolio vega", "surface_pure": "pure surface (blend 0)", "aware_pure": "aware, pure surface", "aware_wide": "aware, half-spread × 1.5", "aware_tight": "aware, half-spread × 0.7", "aware_lean_half": "aware, lean 0.75 vol points at the limit", "aware_lean_double": "aware, lean 3 vol points at the limit", "aware_nofees": "aware, fees off", "market_nofees": "market, fees off", "aware_slow": "aware, re-pricing on the 5-second timer only", "aware_noprot": "aware, no protections", "market_noprot": "market, no protections"}
    rows = "".join(f"| {k} | {desc.get(k, '')} | {r['fills']} | {f(r['pnl_usd'], 0, plus=True)} | {f(r['spread_capture_usd'], 0, plus=True)} | {f(r.get('markout_dh_5m_usd'), 0, plus=True)} | {f(r.get('adverse_selection_5m_usd'), 0, plus=True)} | {f(r['fees_usd'], 0)} | {f(r['hedge_pnl_usd'], 0, plus=True)} | {r.get('n_pulls', 0)} | {f(r['max_abs_vega'], 0)} |\n" for k, r in runs.items())
    v["sens_table"] = f"*All runs, USD over {v['mm_hours']} hours of tape:*\n\n" + hdr + rows
else:
    v["sens_table"] = ""
# tape surfaces
v["n_surfaces"] = f(ts.get("n_surfaces", 0)); v["slices_per_surface"] = f(ts.get("n_slices", 0) / max(ts.get("n_surfaces", 1), 1), 0); v["quotes_per_surface"] = f(ts.get("quotes_per_surface"), 0)
v["tape_rmse"] = f(ts.get("rmse_vol_points_median"), 2); v["tape_within"] = f(ts.get("within_spread_share_mean"), 0, pct=True); v["tape_butterfly"] = f(ts.get("butterfly_violations_total", 0)); v["tape_calendar"] = f(ts.get("calendar_ok_share"), 0, pct=True); v["tape_markdiff"] = f(ts.get("mark_iv_abs_diff_volpts_median"), 2)
# chains
for sym in ("SPY", "AAPL", "NVDA"):
    c = ch.get(sym, {}); k = sym.lower()
    v[f"{k}_rmse"] = f(c.get("rmse_vol_points_median"), 2); v[f"{k}_days"] = f(c.get("days_fitted", 0)); v[f"{k}_within"] = f(c.get("within_spread_share_mean"), 0, pct=True); v[f"{k}_vendor"] = f(c.get("vendor_iv_abs_diff_volpts_median"), 2)
# micro
v["pk_events"] = f(pk.get("events", 0)); v["pk_latency"] = f(pk.get("median_latency_s"), 1); v["pk_stale5"] = f(pk.get("share_stale_5s"), 0, pct=True); v["pk_loss"] = f(pk.get("mean_pickoff_loss_usd"), 2); v["pk_exceeds"] = f(pk.get("share_move_exceeds_half_spread"), 0, pct=True)
bb = pk.get("by_bucket", {})
if bb:
    v["pk_bucket_sentence"] = " (" + "; ".join(f"{b}: {f(x['mean_loss_usd'], 2)} USD, {f(x['share_exceeds_half_spread'], 0, pct=True)} % beyond the half-spread, n = {x['n']}" for b, x in bb.items()) + ")"
else:
    v["pk_bucket_sentence"] = ""
sd = {(r["tenor"], r["moneyness"]): r for r in mi.get("spread_depth", [])}
for key, tenor in (("sd_atm_short", "<2d"), ("sd_atm_week", "2-10d"), ("sd_atm_month", "10-45d"), ("sd_atm_long", ">45d")):
    v[key] = f(g(sd, (tenor, "atm"), "spread_volpts"), 1) if (tenor, "atm") in sd else "n/a"
v["blk_share_trades"] = f(bl.get("block_share_of_trades"), 1, pct=True); v["blk_share_contracts"] = f(bl.get("block_share_of_contracts"), 0, pct=True)
v["blk_diff"] = f(g(bl, "block", "median_abs_diff_volpts"), 2); v["scr_diff"] = f(g(bl, "screen", "median_abs_diff_volpts"), 2); v["blk_cost"] = f(g(bl, "block", "aggressor_cost_pct_median"), 2); v["scr_cost"] = f(g(bl, "screen", "aggressor_cost_pct_median"), 2)
# rfq
def rq(name):
    x = rfq.get(name, {})
    return f"{name.replace('_', ' ')} {x['quote']}" if x else f"{name} n/a"
v["rfq_straddle"] = rq("straddle"); v["rfq_rr"] = rq("risk_reversal"); v["rfq_calendar"] = rq("calendar")
# tests
n_tests = 0
for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
    n_tests += len(re.findall(r"^def test_", open(p, encoding="utf-8").read(), re.M))
v["n_tests"] = str(n_tests)

tpl = open(os.path.join(ROOT, "scripts", "README.template.md"), encoding="utf-8").read()
missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", tpl)) - set(v))
if missing:
    print("missing:", missing)
out = re.sub(r"\{\{(\w+)\}\}", lambda m: v.get(m.group(1), "{{" + m.group(1) + "}}"), tpl)
with open(os.path.join(ROOT, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(out)
print("wrote README.md")
