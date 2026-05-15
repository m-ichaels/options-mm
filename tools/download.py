#!/usr/bin/env python3
"""Free data for the options market-making engine.   python tools/download.py [instruments] [trades] [dvol] [chains] [prices] [rates] [all] [--from 2021-01-01] [--currency BTC]

  Deribit history API    every option trade (price, IV, mark, index, direction, size, block flag) by currency and time,
                         the instrument catalogue (live and expired), DVOL history      -> data/raw/deribit/
  Deribit websocket      the live tape is recorded by tools/record.py                  -> data/raw/tape/
  DoltHub options        daily end-of-day option chains (bid, ask, IV, Greeks) for SPY and single names since 2019,
                         post-no-preference/options, through the SQL API              -> data/raw/dolt/
  Yahoo                  daily bars and dividends for the chain underlyings            -> data/reference/prices.parquet
  New York Fed           SOFR and EFFR for discounting                                 -> data/reference/rates.csv
"""
import csv
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw"); REF = os.path.join(ROOT, "data", "reference")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
HIST = "https://history.deribit.com/api/v2/public"
LIVE = "https://www.deribit.com/api/v2/public"


def get(url, retries=4, timeout=90):
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            if e.code == 429:
                time.sleep(10 * (k + 1)); continue
            if k == retries - 1:
                return None
            time.sleep(2 * (k + 1))
        except Exception:  # noqa: BLE001
            if k == retries - 1:
                return None
            time.sleep(2 * (k + 1))


def jget(url, **kw):
    raw = get(url, **kw)
    return json.loads(raw) if raw else None


# ---- Deribit -----------------------------------------------------------------------------------------------------------
def instruments(currency="BTC"):
    os.makedirs(os.path.join(RAW, "deribit"), exist_ok=True)
    out = []
    for expired in ("false", "true"):
        d = jget(f"{HIST}/get_instruments?currency={currency}&kind=option&expired={expired}")
        if d and "result" in d:
            out += d["result"]
    p = os.path.join(RAW, "deribit", f"instruments_{currency}.json")
    json.dump(out, open(p, "w"))
    print(f"instruments {currency}: {len(out)} (live + expired) -> {p}")
    return out


