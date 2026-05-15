"""Loaders and the store: the Deribit instrument catalogue and trade history, the recorded tape (jsonl.gz -> parquet
tables), the DoltHub end-of-day chains, underlying prices and rates, and a DuckDB database of views over the parquet
files for the post-trade SQL."""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import re
import zlib

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# OPTMM_DATA points the whole pipeline at another data root (CI runs on the committed data/sample)
DATA = os.environ.get("OPTMM_DATA") or os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw"); DER = os.path.join(DATA, "derived"); REF = os.path.join(DATA, "reference")
TAPE_RAW = os.path.join(RAW, "tape"); TAPE = os.path.join(DER, "tape")
DB_PATH = os.path.join(DER, "optmm.duckdb")
EXPIRY_HOUR_UTC = 8
MONTHS = {m: i + 1 for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
NAME_RE = re.compile(r"^([A-Z]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:d\d+)?)-([CP])$")


def sql_path(p: str) -> str:
    return p.replace(os.sep, "/").replace("'", "''")


# ---- instruments ---------------------------------------------------------------------------------------------------------------
def parse_instrument(name: str):
    """'BTC-25SEP26-80000-C' -> (currency, expiry datetime UTC 08:00, strike, cp=+1/-1); None if not an option name"""
    m = NAME_RE.match(name)
    if not m:
        return None
    cur, d, mon, y, k, cp = m.groups()
    exp = dt.datetime(2000 + int(y), MONTHS[mon], int(d), EXPIRY_HOUR_UTC, tzinfo=dt.timezone.utc)
    return cur, exp, float(k.replace("d", ".")), 1.0 if cp == "C" else -1.0


def expiry_ms(name: str) -> int:
    p = parse_instrument(name)
    return int(p[1].timestamp() * 1000) if p else 0


def add_instrument_columns(df: pd.DataFrame, col: str = "instrument_name") -> pd.DataFrame:
    names = df[col].unique(); parsed = {n: parse_instrument(n) for n in names}
    df = df.copy()
    df["expiry_ms"] = df[col].map(lambda n: int(parsed[n][1].timestamp() * 1000) if parsed[n] else 0)
    df["strike"] = df[col].map(lambda n: parsed[n][2] if parsed[n] else np.nan)
    df["cp"] = df[col].map(lambda n: parsed[n][3] if parsed[n] else np.nan)
    return df


def load_instruments(currency: str = "BTC") -> pd.DataFrame:
    p = os.path.join(RAW, "deribit", f"instruments_{currency}.json")
    d = json.load(open(p, encoding="utf-8"))
    df = pd.DataFrame(d)[["instrument_name", "strike", "option_type", "expiration_timestamp", "creation_timestamp", "is_active", "tick_size", "contract_size"]]
    df["cp"] = np.where(df["option_type"] == "call", 1.0, -1.0)
    return df


# ---- trade history -------------------------------------------------------------------------------------------------------------
def load_trades(currency: str = "BTC", months: list | None = None, columns: list | None = None, where: str | None = None) -> pd.DataFrame:
    """the trade history (monthly parquet files), optionally a column subset and a SQL filter (the files hold ~25 M rows)"""
    files = sorted(glob.glob(os.path.join(RAW, "deribit", f"trades_{currency}_*.parquet")))
    if months:
        files = [f for f in files if any(f.endswith(f"_{m}.parquet") for m in months)]
    if not files:
        return pd.DataFrame()
    con = duckdb.connect()
    cols = "*" if not columns else ", ".join(columns)
    df = con.execute(f"select {cols} from read_parquet([{', '.join(chr(39) + sql_path(f) + chr(39) for f in files)}], union_by_name=true) {'where ' + where if where else ''} order by timestamp").df(); con.close()
    return df


# the settlement window of the daily history surfaces, and the block study's input: every block print and one screen
# print in ten (trade_seq is the exchange's per-instrument sequence number)
SETTLEMENT_WINDOW_SQL = "((timestamp // 1000) % 86400) // 3600 in (6, 7)"
BLOCK_SAMPLE_SQL = "block_trade_id is not null or trade_seq % 10 = 0"
SCREEN_SAMPLE_WEIGHT = 10.0


def trade_months(currency: str = "BTC") -> list:
    return sorted(os.path.basename(f)[len(f"trades_{currency}_"):-8] for f in glob.glob(os.path.join(RAW, "deribit", f"trades_{currency}_*.parquet")))


def load_dvol(currency: str = "BTC", resolution: str = "1D") -> pd.DataFrame:
    d = json.load(open(os.path.join(RAW, "deribit", f"dvol_{currency}.json"), encoding="utf-8"))
    df = pd.DataFrame(d[resolution], columns=["ts", "open", "high", "low", "close"]); df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True); return df


