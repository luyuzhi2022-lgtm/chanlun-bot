# -*- coding: utf-8 -*-
"""tdxlive.py 验收：主站测速 / 报价 / 各市场 / K线 / 并发池 / 自选股"""
import time, json, sys
sys.path.insert(0, r'E:\Hermes\tdx-live')
from tdxlive import (TdxLive, TdxPool, rank_servers, load_servers,
                     read_watchlist, infer_market, is_trading_time)

print("=== 1) 主站（从本地通达信 connect.cfg 读） ===")
srv = load_servers()
rk = rank_servers(srv)
print(f"   connect.cfg 共 {len(srv)} 个，可用 {len(rk)} 个，最快 5 个:")
for ms, ip in rk[:5]:
    print(f"     {ip:16s} {ms:5.1f} ms")

c = TdxLive(verbose=True).connect()
print(f"   已连上: {c.host}")

print("\n=== 2) 实时报价（含五档） ===")
for code in ['600519', '000001', '300750', '688981', '002594']:
    q = c.quote(code)
    if q:
        print(f"   {q['code']} {q['price']:>9} {q['change_pct']:>6}%  昨收{q['last_close']} 高{q['high']} 低{q['low']} "
              f"额{q['amount']/1e8:.2f}亿  买1 {q['bid'][0]}({q['bid_vol'][0]}) 卖1 {q['ask'][0]}({q['ask_vol'][0]})")

print("\n=== 3) 市场号推断校验（含北交所/ETF/板块指数/指数） ===")
for code, m in [('600519', 1), ('000001', 0), ('300750', 0), ('688981', 1), ('510300', 1),
                ('430047', 2), ('880660', 1), ('999999', 1), ('000300', 1)]:
    got = infer_market(code)
    q = c.quote(code)
    px = q['price'] if q else None
    print(f"   {code}: infer={got} 期望={m} {'OK' if got==m else '⚠️'}  price={px}"
          + ("" if px else "  (=None 用显式 market 再试 " + str(c.quote(code, market=m) and '成功') + ")"))

print("\n=== 4) 各周期 K 线（缠论级别） ===")
for p in ['1m', '5m', '15m', '30m', '60m', 'day']:
    b = c.bars('600519', p, 6)
    print(f"   {p:4s} {len(b)}根  末根 {b[-1]['datetime']} O{b[-1]['open']} H{b[-1]['high']} L{b[-1]['low']} C{b[-1]['close']} V{b[-1]['vol']:.0f}" if b else f"   {p}: 空")

print("\n=== 5) 延迟（20 次 quote） ===")
lat = []
for _ in range(20):
    t0 = time.time(); c.quote('600519'); lat.append(time.time()-t0)
print(f"   平均 {sum(lat)/len(lat)*1000:.1f} ms  最快 {min(lat)*1000:.1f}  最慢 {max(lat)*1000:.1f}")
c.close()

print("\n=== 6) 并发池 ===")
codes = ['600519','000001','000858','601318','300750','688981','000059','002594',
         '600036','000333','601899','600030','002415','300059','601988','600887']
with TdxPool(n=8, verbose=True) as pool:
    t0 = time.time()
    res = pool.batch(codes, lambda conn, code: (conn.quote(code) or {}).get('price'))
    el = time.time()-t0
    print(f"   {len(codes)} 只 / 8 路并发: {el*1000:.0f} ms   速度 {len(codes)/el:.1f} 只/秒")
    for cd, px in list(zip(codes, res))[:8]:
        print(f"     {cd} {px}")
    t0 = time.time()
    res2 = pool.batch(codes, lambda conn, code: (conn.quote(code) or {}).get('price'))
    print(f"   第二轮: {(time.time()-t0)*1000:.0f} ms")

print("\n=== 7) 自选股 ===")
wl = read_watchlist()
print(f"   T0002/blocknew/zxg.blk -> {len(wl)} 只: {wl}")

print("\n=== 8) 交易时段判断 ===")
import datetime as dt
print(f"   now={dt.datetime.now():%Y-%m-%d %H:%M:%S}  在职交易时段={is_trading_time()}")
