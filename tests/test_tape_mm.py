"""The tape parser on a synthetic recording, and the quoting engine on a hand-made tape: fills through the quote, the
queue ahead of us, the crossing rule, fees, the hedge, and the P&L identity."""
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from optmm import bs, data as D, marks as M, micro as X, mm, rfq as R, surface as S  # noqa: E402

T0 = 1_793_500_000_000          # ms, 2026-11-01: the 1JAN27 option has 61 days to run
EXP1 = int(pd.Timestamp("2027-01-01T08:00:00Z").value // 10 ** 6)


def ticker(ts, name, bid, ask, bs_, as_, mark, mark_iv, delta, vega, F=100000.0, index=100000.0):
    return {"t": ts, "ch": f"ticker.{name}.100ms", "d": {"timestamp": ts, "instrument_name": name, "best_bid_price": bid, "best_ask_price": ask, "best_bid_amount": bs_, "best_ask_amount": as_, "mark_price": mark, "mark_iv": mark_iv, "bid_iv": mark_iv - 2, "ask_iv": mark_iv + 2, "greeks": {"delta": delta, "gamma": 0.00001, "vega": vega, "theta": -20.0}, "underlying_price": F, "index_price": index, "open_interest": 10.0}}


def trade(ts, name, price, amount, direction):
    return {"t": ts, "ch": f"trades.{name}.100ms", "d": [{"timestamp": ts, "instrument_name": name, "price": price, "amount": amount, "direction": direction, "iv": 50.0, "mark_price": price, "index_price": 100000.0, "trade_id": str(ts), "tick_direction": 0}]}


def index(ts, px):
    return {"t": ts, "ch": "deribit_price_index.btc_usd", "d": {"timestamp": ts, "price": px, "index_name": "btc_usd"}}


def perp(ts, bid=99990.0, ask=100010.0):
    return {"t": ts, "ch": "ticker.BTC-PERPETUAL.100ms", "d": {"timestamp": ts, "instrument_name": "BTC-PERPETUAL", "best_bid_price": bid, "best_ask_price": ask, "best_bid_amount": 100.0, "best_ask_amount": 100.0, "mark_price": 0.5 * (bid + ask), "index_price": 100000.0, "funding_8h": 0.0}}


def write_tape(path, lines):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps(l) + "\n")


def test_parse_instrument():
    cur, exp, k, cp = D.parse_instrument("BTC-25SEP26-80000-C")
    assert cur == "BTC" and exp.isoformat() == "2026-09-25T08:00:00+00:00" and k == 80000.0 and cp == 1.0
    assert D.parse_instrument("BTC-PERPETUAL") is None and D.parse_instrument("ETH-3OCT26-2d5-P")[2] == 2.5