# ---- the recorded tape -----------------------------------------------------------------------------------------------------------
GZ_MEMBER = rb"\x1f\x8b\x08\x08.{4}\x02\xff"          # the header gzip.open writes: flags FNAME, xfl 2, os 255, then the name


def tape_lines(path: str):
    """every line of an hour file, member by member: the recorder appends a new gzip member when it restarts and a kill
    leaves the previous member without its trailer, which gzip.open cannot read past; members are located by their
    header (the file's own name is in it), each is inflated on its own and a truncated one yields what it has"""
    raw = open(path, "rb").read()
    sig = re.compile(GZ_MEMBER + re.escape(os.path.basename(path)[:-3].encode()) + rb"\x00", re.S)
    heads = [(m.start(), m.end()) for m in sig.finditer(raw)] or [(0, 0)]
    for i, (s0, s) in enumerate(heads):
        # raw deflate after the header: the trailer's checksum is never consulted, so a member whose tail is missing or
        # damaged still gives up everything before the damage
        end = heads[i + 1][0] if i + 1 < len(heads) else len(raw)
        d = zlib.decompressobj(-15 if s > s0 else 31); rest = b""
        for pos in range(s, end, 1 << 20):
            try:
                chunk = d.decompress(raw[pos:min(pos + (1 << 20), end)])
            except zlib.error:
                break
            parts = (rest + chunk).split(b"\n"); rest = parts.pop()
            for line in parts:
                yield line
        if rest.strip():
            yield rest


def parse_tape_file(path: str) -> dict:
    """one hour of raw tape -> DataFrames: ticker, book, trades, index, perp_book, perp_trades"""
    tick, book, trades, index, pbook, ptrades = [], [], [], [], [], []
    try:
        if True:
            for line in tape_lines(path):
                try:
                    m = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue        # a line cut by a kill or a restart
                t = m["t"]; ch = m["ch"]; d = m["d"]
                if ch.startswith("ticker."):
                    name = d.get("instrument_name", ""); g = d.get("greeks") or {}
                    if name.endswith("PERPETUAL"):
                        tick.append((t, d.get("timestamp"), name, d.get("best_bid_price"), d.get("best_ask_price"), d.get("best_bid_amount"), d.get("best_ask_amount"), d.get("mark_price"), None, None, None, None, None, None, None, d.get("index_price"), d.get("index_price"), d.get("open_interest"), d.get("funding_8h")))
                    else:
                        tick.append((t, d.get("timestamp"), name, d.get("best_bid_price"), d.get("best_ask_price"), d.get("best_bid_amount"), d.get("best_ask_amount"), d.get("mark_price"), d.get("mark_iv"), d.get("bid_iv"), d.get("ask_iv"), g.get("delta"), g.get("gamma"), g.get("vega"), g.get("theta"), d.get("underlying_price"), d.get("index_price"), d.get("open_interest"), None))
                elif ch.startswith("book."):
                    name = d.get("instrument_name", ""); bids = d.get("bids") or []; asks = d.get("asks") or []
                    row = (t, d.get("timestamp"), name, [float(b[0]) for b in bids], [float(b[1]) for b in bids], [float(a[0]) for a in asks], [float(a[1]) for a in asks])
                    (pbook if name.endswith("PERPETUAL") else book).append(row)
                elif ch.startswith("trades."):
                    for x in d:
                        name = x.get("instrument_name", "")
                        if name.endswith("PERPETUAL"):
                            ptrades.append((t, x.get("timestamp"), name, x.get("price"), x.get("amount"), x.get("direction"), x.get("trade_id")))
                        else:
                            trades.append((t, x.get("timestamp"), name, x.get("price"), x.get("amount"), x.get("direction"), x.get("iv"), x.get("mark_price"), x.get("index_price"), x.get("block_trade_id"), x.get("trade_id"), x.get("tick_direction")))
                elif ch.startswith("deribit_price_index"):
                    index.append((t, d.get("timestamp"), d.get("price")))
    except (EOFError, OSError):
        pass        # an unreadable file keeps what was read
    tick_cols = ["t", "ts", "instrument", "bid", "ask", "bid_size", "ask_size", "mark", "mark_iv", "bid_iv", "ask_iv", "delta", "gamma", "vega", "theta", "underlying", "index", "open_interest", "funding_8h"]
    out = {"ticker": pd.DataFrame(tick, columns=tick_cols), "book": pd.DataFrame(book, columns=["t", "ts", "instrument", "bid_px", "bid_sz", "ask_px", "ask_sz"]), "trades": pd.DataFrame(trades, columns=["t", "ts", "instrument", "price", "amount", "direction", "iv", "mark", "index", "block_trade_id", "trade_id", "tick_direction"]),
           "index": pd.DataFrame(index, columns=["t", "ts", "price"]), "perp_book": pd.DataFrame(pbook, columns=["t", "ts", "instrument", "bid_px", "bid_sz", "ask_px", "ask_sz"]), "perp_trades": pd.DataFrame(ptrades, columns=["t", "ts", "instrument", "price", "amount", "direction", "trade_id"])}
    for k in ("ticker", "trades"):
        if len(out[k]):
            out[k] = add_instrument_columns(out[k], "instrument")
    return out


