# -*- coding: utf-8 -*-
"""
watch.py — 通达信实时行情采集/查看 CLI

    python watch.py scan                      # 测所有主站速度
    python watch.py watchlist                 # 读通达信客户端自选股
    python watch.py quote 600519 000001       # 实时报价
    python watch.py snap 600519               # 分时快照
    python watch.py bars 600519 1m 240        # 导出 K 线 CSV
    python watch.py collect --interval 3      # 盘中采集自选股（默认采自选股）
    python watch.py collect --codes 600519,000001 --interval 3 --duration 600
    python watch.py market --out data/market_$(date).csv   # 全市场快照

所有采集结果写到 --out 目录（默认 ./data）：
    ticks_YYYYMMDD.csv   逐笔快照（每次轮询一行）
    bars1m_YYYYMMDD.csv  1 分钟 K 线（每根收盘后追加一行）
    latest.json          最新快照，供其他程序/缠论分析读取
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tdxlive import (TDX_DIR, TdxLive, TdxPool, infer_market, is_trading_time,
                     load_servers, rank_servers, read_watchlist)

TICK_COLS = ["ts", "code", "price", "change_pct", "last_close", "open", "high", "low",
             "vol", "amount"] + \
            [f"bid{i}" for i in range(1, 6)] + [f"ask{i}" for i in range(1, 6)]


def _mkdir(p):
    os.makedirs(p, exist_ok=True)
    return p


def _row(q: dict, ts: str) -> dict:
    r = {k: q.get(k) for k in ("code", "price", "change_pct", "last_close", "open", "high", "low", "vol", "amount")}
    for i in range(5):
        r[f"bid{i+1}"] = (q.get("bid") or [None] * 5)[i]
        r[f"ask{i+1}"] = (q.get("ask") or [None] * 5)[i]
    r["ts"] = ts
    return r


def cmd_scan(a):
    srv = load_servers()
    rk = rank_servers(srv)
    print(f"主站来源: {os.path.join(TDX_DIR, 'connect.cfg')}  共 {len(srv)} 个，可用 {len(rk)} 个\n")
    for ms, ip in rk:
        print(f"  {ip:16s} {ms:7.1f} ms")
    if not rk:
        sys.exit("没有任何可用主站")


def cmd_watchlist(a):
    wl = read_watchlist()
    print(f"通达信自选股（{len(wl)} 只）: {' '.join(wl) if wl else '(空)'}")


def cmd_quote(a):
    c = TdxLive(verbose=a.verbose).connect()
    for code in a.codes:
        q = c.quote(code, market=a.market)
        if not q:
            print(f"{code}: 无数据"); continue
        print(f"{q['code']}  {q['price']:>10.3f}  {q['change_pct']:>+6.2f}%   "
              f"昨收 {q['last_close']:>9.3f}  开 {q['open']:>9.3f}  高 {q['high']:>9.3f}  低 {q['low']:>9.3f}   "
              f"量 {q['vol']/1e4:>10.1f}万股  额 {q['amount']/1e8:>7.2f}亿")
        if a.verbose:
            def _f(v):
                return f"{v:>9.3f}" if isinstance(v, (int, float)) else "        -"
            for i in range(5):
                print(f"     买{i+1} {_f(q['bid'][i])} x {q['bid_vol'][i]:<8}   卖{i+1} {_f(q['ask'][i])} x {q['ask_vol'][i]}")
    c.close()


def cmd_snap(a):
    c = TdxLive(verbose=a.verbose).connect()
    for code in a.codes:
        s = c.snap(code)
        print(f"{code}: 最新 {s['last'] if s else None}  分时点数 {len(s['prices']) if s else 0}")
        if a.verbose and s:
            print("   ", s["prices"])
    c.close()


def cmd_bars(a):
    c = TdxLive(verbose=a.verbose).connect()
    out = _mkdir(a.out)
    for code in a.codes:
        bars = c.bars(code, a.period, a.count, market=a.market)
        if not bars:
            print(f"{code}: 空"); continue
        p = os.path.join(out, f"bars_{code}_{a.period}.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(bars[0].keys()))
            w.writeheader(); w.writerows(bars)
        print(f"{code} {a.period} {len(bars)} 根 -> {p}   末根 {bars[-1]['datetime']} C={bars[-1]['close']}")
    c.close()


def _resolve_codes(a) -> list[str]:
    if getattr(a, "codes", None):
        return [c.strip() for c in a.codes if c.strip()]
    wl = read_watchlist()
    if not wl:
        sys.exit("没传 --codes 且通达信自选股为空；用 --codes 600519,000001 指定")
    print(f"[watch] 使用通达信自选股 {len(wl)} 只: {' '.join(wl)}")
    return wl


def cmd_collect(a):
    codes = _resolve_codes(a)
    out = _mkdir(a.out)
    day = dt.datetime.now().strftime("%Y%m%d")
    tick_path = os.path.join(out, f"ticks_{day}.csv")
    bar_path = os.path.join(out, f"bars1m_{day}.csv")
    latest = os.path.join(out, "latest.json")

    new_tick = not os.path.exists(tick_path)
    new_bar = not os.path.exists(bar_path)
    ftick = open(tick_path, "a", newline="", encoding="utf-8-sig")
    wtick = csv.DictWriter(ftick, fieldnames=TICK_COLS)
    if new_tick:
        wtick.writeheader()
    bar_cols = ["datetime", "code", "open", "high", "low", "close", "vol", "amount",
                "up_count", "down_count"]
    fbar = open(bar_path, "a", newline="", encoding="utf-8-sig")
    wbar = csv.DictWriter(fbar, fieldnames=bar_cols)
    if new_bar:
        wbar.writeheader()

    n_conn = max(2, min(a.conn, max(2, len(codes))))
    print(f"[watch] {len(codes)} 只 / {n_conn} 路并发  轮询间隔 {a.interval}s  "
          f"交易时段={is_trading_time()}  输出 {out}")
    t_end = time.time() + a.duration if a.duration else None
    last_min = None
    rounds = 0
    try:
        with TdxPool(n=n_conn, verbose=a.verbose) as pool:
            while True:
                t0 = time.time()
                quotes = pool.batch(codes, lambda c, code: c.quote(code))
                ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                live = {}
                for q in quotes:
                    if q and q.get("price"):
                        wtick.writerow(_row(q, ts))
                        live[q["code"]] = q
                ftick.flush()
                json.dump({"updated": ts, "trading": is_trading_time(), "stocks": live},
                          open(latest, "w", encoding="utf-8"), ensure_ascii=False)
                rounds += 1
                ok = len(live)
                # 每分钟收 1 分钟 K 线（缠论用）
                cur_min = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                if a.bars1m and cur_min != last_min and (is_trading_time() or a.force):
                    last_min = cur_min
                    bars = pool.batch(codes, lambda c, code: c.bars(code, "1m", 3))
                    for code, b in zip(codes, bars):
                        if b:
                            for k in b:
                                wbar.writerow({"datetime": k.get("datetime"), "code": code,
                                               "open": k.get("open"), "high": k.get("high"),
                                               "low": k.get("low"), "close": k.get("close"),
                                               "vol": k.get("vol"), "amount": k.get("amount"),
                                               "up_count": k.get("up_count"), "down_count": k.get("down_count")})
                    fbar.flush()
                el = time.time() - t0
                print(f"  {ts}  成功 {ok}/{len(codes)}  耗时 {el*1000:.0f}ms", flush=True)
                if not is_trading_time() and not a.force:
                    print("[watch] 非交易时段，结束（加 --force 可强制跑）")
                    break
                if t_end and time.time() >= t_end:
                    print(f"[watch] 到达 duration，共 {rounds} 轮")
                    break
                time.sleep(max(0.0, a.interval - el))
    except KeyboardInterrupt:
        print(f"\n[watch] 手动停止，共 {rounds} 轮")
    finally:
        ftick.close(); fbar.close()
        print(f"[watch] 明细 -> {tick_path}\n[watch] 分钟线 -> {bar_path}\n[watch] 最新快照 -> {latest}")


def cmd_market(a):
    """全市场快照（多路并发）。"""
    codes = a.codes
    if not codes:
        codes = _all_a_codes()
    out = _mkdir(a.out)
    p = os.path.join(out, f"market_{dt.datetime.now():%Y%m%d_%H%M%S}.csv")
    print(f"[market] {len(codes)} 只 / {a.conn} 路并发 ...")
    n_conn = max(2, min(a.conn, len(codes)))
    rows = []
    with TdxPool(n=n_conn, verbose=a.verbose) as pool:
        t0 = time.time()
        def step(conn, code):
            q = conn.quote(code)
            return {"code": code, "price": q["price"], "change_pct": q["change_pct"],
                    "vol": q["vol"], "amount": q["amount"]} if q and q.get("price") else None
        # 分块，便于进度与失败重试
        CH = max(200, len(codes) // 20 + 1)
        for i in range(0, len(codes), CH):
            part = codes[i:i + CH]
            res = pool.batch(part, step, workers=n_conn)
            rows += [r for r in res if r]
            print(f"  {i+len(part)}/{len(codes)}  已用 {time.time()-t0:.0f}s", flush=True)
        el = time.time() - t0
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["code", "price", "change_pct", "vol", "amount"])
        w.writeheader(); w.writerows(rows)
    el = time.time() - t0
    print(f"[market] 成功 {len(rows)}/{len(codes)}  耗时 {el:.1f}s ({len(rows)/el:.0f} 只/秒) -> {p}")


def _all_a_codes() -> list[str]:
    """从通达信本地 vipdoc 目录列出全部 A 股代码（无需联网）。"""
    codes = []
    for mk, pre in (("sh", "6"), ("sz", "0")):
        d = os.path.join(TDX_DIR, "vipdoc", mk, "lday")
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".day"):
                continue
            code = fn[2:8]
            if len(code) == 6 and code.isdigit():
                codes.append(code)
    # 3 开头创业板/0 开头深市都在 sz 里；6 开头沪市在 sh
    return sorted(set(codes))


def main():
    ap = argparse.ArgumentParser(description="通达信实时行情工具（2026-09 新协议）")
    ap.add_argument("--verbose", "-v", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true")

    sub.add_parser("scan", parents=[common]).set_defaults(fn=cmd_scan)
    sub.add_parser("watchlist", parents=[common]).set_defaults(fn=cmd_watchlist)

    p = sub.add_parser("quote", parents=[common]); p.add_argument("codes", nargs="+"); p.add_argument("--market", type=int); p.set_defaults(fn=cmd_quote)
    p = sub.add_parser("snap", parents=[common]);  p.add_argument("codes", nargs="+"); p.add_argument("--market", type=int); p.set_defaults(fn=cmd_snap)
    p = sub.add_parser("bars", parents=[common]);  p.add_argument("codes", nargs="+")
    p.add_argument("--period", default="1m"); p.add_argument("--count", type=int, default=240)
    p.add_argument("--market", type=int); p.add_argument("--out", default="data"); p.set_defaults(fn=cmd_bars)

    p = sub.add_parser("collect", parents=[common], help="盘中轮询采集（默认采通达信自选股）")
    p.add_argument("--codes", default=""); p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--duration", type=int, default=0); p.add_argument("--conn", type=int, default=8)
    p.add_argument("--out", default="data"); p.add_argument("--bars1m", action="store_true", default=True)
    p.add_argument("--no-bars1m", dest="bars1m", action="store_false")
    p.add_argument("--force", action="store_true", help="非交易时段也跑")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("market", parents=[common], help="全市场快照")
    p.add_argument("--codes", default=""); p.add_argument("--conn", type=int, default=16)
    p.add_argument("--out", default="data"); p.set_defaults(fn=cmd_market)

    a = ap.parse_args()
    if getattr(a, "codes", None) and isinstance(a.codes, str) and a.cmd in ("collect", "market"):
        a.codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    a.fn(a)


if __name__ == "__main__":
    main()