def test_tape_parser_and_build(tmp_path, monkeypatch):
    raw = tmp_path / "raw"; der = tmp_path / "der"; raw.mkdir(); der.mkdir()
    monkeypatch.setattr(D, "TAPE_RAW", str(raw)); monkeypatch.setattr(D, "TAPE", str(der))
    name = "BTC-1JAN27-100000-C"
    lines = [index(T0, 100000.0), perp(T0), ticker(T0 + 100, name, 0.05, 0.052, 5.0, 4.0, 0.051, 50.0, 0.5, 40.0), trade(T0 + 200, name, 0.05, 1.0, "sell")]
    write_tape(raw / "20270101_00.jsonl.gz", lines)
    st = D.build_tape(verbose=False)
    tk = D.load_tape("ticker"); tr = D.load_tape("trades"); ix = D.load_tape("index")
    assert st["20270101_00"]["ticker"] == 2 and len(tk) == 2 and len(tr) == 1 and len(ix) == 1
    opt = tk[tk["instrument"] == name].iloc[0]
    assert opt["bid"] == 0.05 and opt["strike"] == 100000.0 and opt["cp"] == 1.0 and opt["expiry_ms"] == int(pd.Timestamp("2027-01-01T08:00:00Z").value // 10 ** 6)
    # a second call with an unchanged file does nothing; a grown file is re-read
    assert D.build_tape(verbose=False) == {}
    write_tape(raw / "20270101_00.jsonl.gz", lines + [index(T0 + 300, 100010.0)])
    assert D.build_tape(verbose=False)["20270101_00"]["index"] == 2


def make_tape(prints):
    """one 61-day ATM call quoted 0.050 / 0.052 BTC with 5 up on the bid and 4 on the ask, plus the perp and the index,
    then the prints; the mark IV is the implied vol of the mark so the market quoter's centre is the screen's mid"""
    name = "OPT"; rows = []
    T = (EXP1 - T0) / (365.25 * 86400e3); mark_iv = float(bs.implied_vol(0.051 * 100000.0, 100000.0, 100000.0, T, 1.0)) * 100
    for k in range(0, 200):
        ts = T0 + k * 1000
        rows.append(index(ts, 100000.0)); rows.append(perp(ts))
        rows.append(ticker(ts, name, 0.050, 0.052, 5.0, 4.0, 0.051, mark_iv, 0.55, 40.0))
    rows += prints
    return rows


def tape_frames(tmp_path, monkeypatch, rows, name="BTC-1JAN27-100000-C"):
    raw = tmp_path / "raw"; der = tmp_path / "der"; raw.mkdir(exist_ok=True); der.mkdir(exist_ok=True)
    monkeypatch.setattr(D, "TAPE_RAW", str(raw)); monkeypatch.setattr(D, "TAPE", str(der))
    for r in rows:
        r["ch"] = r["ch"].replace("OPT", name)
        if isinstance(r["d"], dict) and r["d"].get("instrument_name") == "OPT":
            r["d"]["instrument_name"] = name
        if isinstance(r["d"], list):
            for x in r["d"]:
                if x.get("instrument_name") == "OPT":
                    x["instrument_name"] = name
    write_tape(raw / "20270101_00.jsonl.gz", rows); D.build_tape(verbose=False)
    tk = D.load_tape("ticker"); tr = D.load_tape("trades"); ix = D.load_tape("index")
    return tk[~tk["instrument"].str.endswith("PERPETUAL")], tr, ix, tk[tk["instrument"].str.endswith("PERPETUAL")]


def test_market_quoter_fill_and_pnl_identity(tmp_path, monkeypatch):
    # the market quoter centres on the mark IV with the screen's half-spread: its bid rounds down to 0.0495, one tick
    # below the screen's 0.050; a seller printing 6 at 0.0495 is a print at our level with nothing ahead of us
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.0495, 6.0, "sell")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    cfg = mm.MMConfig(quoter="market", requote_s=5, hedge_s=10, hedge_threshold_btc=10.0, fees=True)
    r = mm.run_mm(opt, tr, ix, perp_t, cfg)
    f = r["fills"]; s = r["summary"]
    assert len(f) == 1 and f["side"].iloc[0] == 1 and f["amount"].iloc[0] == 1.0 and f["price_btc"].iloc[0] == pytest.approx(0.0495)
    assert r["positions"]["BTC-1JAN27-100000-C"] == 1.0
    # spread capture: bought three ticks below the mark
    assert f["spread_capture_usd"].iloc[0] == pytest.approx((0.051 - 0.0495) * 100000.0)
    # P&L identity: cash paid + fee against the mark at the end
    fee = min(mm.OPTION_FEE_BTC, mm.OPTION_FEE_CAP * 0.0495) * 100000.0
    assert s["fees_usd"] == pytest.approx(fee) and s["pnl_usd"] == pytest.approx((0.051 - 0.0495) * 100000.0 - fee)
    # a print at the screen's bid, above our level, does not touch us
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.050, 6.0, "sell")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    assert mm.run_mm(opt, tr, ix, perp_t, cfg)["summary"]["fills"] == 0


def test_queue_position_at_the_screen_level(tmp_path, monkeypatch):
    # with a wider spread multiple the quoter joins the screen's ask at 0.052 behind the 4 displayed: 3 traded, then 3 more
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.052, 3.0, "buy"), trade(T0 + 31_000, "OPT", 0.052, 3.0, "buy")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    r = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, spread_mult=1.0))
    f = r["fills"]
    assert len(f) == 1 and f["ts"].iloc[0] == T0 + 31_000 and f["side"].iloc[0] == -1 and f["amount"].iloc[0] == 1.0     # the first print clears 3 of the 4 ahead; the second reaches us


def test_print_through_our_level_fills_in_full(tmp_path, monkeypatch):
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.049, 0.4, "sell")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    r = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0))
    assert len(r["fills"]) == 1 and r["fills"]["amount"].iloc[0] == 0.4       # the seller went through our bid: we would have been hit first