def build_tape(force: bool = False, verbose: bool = True) -> dict:
    """every raw hour -> parquet tables under data/derived/tape; a file is converted again only when its size changed
    (the recording machine's clock is not trusted to say which hour is closed)"""
    os.makedirs(TAPE, exist_ok=True)
    files = sorted(glob.glob(os.path.join(TAPE_RAW, "*.jsonl.gz")))
    done = 0; stats = {}
    for f in files:
        hour = os.path.basename(f)[:11]; size = os.path.getsize(f)
        marker = os.path.join(TAPE, f"{hour}.size")
        if not force and os.path.exists(marker) and open(marker).read().strip() == str(size):
            continue
        tables = parse_tape_file(f)
        for name, df in tables.items():
            p = os.path.join(TAPE, f"{name}_{hour}.parquet")
            if len(df):
                df.to_parquet(p, index=False)
            elif os.path.exists(p):
                os.remove(p)
        stats[hour] = {k: int(len(v)) for k, v in tables.items()}
        open(marker, "w").write(str(size))
        done += 1
    if verbose:
        print(f"tape: {done} hour files converted; {len(files)} raw hours")
    return stats


def load_tape(table: str, hours: list | None = None) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(TAPE, f"{table}_*.parquet")))
    if hours:
        files = [f for f in files if any(f.endswith(f"_{h}.parquet") for h in hours)]
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    # order by the exchange timestamp: the recording machine's clock is not the exchange's; a reconnect can deliver a
    # notification twice
    key = [c for c in ("ts", "instrument", "trade_id", "price") if c in df.columns]
    return df.drop_duplicates(key).sort_values("ts", kind="stable").reset_index(drop=True)


def tape_hours() -> list:
    return sorted({os.path.basename(f)[len("ticker_"):-8] for f in glob.glob(os.path.join(TAPE, "ticker_*.parquet"))})


# ---- DoltHub chains, prices, rates -------------------------------------------------------------------------------------------
def load_chains(symbol: str) -> pd.DataFrame:
    p = os.path.join(RAW, "dolt", f"chains_{symbol}.jsonl")
    if not os.path.exists(p):
        return pd.DataFrame()
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    df = pd.DataFrame(rows)
    for c in ("strike", "bid", "ask", "vol", "delta", "gamma", "theta", "vega", "rho"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]); df["expiration"] = pd.to_datetime(df["expiration"])
    df["cp"] = np.where(df["call_put"].str.lower().str.startswith("c"), 1.0, -1.0)
    df["T"] = ((df["expiration"] - df["date"]).dt.days + 0.5) / 365.0      # expiry at the close: the day itself counts as half
    df["mid"] = 0.5 * (df["bid"] + df["ask"]); df["spread"] = df["ask"] - df["bid"]
    return df.drop_duplicates(["date", "expiration", "strike", "cp"]).sort_values(["date", "expiration", "strike"]).reset_index(drop=True)


def load_prices() -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(REF, "prices.parquet")); df["date"] = pd.to_datetime(df["date"]); return df