def trades(currency="BTC", start=dt.date(2021, 1, 1), end=None):
    """all option trades by month, paginated by time; resumes from the last complete month on disk"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    os.makedirs(os.path.join(RAW, "deribit"), exist_ok=True)
    end = end or dt.date.today()
    y, m = start.year, start.month
    cols = ["timestamp", "trade_id", "trade_seq", "instrument_name", "price", "mark_price", "iv", "index_price", "direction", "amount", "contracts", "tick_direction", "block_trade_id", "liquidation"]
    while dt.date(y, m, 1) <= end:
        p = os.path.join(RAW, "deribit", f"trades_{currency}_{y}{m:02d}.parquet")
        m_end = dt.date(y + (m == 12), m % 12 + 1, 1)
        complete = m_end <= end
        if os.path.exists(p) and complete:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1); continue
        t0 = int(dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        t1 = int(dt.datetime(m_end.year, m_end.month, m_end.day, tzinfo=dt.timezone.utc).timestamp() * 1000) - 1
        rows = []; cur = t0; calls = 0; t_start = time.time()
        while True:
            d = jget(f"{HIST}/get_last_trades_by_currency_and_time?currency={currency}&kind=option&start_timestamp={cur}&end_timestamp={t1}&count=1000&sorting=asc")
            calls += 1
            if not d or "result" not in d:
                print("  retry-fail at", cur); time.sleep(5)
                d = jget(f"{HIST}/get_last_trades_by_currency_and_time?currency={currency}&kind=option&start_timestamp={cur}&end_timestamp={t1}&count=1000&sorting=asc")
                if not d or "result" not in d:
                    break
            tr = d["result"]["trades"]
            if not tr:
                break
            for x in tr:
                rows.append(tuple(x.get(c) for c in cols))
            last = tr[-1]["timestamp"]
            if not d["result"].get("has_more") or last >= t1:
                break
            cur = last + 1 if last > cur else cur + 1
            time.sleep(0.05)
        if rows:
            # de-duplicate on trade_id (the +1 ms restart can repeat a trade)
            seen = set(); uniq = []
            for r in rows:
                if r[1] in seen:
                    continue
                seen.add(r[1]); uniq.append(r)
            colsd = list(zip(*uniq))
            table = pa.table({"timestamp": pa.array(colsd[0], pa.int64()), "trade_id": pa.array([str(v) for v in colsd[1]]), "trade_seq": pa.array(colsd[2], pa.int64()), "instrument_name": pa.array(colsd[3]), "price": pa.array(colsd[4], pa.float64()), "mark_price": pa.array(colsd[5], pa.float64()), "iv": pa.array(colsd[6], pa.float64()), "index_price": pa.array(colsd[7], pa.float64()), "direction": pa.array(colsd[8]), "amount": pa.array(colsd[9], pa.float64()), "contracts": pa.array([float(v) if v is not None else None for v in colsd[10]], pa.float64()), "tick_direction": pa.array(colsd[11], pa.int64()), "block_trade_id": pa.array([str(v) if v is not None else None for v in colsd[12]]), "liquidation": pa.array([str(v) if v is not None else None for v in colsd[13]])})
            pq.write_table(table, p, compression="zstd")
        print(f"  trades {currency} {y}-{m:02d}: {len(rows)} rows, {calls} calls, {time.time() - t_start:.0f}s", flush=True)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def dvol(currency="BTC"):
    os.makedirs(os.path.join(RAW, "deribit"), exist_ok=True)
    out = {}
    for res in ("1D", "3600"):
        rows = {}; t0 = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000); cur_end = int(time.time() * 1000)
        while cur_end > t0:
            d = jget(f"{LIVE}/get_volatility_index_data?currency={currency}&start_timestamp={t0}&end_timestamp={cur_end}&resolution={res}")
            data = (d or {}).get("result", {}).get("data", [])
            if not data:
                break
            for r in data:
                rows[r[0]] = r
            oldest = min(r[0] for r in data)
            if oldest <= t0 or len(data) < 2:
                break
            cur_end = oldest - 1; time.sleep(0.1)
        out[res] = [rows[k] for k in sorted(rows)]
    p = os.path.join(RAW, "deribit", f"dvol_{currency}.json"); json.dump(out, open(p, "w"))
    print(f"dvol {currency}: {len(out.get('1D', []))} days, {len(out.get('3600', []))} hours -> {p}")


# ---- DoltHub end-of-day chains --------------------------------------------------------------------------------------------
def dolt_query(sql):
    u = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master?q=" + urllib.parse.quote(sql)
    d = jget(u, timeout=180)
    if not d or d.get("query_execution_status") != "Success":
        return None
    return d.get("rows", [])


def chains(symbols=("SPY", "AAPL", "NVDA"), start=dt.date(2024, 1, 2), end=None):
    os.makedirs(os.path.join(RAW, "dolt"), exist_ok=True); end = end or dt.date.today()
    for sym in symbols:
        p = os.path.join(RAW, "dolt", f"chains_{sym}.jsonl")
        have = set()
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                try:
                    have.add(json.loads(line)["date"])
                except Exception:
                    pass
        n = 0; d = start; t0 = time.time()
        with open(p, "a", encoding="utf-8") as f:
            while d <= end:
                if d.weekday() < 5 and d.isoformat() not in have:
                    rows = dolt_query(f"SELECT * FROM option_chain WHERE act_symbol='{sym}' AND date='{d.isoformat()}'")
                    if rows:
                        for r in rows:
                            f.write(json.dumps(r) + "\n")
                        n += len(rows)
                    time.sleep(0.2)
                d += dt.timedelta(days=1)
        print(f"chains {sym}: +{n} rows ({time.time() - t0:.0f}s) -> {p}", flush=True)


# ---- Yahoo and FRED ------------------------------------------------------------------------------------------------------
def prices(symbols=("SPY", "AAPL", "NVDA")):
    import pyarrow as pa
    import pyarrow.parquet as pq
    os.makedirs(REF, exist_ok=True); bars = []; divs = []
    for sym in symbols:
        raw = get(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}?range=5y&interval=1d&events=div,splits", timeout=60)
        if not raw:
            print("  no data", sym); continue
        r = json.loads(raw)["chart"]["result"][0]; ts = r.get("timestamp") or []; q = r["indicators"]["quote"][0]; adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", [None] * len(ts))
        for i, t in enumerate(ts):
            if q["close"][i] is None:
                continue
            bars.append((sym, dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), q["open"][i] or q["close"][i], q["high"][i] or q["close"][i], q["low"][i] or q["close"][i], q["close"][i], adj[i] or q["close"][i], q["volume"][i] or 0))
        for t, e in (r.get("events", {}).get("dividends") or {}).items():
            divs.append((sym, dt.datetime.fromtimestamp(int(e.get("date", t)), dt.timezone.utc).date().isoformat(), e["amount"]))
        time.sleep(0.3)
    c = list(zip(*bars))
    pq.write_table(pa.table({"symbol": c[0], "date": c[1], "open": pa.array(c[2], pa.float64()), "high": pa.array(c[3], pa.float64()), "low": pa.array(c[4], pa.float64()), "close": pa.array(c[5], pa.float64()), "adjclose": pa.array(c[6], pa.float64()), "volume": pa.array(c[7], pa.float64())}), os.path.join(REF, "prices.parquet"), compression="zstd")
    with open(os.path.join(REF, "dividends.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["symbol", "ex_date", "amount"]); w.writerows(sorted(divs))
    print(f"prices: {len(bars)} bars, {len(divs)} dividends")


def rates():
    """SOFR and EFFR from the New York Fed (FRED times out from here)"""
    os.makedirs(REF, exist_ok=True); rows = {}
    for name, url in (("SOFR", "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2020-01-01&endDate=2030-12-31"), ("EFFR", "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2020-01-01&endDate=2030-12-31")):
        d = jget(url, timeout=60)
        for r in (d or {}).get("refRates", []):
            rows.setdefault(r["effectiveDate"], {})[name] = float(r["percentRate"])
    with open(os.path.join(REF, "rates.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["date", "SOFR", "EFFR"])
        for d in sorted(rows):
            w.writerow([d, rows[d].get("SOFR", ""), rows[d].get("EFFR", "")])
    print(f"rates: {len(rows)} days")


if __name__ == "__main__":
    a = sys.argv[1:]; what = [x for x in a if not x.startswith("--")]
    start = dt.date.fromisoformat(a[a.index("--from") + 1]) if "--from" in a else dt.date(2021, 1, 1)
    cur = a[a.index("--currency") + 1] if "--currency" in a else "BTC"
    if "instruments" in what or "all" in what:
        instruments(cur)
    if "trades" in what or "all" in what:
        trades(cur, start)
    if "dvol" in what or "all" in what:
        dvol(cur)
    if "chains" in what or "all" in what:
        chains()
    if "prices" in what or "all" in what:
        prices()
    if "rates" in what or "all" in what:
        rates()
    print("done")
