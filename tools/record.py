#!/usr/bin/env python3
"""Deribit tape recorder.   python tools/record.py [--currency BTC] [--hours 0] [--out data/raw/tape]

Subscribes over the public websocket (no key) to the front expiries of the option chain and writes every message to
hourly gzip-compressed JSON-lines files: one line per notification, {"t": receive time in ms, "ch": channel, "d": data}.

  ticker.<option>.100ms          best bid/ask with sizes, mark price, mark/bid/ask IV, Greeks, underlying and index: every
                                 option within +-30 % of the index on the two nearest dailies, the weekly, the monthly and
                                 the quarterly (the quoting universe)
  book.<option>.none.10.100ms    ten levels a side within +-12 % of the index on the nearest daily, the weekly and the monthly
  trades.<option>.100ms          every print on the quoting universe, with IV, mark, index, direction and block flag
  book.BTC-PERPETUAL / trades    the hedge instrument
  deribit_price_index.btc_usd    the index

The instrument set is refreshed every hour (expiries roll, the index moves); the connection is re-established on any
error; heartbeats are answered.  Files rotate on the hour so a crash loses at most the open hour.
"""
import argparse
import asyncio
import datetime as dt
import gzip
import json
import os
import signal
import sys
import time

import websockets

WS = "wss://www.deribit.com/ws/api/v2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def utc_hour_name(out: str) -> str:
    return os.path.join(out, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H") + ".jsonl.gz")


async def rpc(ws, method: str, params: dict, rid: int):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))


def select_expiries(expiries: list, now_ms: float, min_hours: float = 2.0, horizons_days=(0, 0, 5, 20, 60)) -> list:
    """the two nearest expiries (dailies) plus the first with at least 5, 20 and 60 days to go (weekly, monthly, quarterly)"""
    exps = sorted(e for e in set(expiries) if e - now_ms > min_hours * 3600e3)
    chosen = []
    for h in horizons_days:
        for e in exps:
            if e not in chosen and e - now_ms >= h * 86400e3:
                chosen.append(e); break
    return sorted(chosen)


def select_instruments(instruments: list, index: float, window: float = 0.30, book_window: float = 0.12, min_hours: float = 2.0, now_ms: float | None = None) -> tuple[list, list]:
    """the quoting universe: strikes within +-window of the index on the selected expiries; full ten-level books for
    strikes within +-book_window on the nearest daily, the weekly and the monthly.  now_ms: the exchange's clock when
    known (the recording machine's can be hours out)"""
    now = now_ms or time.time() * 1000
    exps = select_expiries([i["expiration_timestamp"] for i in instruments], now, min_hours)
    book_exps = {exps[0]} | ({exps[2], exps[3]} if len(exps) >= 4 else set(exps[1:]))
    ticker, books = [], []
    for i in instruments:
        if i["expiration_timestamp"] not in exps:
            continue
        k = float(i["strike"])
        if abs(k / index - 1) <= window:
            ticker.append(i["instrument_name"])
            if i["expiration_timestamp"] in book_exps and abs(k / index - 1) <= book_window:
                books.append(i["instrument_name"])
    return sorted(ticker), sorted(books)


async def session(currency: str, out: str, stop_at: float | None, stats: dict):
    async with websockets.connect(WS, ping_interval=None, max_queue=4096, max_size=2**23) as ws:
        rid = 1
        await rpc(ws, "public/set_heartbeat", {"interval": 30}, rid); rid += 1
        await rpc(ws, "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}, rid); rid += 1
        await rpc(ws, "public/get_instruments", {"currency": currency, "kind": "option", "expired": False}, rid); rid += 1
        index = None; instruments = None; subscribed = set(); last_select = 0.0
        fh = None; fname = None; n = 0; t_last_log = time.time(); exchange_ms = None
        try:
            while True:
                if stop_at and time.time() > stop_at:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    await rpc(ws, "public/test", {}, rid); rid += 1; continue
                now_ms = int(time.time() * 1000); msg = json.loads(raw)
                if "method" in msg:
                    if msg["method"] == "heartbeat":
                        if msg["params"].get("type") == "test_request":
                            await rpc(ws, "public/test", {}, rid); rid += 1
                        continue
                    if msg["method"] == "subscription":
                        ch = msg["params"]["channel"]; d = msg["params"]["data"]
                        if isinstance(d, dict) and d.get("timestamp"):
                            exchange_ms = d["timestamp"]
                        name = utc_hour_name(out)
                        if name != fname:
                            if fh:
                                fh.close()
                            fh = gzip.open(name, "at", encoding="utf-8"); fname = name
                        fh.write(json.dumps({"t": now_ms, "ch": ch, "d": d}, separators=(",", ":")) + "\n"); n += 1; stats["lines"] = stats.get("lines", 0) + 1
                        if ch.startswith("deribit_price_index"):
                            index = float(d["price"])
                        if abs(time.time() - t_last_log) > 300:
                            print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), "lines", stats["lines"], "subs", len(subscribed), "index", index, flush=True); t_last_log = time.time()
                            if fh:
                                fh.flush()
                    continue
                res = msg.get("result")
                if isinstance(res, dict) and "index_price" in res:
                    index = float(res["index_price"])
                elif isinstance(res, list) and res and isinstance(res[0], dict) and "instrument_name" in res[0]:
                    instruments = res
                if index and instruments and abs(time.time() - last_select) > 3600:
                    ticker, books = select_instruments(instruments, index, now_ms=exchange_ms)
                    chans = [f"ticker.{i}.100ms" for i in ticker] + [f"trades.{i}.100ms" for i in ticker] + [f"book.{i}.none.10.100ms" for i in books]
                    chans += [f"book.{currency}-PERPETUAL.none.10.100ms", f"trades.{currency}-PERPETUAL.100ms", f"deribit_price_index.{currency.lower()}_usd", f"ticker.{currency}-PERPETUAL.100ms"]
                    new = [c for c in chans if c not in subscribed]
                    for k in range(0, len(new), 100):
                        await rpc(ws, "public/subscribe", {"channels": new[k:k + 100]}, rid); rid += 1
                    subscribed |= set(new); last_select = time.time()
                    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), f"index {index:.0f}: {len(ticker)} tickers, {len(books)} books, {len(subscribed)} channels", flush=True)
                    # refresh the instrument list and the index for the next selection
                    asyncio.get_event_loop().call_later(3600, lambda: asyncio.ensure_future(rpc(ws, "public/get_index_price", {"index_name": f"{currency.lower()}_usd"}, 900001)))
                    asyncio.get_event_loop().call_later(3601, lambda: asyncio.ensure_future(rpc(ws, "public/get_instruments", {"currency": currency, "kind": "option", "expired": False}, 900002)))
        finally:
            if fh:
                fh.close()


async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--currency", default="BTC"); ap.add_argument("--hours", type=float, default=0.0); ap.add_argument("--out", default=os.path.join(ROOT, "data", "raw", "tape"))
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    stop_at = time.time() + a.hours * 3600 if a.hours > 0 else None
    stats = {}
    while True:
        if stop_at and time.time() > stop_at:
            break
        try:
            await session(a.currency, a.out, stop_at, stats)
        except Exception as e:  # noqa: BLE001
            print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), "reconnect after", repr(e)[:200], flush=True)
            await asyncio.sleep(5)
    print("recorder stopped; lines", stats.get("lines", 0))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