def load_rates() -> pd.Series:
    r = pd.read_csv(os.path.join(REF, "rates.csv")); r["date"] = pd.to_datetime(r["date"])
    s = r.set_index("date")["SOFR"].astype(float) / 100.0
    return s.sort_index()


# ---- DuckDB views -----------------------------------------------------------------------------------------------------------------
def connect(path: str = DB_PATH):
    os.makedirs(DER, exist_ok=True); con = duckdb.connect(path)
    def view(name, pattern):
        files = sorted(glob.glob(pattern))
        if files:
            con.execute(f"create or replace view {name} as select * from read_parquet([{', '.join(chr(39) + sql_path(f) + chr(39) for f in files)}], union_by_name=true)")
    view("trades_hist", os.path.join(RAW, "deribit", "trades_*.parquet"))
    for t in ("ticker", "book", "trades", "index", "perp_book", "perp_trades"):
        view(f"tape_{t}", os.path.join(TAPE, f"{t}_*.parquet"))
    for t in ("surfaces", "slices", "mm_fills", "mm_pnl", "mm_quotes", "chain_fits"):
        view(t, os.path.join(DER, f"{t}.parquet"))
    return con


# ---- a small committed sample for CI -------------------------------------------------------------------------------------------
def make_sample(out: str | None = None, tape_minutes: int = 30, hist_days: int = 7, chain_days: int = 40, symbols: tuple = ("SPY",), verbose: bool = True) -> dict:
    """Writes data/sample: the last tape_minutes of the tape (the ticker thinned to quote changes and a 10 s mark refresh),
    the prints of the last hist_days days of the latest trade month in the 05:00-08:00 UTC window, the last chain_days
    days of the listed chains, and the reference tables.  `OPTMM_DATA=data/sample python -m optmm run --quick` then runs
    the whole pipeline on it in a few minutes."""
    from .mm import thin_ticks
    out = out or os.path.join(ROOT, "data", "sample"); stats = {}
    tape_dir = os.path.join(out, "derived", "tape"); os.makedirs(tape_dir, exist_ok=True)
    tick = load_tape("ticker")
    if len(tick):
        t1 = int(tick["ts"].max()); t0 = t1 - tape_minutes * 60000
        for name, fn in (("ticker", lambda d: thin_ticks(d)), ("trades", None), ("index", None), ("perp_trades", None)):
            d = load_tape(name)
            if d.empty:
                continue
            d = d[(d["ts"] >= t0) & (d["ts"] <= t1)]
            if fn is not None:
                d = fn(d)
            d.to_parquet(os.path.join(tape_dir, f"{name}_sample.parquet"), index=False); stats[f"tape_{name}"] = int(len(d))
    months = trade_months()
    if months:
        d = load_trades(months=[months[-1]])
        t = pd.to_datetime(d["timestamp"], unit="ms", utc=True); hour = t.dt.hour + t.dt.minute / 60.0; day = t.dt.floor("D")
        keep_days = sorted(day.unique())[-hist_days:]
        d = d[day.isin(keep_days) & (hour >= 5) & (hour < 8)]
        os.makedirs(os.path.join(out, "raw", "deribit"), exist_ok=True)
        d.to_parquet(os.path.join(out, "raw", "deribit", f"trades_BTC_{months[-1]}.parquet"), index=False); stats["hist_prints"] = int(len(d))
    os.makedirs(os.path.join(out, "raw", "dolt"), exist_ok=True)
    for sym in symbols:
        p = os.path.join(RAW, "dolt", f"chains_{sym}.jsonl")
        if not os.path.exists(p):
            continue
        rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        dates = sorted({r["date"] for r in rows})[-chain_days:]
        with open(os.path.join(out, "raw", "dolt", f"chains_{sym}.jsonl"), "w", encoding="utf-8", newline="\n") as f:
            n = 0
            for r in rows:
                if r["date"] in dates:
                    f.write(json.dumps(r) + "\n"); n += 1
        stats[f"chain_rows_{sym}"] = n
    os.makedirs(os.path.join(out, "reference"), exist_ok=True)
    for fn in ("prices.parquet", "dividends.csv", "rates.csv"):
        p = os.path.join(REF, fn)
        if os.path.exists(p):
            with open(p, "rb") as src, open(os.path.join(out, "reference", fn), "wb") as dst:
                dst.write(src.read())
    if verbose:
        print("sample:", stats)
    return stats