def test_big_print_through_the_ask_fills_our_size_only(tmp_path, monkeypatch):
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.0525, 10.0, "buy")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    r = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0))
    assert len(r["fills"]) == 1 and r["fills"]["side"].iloc[0] == -1 and r["fills"]["amount"].iloc[0] == 1.0    # never more than the posted size


def test_hedge_trades_the_perp(tmp_path, monkeypatch):
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.049, 1.0, "sell")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    r = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_s=10, hedge_threshold_btc=0.1, size=1.0))
    s = r["summary"]; p = r["path"]
    assert s["n_hedges"] >= 1 and abs(p["net_delta_btc"].iloc[-1]) < 0.1 and s["fees_usd"] > 0
    assert p["perp_pos"].iloc[-1] == pytest.approx(-0.55, abs=1e-9)          # short the delta of one long call


def test_thin_ticks_keeps_changes():
    ts = np.arange(0, 100_000, 100); df = pd.DataFrame({"ts": ts, "instrument": "X", "bid": 1.0, "ask": 2.0, "bid_size": 1.0, "ask_size": 1.0})
    df.loc[500, "bid"] = 1.1
    out = mm.thin_ticks(df, mark_every_s=10)
    assert len(out) < len(df) / 5 and (out["ts"] == ts[500]).any()


def test_rfq_structures():
    F = 100.0; rows = []
    for T, th, rho, psi in ((0.1, 0.02, -0.3, 0.15), (0.5, 0.06, -0.4, 0.3)):
        K = F * np.exp(np.linspace(-0.5, 0.5, 21)); k = np.log(K / F)
        rows += [{"T": T, "F": F, "K": kk, "iv": np.sqrt(S.ssvi_w(x, th, rho, psi) / T)} for kk, x in zip(K, k)]
    surf = S.fit_surface(pd.DataFrame(rows))
    st = R.price_structure(surf, R.structure("straddle", 100.0, 0.1), 0.01, size=1.0, index=F)
    c = bs.black(F, 100.0, 0.1, surf.iv(np.array([100.0]), 0.1)[0], 1.0); p = bs.black(F, 100.0, 0.1, surf.iv(np.array([100.0]), 0.1)[0], -1.0)
    assert st["mid_usd"] == pytest.approx(c + p) and st["package_factor"] == 0.6 and st["ask_usd"] > st["mid_usd"] > st["bid_usd"]
    assert abs(st["greeks"]["delta"]) < 0.2                                 # a straddle is near delta-neutral
    rr = R.price_structure(surf, R.structure("risk_reversal", 95.0, 105.0, 0.1), 0.01)
    assert rr["greeks"]["delta"] > 0.5                                       # long call, short put


def test_micro_index_moves_and_blocks():
    ts = np.arange(0, 600_000, 1000); px = 100000.0 * np.ones(len(ts)); px[300:] *= 1.002
    ev = X.index_moves(pd.DataFrame({"ts": ts, "price": px}), window_s=30, bp_threshold=10.0)
    assert len(ev) == 1 and ev["move_bp"].iloc[0] == pytest.approx(20.0, abs=0.01)
    tr = pd.DataFrame({"timestamp": [T0 + 1, T0 + 2, T0 + 3, T0 + 4], "instrument_name": ["BTC-1JAN27-100000-C"] * 4, "price": [0.05, 0.055, 0.05, 0.052], "mark_price": [0.05, 0.05, 0.05, 0.05], "iv": [50, 55, 50, 52], "index_price": [100000.0] * 4, "amount": [1, 30, 1, 60], "direction": ["buy", "buy", "sell", "sell"], "block_trade_id": [None, "b1", None, "b2"], "expiry_ms": [EXP1] * 4, "strike": [100000.0] * 4, "cp": [1.0] * 4})
    t, summ = X.block_trades_vs_screen(tr)
    assert summ["block"]["trades"] == 2 and summ["screen"]["trades"] == 2 and summ["block_share_of_contracts"] == pytest.approx(90 / 92)
    assert summ["block"]["median_abs_diff_pct"] == pytest.approx(7.0, abs=1e-9)


def test_tape_lines_reads_past_a_truncated_member(tmp_path):
    """the recorder appends a new gzip member on restart; a kill leaves the previous one without its trailer"""
    import gzip
    import json
    import zlib
    from optmm import data as D
    path = tmp_path / "20260101_00.jsonl.gz"
    lines1 = [json.dumps({"t": i, "ch": "x", "d": {}}) for i in range(200)]
    buf = gzip.compress(("\n".join(lines1) + "\n").encode(), mtime=0)
    with open(path, "wb") as f:
        # the first member keeps only part of its bytes (killed mid-write); gzip.open writes FNAME so give the header ours
        hdr = b"\x1f\x8b\x08\x08" + b"\x00\x00\x00\x00" + b"\x02\xff" + b"20260101_00.jsonl\x00"
        c = zlib.compressobj(9, zlib.DEFLATED, -15); body = c.compress(("\n".join(lines1) + "\n").encode()) + c.flush()
        f.write(hdr + body[: len(body) * 2 // 3])
        c = zlib.compressobj(9, zlib.DEFLATED, -15); lines2 = [json.dumps({"t": 1000 + i, "ch": "y", "d": {}}) for i in range(50)]
        body2 = c.compress(("\n".join(lines2) + "\n").encode()) + c.flush()
        f.write(hdr + body2 + b"\x00" * 8)
    got = []
    for line in D.tape_lines(str(path)):
        try:
            got.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    assert len(got) > 50 and got[-1]["t"] == 1049 and got[-50]["t"] == 1000    # the second member is read in full
    assert sum(m["ch"] == "x" for m in got) > 10                                # and the truncated first one in part


def test_fast_market_pull_and_index_requote(tmp_path, monkeypatch):
    """a 30 bp index jump inside ten seconds pulls the quotes for pull_for_s; a print through the old bid in that window
    finds nothing; with the pull off the same print fills"""
    rows = make_tape([])
    for r in rows:
        if r["ch"].startswith("deribit_price_index") and r["d"]["timestamp"] >= T0 + 61_000:
            r["d"]["price"] = 100300.0
        if r["ch"].startswith("ticker.") and r["d"]["timestamp"] >= T0 + 61_000:
            r["d"]["index_price"] = 100300.0
    rows += [trade(T0 + 63_000, "OPT", 0.049, 1.0, "sell")]
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    on = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, pull_bp=15.0, pull_window_s=10, pull_for_s=10))
    off = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, pull_bp=0.0))
    assert on["summary"]["n_pulls"] >= 1 and on["summary"]["fills"] == 0
    assert off["summary"]["n_pulls"] == 0 and off["summary"]["fills"] == 1
    # the index move re-prices the quotes before the timer: with the pull off there is a post at 61 s, off the 5-second grid
    assert (off["quotes"]["ts"] == T0 + 61_000).any()


def test_hard_vega_limit_stops_the_adding_side(tmp_path, monkeypatch):
    """after a fill takes the portfolio vega past vega_hard, the bid side is withdrawn and a second seller finds no bid"""
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.049, 1.0, "sell"), trade(T0 + 60_000, "OPT", 0.049, 1.0, "sell")])
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    stop = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, vega_hard=20.0, pull_bp=0.0))
    free = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, vega_hard=0.0, pull_bp=0.0))
    assert stop["summary"]["fills"] == 1 and free["summary"]["fills"] == 2                # one contract of vega 40 > 20 stops the bids


def test_delta_hedged_markout(tmp_path, monkeypatch):
    """the raw mark-out of a long call after the index rises is delta x dS plus the mark's own move; the hedged one is
    only the latter (the tape's mark is flat, so the hedged mark-out is minus the delta move, and the raw one is ~0)"""
    rows = make_tape([trade(T0 + 30_000, "OPT", 0.049, 1.0, "sell")])
    for r in rows:
        if r["ch"].startswith("ticker.OPT") and r["d"]["timestamp"] >= T0 + 80_000:
            r["d"]["index_price"] = 101000.0; r["d"]["underlying_price"] = 101000.0
    opt, tr, ix, perp_t = tape_frames(tmp_path, monkeypatch, rows)
    r = mm.run_mm(opt, tr, ix, perp_t, mm.MMConfig(quoter="market", requote_s=5, hedge_threshold_btc=10.0, pull_bp=0.0))
    f = r["fills"].iloc[0]
    raw = (0.051 * 101000.0 - 0.0495 * 100000.0); hedged = raw - 0.55 * 1000.0          # filled at our bid of 0.0495
    assert f["markout_1m_usd"] == pytest.approx(raw, rel=1e-6) and f["markout_dh_1m_usd"] == pytest.approx(hedged, rel=1e-6)
