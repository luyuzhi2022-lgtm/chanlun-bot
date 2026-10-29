# -*- coding: utf-8 -*-
"""
tdxlive.py — 通达信行情实时接入核心库（2026-09 新协议，低延迟 ~80ms）

背景（2026-10-16 本机实测结论）：
  * 老 pytdx / mootdx 的线上行情接口已失效：服务器返回空数据（2026-09-10 通达信改了协议）。
    实测 pytdx get_security_quotes / get_security_bars 在 5 个主站上全部返回 0 条。
  * 新协议握手 = 0c02(init) + 0c03(tdxlevel 认证)，之后普通命令可用；
    老 pytdx 的 K线/报价命令只需把版本标识换成 18 7b 00 01，响应字段 vol/amount 由 u32 变 f32。
  * 主站列表可直接复用本地通达信客户端的 connect.cfg（本机 73 个 IP，实测 45 个可用，最快 ~15ms）。

用法：
    from tdxlive import TdxLive, TdxPool, read_watchlist
    c = TdxLive()                       # 自动从 connect.cfg 选最快主站
    c.quote('600519')                   # 单只盘口（现价/昨收/开高低/量额/五档）
    c.bars('600519', '1m', 240)         # 当日 1 分钟 K 线（缠论用）
    c.close()

    p = TdxPool(n=8)                    # 8 个主站并发
    p.batch(['600519','000001'], lambda c, code: c.quote(code))
"""
from __future__ import annotations

import os
import re
import select
import socket
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# ---------------------------------------------------------------- 常量

TDX_DIR = os.environ.get("TDX_DIR", r"C:\new_tdx64")
PORT = 7709

C02 = bytes.fromhex("0c0218940001030003000d0001")
C03 = bytes.fromhex(
    "0c031899000120002000db0f7464786c6576656c"
    "000000295cf740110000000000000000000000000005"
)
SN_PREFIX = bytes.fromhex("0c090041030027002700d10f")
SN_SUFFIX = bytes.fromhex("0000000000000000000000000000000001001400000000010000000000")

# 内置兜底主站（connect.cfg 读不到时用）
FALLBACK_SERVERS = [
    "101.35.121.35", "122.51.120.217", "123.60.73.44", "123.60.164.122",
    "129.211.70.79", "123.60.84.66", "111.229.247.189", "124.71.187.122",
    "119.97.185.59", "124.223.163.242", "82.156.174.84", "43.139.18.171",
]

# K线周期名 -> 协议 category
PERIODS = {
    "1m": 8, "5m": 0, "15m": 1, "30m": 2, "60m": 3,
    "day": 9, "week": 6, "month": 7, "1d": 9, "1w": 6, "1M": 7,
    "day_hfq": 4,
}


# ---------------------------------------------------------------- 主站

def load_servers(tdx_dir: str = TDX_DIR) -> list[str]:
    """优先从本地通达信客户端 connect.cfg 提取主站 IP（与客户端用的是同一批服务器）。"""
    cfg = os.path.join(tdx_dir, "connect.cfg")
    if os.path.exists(cfg):
        try:
            raw = open(cfg, "rb").read()
            seen, out = set(), []
            for m in re.findall(rb"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", raw):
                ip = m.decode()
                if ip.startswith(("0.", "127.", "255.")) or ip in seen:
                    continue
                seen.add(ip)
                out.append(ip)
            if out:
                return out
        except Exception:
            pass
    return list(FALLBACK_SERVERS)


def probe_server(ip: str, timeout: float = 2.0) -> Optional[float]:
    """TCP 7709 连通性 + 延迟（ms），不可用返回 None。"""
    s = socket.socket()
    s.settimeout(timeout)
    t0 = time.time()
    try:
        s.connect((ip, PORT))
        return round((time.time() - t0) * 1000, 1)
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


_RANK_CACHE: dict[tuple, list] = {}


def rank_servers(servers: Optional[list[str]] = None, workers: int = 32,
                 use_cache: bool = True) -> list[tuple[float, str]]:
    """并发探测，返回 [(延迟ms, ip), ...] 按延迟升序。结果在进程内缓存（探测要 1~2 秒）。"""
    servers = servers or load_servers()
    key = tuple(servers)
    if use_cache and key in _RANK_CACHE:
        return _RANK_CACHE[key]
    res = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for ip, ms in zip(servers, ex.map(probe_server, servers)):
            if ms is not None:
                res.append((ms, ip))
    res.sort()
    if use_cache:
        _RANK_CACHE[key] = res
    return res


# ---------------------------------------------------------------- 市场号

def infer_market(code: str) -> int:
    """代码 -> 市场号。0=深市 1=沪市 2=北交所
    注意：
      * 5 开头是沪市基金（510300/588000），88 开头是沪市板块指数 —— 都是 1。
      * 北交所（43/83/87/92 开头）是 2。
      * 000xxx 有歧义：000001 是平安银行(深0)，但 000300/000905/000852 等是沪市指数(1)。
        指数请显式传 market=1，或见下面的 SH_INDEX_CODES 白名单。
    """
    code = code.strip()
    if code in SH_INDEX_CODES:
        return 1
    if code.startswith(("43", "83", "87", "92")):
        return 2
    if code.startswith("88"):        # 板块指数（880xxx）
        return 1
    if code[:1] in ("5", "6", "9"):  # 沪市A/B、ETF、指数（999999）
        return 1
    return 0                          # 深市 0/3/399 开头


# 沪市指数白名单（000xxx 段与深市股票重号）
SH_INDEX_CODES = {
    "000300", "000016", "000010", "000015", "000903", "000905", "000852",
    "000688", "000922", "000932", "000133", "000906", "000698", "000985",
}


# ---------------------------------------------------------------- 变长价解码（同 pytdx，MIT）

def _get_price(data: bytes, pos: int) -> tuple[int, int]:
    pos_byte = 6
    bdata = data[pos]
    intdata = bdata & 0x3F
    sign = bool(bdata & 0x40)
    if bdata & 0x80:
        while True:
            pos += 1
            bdata = data[pos]
            intdata += (bdata & 0x7F) << pos_byte
            pos_byte += 7
            if not (bdata & 0x80):
                break
    pos += 1
    return (-intdata if sign else intdata), pos


def _decompress(resp: bytes) -> bytes:
    i = resp.find(b"\x78\x9c")
    if i >= 0:
        try:
            return resp[:i] + zlib.decompress(resp[i:])
        except Exception:
            pass
    return resp


# ---------------------------------------------------------------- 协议构造/解析

def build_quote_req(items: list[tuple[int, str]]) -> bytes:
    """items: [(market, code)]。注意：服务端只认单只（多只会返回空），别指望批量。"""
    n = len(items)
    pkgdatalen = n * 7 + 12
    hdr = struct.pack("<HIHHIIHH", 0x10C, 0x02006320, pkgdatalen, pkgdatalen, 0x5053E, 0, 0, n)
    body = b"".join(struct.pack("<B6s", m, c.encode()) for m, c in items)
    return hdr + body


def parse_quote(resp: bytes) -> Optional[dict]:
    """解析单只报价。响应 100B 左右：帧头 18B + count(2) + market(1) + code(6) + ..."""
    resp = _decompress(resp)
    for head_len in (16, 18, 20):
        b = resp[head_len:]
        if len(b) < 12:
            continue
        (num,) = struct.unpack("<H", b[0:2])
        if num != 1:
            continue
        pos = 2
        market, code, _active1 = struct.unpack("<B6sH", b[pos:pos + 9])
        pos += 9
        base, pos = _get_price(b, pos)
        lc, pos = _get_price(b, pos)
        op, pos = _get_price(b, pos)
        hi, pos = _get_price(b, pos)
        lo, pos = _get_price(b, pos)
        _r0, pos = _get_price(b, pos)
        _r1, pos = _get_price(b, pos)
        vol, pos = _get_price(b, pos)
        cur_vol, pos = _get_price(b, pos)
        (amount,) = struct.unpack("<f", b[pos:pos + 4]); pos += 4
        s_vol, pos = _get_price(b, pos)
        b_vol, pos = _get_price(b, pos)
        # 新协议在 b_vol 之后仍有两个变长字段
        _x, pos = _get_price(b, pos)
        _y, pos = _get_price(b, pos)
        res = {
            "code": code.decode(errors="ignore"),
            "market": market,
            "price": round(base / 100, 3),
            "last_close": round((base + lc) / 100, 3),
            "open": round((base + op) / 100, 3),
            "high": round((base + hi) / 100, 3),
            "low": round((base + lo) / 100, 3),
            "vol": vol * 100,          # 手 -> 股
            "cur_vol": cur_vol * 100,
            "amount": amount,
            "bid": [], "ask": [], "bid_vol": [], "ask_vol": [],
        }
        idx_like = res["code"].startswith(("999", "399", "88")) or res["code"] in SH_INDEX_CODES
        for _ in range(5):
            p, pos = _get_price(b, pos)
            a, pos = _get_price(b, pos)
            av, pos = _get_price(b, pos)
            bv, pos = _get_price(b, pos)
            bp, ap = (base + p) / 100, (base + a) / 100
            # 指数没有五档，这些字节是无关数据：置 None（长度恒为 5，调用方不用改）
            res["bid"].append(None if idx_like or not (0.2 * res["price"] < bp < 5 * res["price"]) else round(bp, 3))
            res["ask"].append(None if idx_like or not (0.2 * res["price"] < ap < 5 * res["price"]) else round(ap, 3))
            res["bid_vol"].append(av)
            res["ask_vol"].append(bv)
        res["change_pct"] = round((res["price"] - res["last_close"]) / res["last_close"] * 100, 2) if res["last_close"] else 0.0
        return res
    return None


def build_bars_req(category: int, market: int, code: str, start: int, count: int) -> bytes:
    values = (0x10C, 0x01007B18, 0x1C, 0x1C, 0x052D, market, code.encode(), category, 1, start, count, 0, 0, 0)
    return struct.pack("<HIHHHH6sHHHHIIH", *values)


def _bar_ok(r: dict) -> bool:
    """记录合理性校验。指数短周期 K 线在通达信协议里布局与个股不同，会导致解析错位，
    错位后的记录一定违反这些基本约束，用校验把垃圾记录挡掉。"""
    try:
        y, mo, d = int(r["datetime"][0:4]), int(r["datetime"][5:7]), int(r["datetime"][8:10])
        hh, mm = int(r["datetime"][11:13]), int(r["datetime"][14:16])
    except Exception:
        return False
    return (1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31
            and 0 <= hh <= 23 and 0 <= mm <= 59
            and r["high"] >= r["low"] > 0 and r["vol"] >= 0)


def parse_bars(body: bytes, category: int, index: bool = False) -> list[dict]:
    """与 pytdx GetSecurityBars 等价；新协议 vol/amount 是 f32。
    ⚠️ 指数（999999/399xxx/880xxx）的记录在 vol/amount 之后**多 4 字节**（u16 涨家数 + u16 跌家数），
    不按 index=True 解会整体错位，只能解出第 1 根。指数 K 线必须用 index=True。
    遇到不合理记录就停（说明布局不匹配），只返回前面正确的部分。"""
    if len(body) < 2:
        return []
    (ret,) = struct.unpack("<H", body[0:2])
    pos, pre, out = 2, 0, []
    for _ in range(ret):
        if category < 4 or category in (7, 8):
            zipday, tminutes = struct.unpack("<HH", body[pos:pos + 4]); pos += 4
            year = (zipday >> 11) + 2004
            month = int((zipday % 2048) / 100)
            day = (zipday % 2048) % 100
            hour, minute = divmod(tminutes, 60)
            ts = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        else:
            d = struct.unpack("<I", body[pos:pos + 4])[0]; pos += 4
            ts = f"{d // 10000:04d}-{d // 100 % 100:02d}-{d % 100:02d} 00:00"
        od, pos = _get_price(body, pos)
        cd, pos = _get_price(body, pos)
        hd, pos = _get_price(body, pos)
        ld, pos = _get_price(body, pos)
        vol, amt = struct.unpack("<ff", body[pos:pos + 8]); pos += 8
        up = down = None
        if index:
            up, down = struct.unpack("<HH", body[pos:pos + 4]); pos += 4
        o = (od + pre) / 1000
        c = (od + pre + cd) / 1000
        h = (od + pre + hd) / 1000
        l = (od + pre + ld) / 1000
        pre = od + cd + pre
        rec = {"datetime": ts, "open": round(o, 3), "high": round(h, 3),
               "low": round(l, 3), "close": round(c, 3), "vol": vol, "amount": amt}
        if index:
            rec["up_count"] = up
            rec["down_count"] = down
        if not _bar_ok(rec):
            break
        out.append(rec)
    return out


def is_index_code(code: str) -> bool:
    """指数（板块指数、沪深指数）：K线记录比个股多 4 字节。"""
    code = code.strip()
    return code.startswith(("999", "399", "88")) or code in SH_INDEX_CODES


# ---------------------------------------------------------------- 连接

class TdxConn:
    """单条到通达信主站的连接。延迟主要由 quiet（静默等待）决定：0.05 -> ~78ms。"""

    def __init__(self, host: str, port: int = PORT, timeout: float = 6.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock: Optional[socket.socket] = None

    # ---- 底层
    def connect(self):
        s = socket.socket()
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        self.sock = s
        s.sendall(C02); self._drain(0.05, 1.0)
        s.sendall(C03); self._drain(0.10, 1.0)
        return self

    def _drain(self, quiet: float, cap: float) -> bytes:
        """读干净：等到 quiet 秒没有新数据为止。这是低延迟的关键。"""
        buf, t0 = b"", time.time()
        while time.time() - t0 < cap:
            r, _, _ = select.select([self.sock], [], [], quiet)
            if not r:
                break
            ch = self.sock.recv(65536)
            if not ch:
                raise ConnectionResetError("server closed")
            buf += ch
        return buf

    def _flush(self):
        """发请求前清掉残留字节，避免错帧。"""
        while True:
            r, _, _ = select.select([self.sock], [], [], 0)
            if not r:
                return
            try:
                if not self.sock.recv(65536):
                    raise ConnectionResetError("server closed")
            except (BlockingIOError, socket.timeout):
                return

    def _req(self, payload: bytes, quiet: float = 0.05, cap: float = 6.0) -> bytes:
        if self.sock is None:
            raise RuntimeError("未连接")
        self._flush()
        self.sock.sendall(payload)
        return self._drain(quiet, cap)

    # ---- 业务接口
    def quote(self, code: str, market: Optional[int] = None) -> Optional[dict]:
        m = infer_market(code) if market is None else market
        return parse_quote(self._req(build_quote_req([(m, code)])))

    def snap(self, code: str, market: Optional[int] = None) -> Optional[dict]:
        """当日分时快照：最新价 + 最近若干分时点。
        ⚠️ prices 不是完整当日分时（实测一盘 62 点且含无效填充），
        要完整分钟序列请用 bars(code,'1m',240)。"""
        m = infer_market(code) if market is None else market
        r = self._req(SN_PREFIX + bytes([m, 0]) + code.encode() + SN_SUFFIX)
        d = _decompress(r)
        if len(d) < 60:
            return None
        vals, p = [], 50
        while p + 4 <= len(d):
            v = struct.unpack("<f", d[p:p + 4])[0]
            if 0.1 < v < 100000:
                vals.append(round(v, 3)); p += 4
            else:
                p += 2
        return {"code": code, "last": vals[-1] if vals else None, "prices": vals}

    def bars(self, code: str, period: str = "1m", count: int = 240,
             start: int = 0, market: Optional[int] = None) -> list[dict]:
        """历史K线。响应体可能 zlib 压缩且分多个 TCP 段到达，所以要按“条数到齐”判定读完，
        不能用固定静默时间（count 大时固定静默会截断 -> 解析出 0 条）。"""
        cat = PERIODS.get(period, period if isinstance(period, int) else 8)
        m = infer_market(code) if market is None else market
        idx = is_index_code(code)
        payload = build_bars_req(cat, m, code, start, count)
        if self.sock is None:
            raise RuntimeError("未连接")
        self._flush()
        self.sock.sendall(payload)
        buf = b""
        quiet = 0.08 if count <= 20 else 0.30
        deadline = time.time() + min(20.0, 1.0 + count * 0.03)
        while time.time() < deadline:
            buf += self._drain(quiet, 6.0)
            if self._bars_ready(buf, count):
                break
            quiet = 0.50
        r = _decompress(buf)
        return parse_bars(r[16:], cat, index=idx) if len(r) > 18 else []

    @staticmethod
    def _bars_ready(buf: bytes, count: int) -> bool:
        try:
            d = _decompress(buf)
            if len(d) < 18:
                return False
            (n,) = struct.unpack("<H", d[16:18])
            return n >= count or n == 0
        except Exception:
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class TdxLive(TdxConn):
    """自动优选主站的高层客户端；断线自动换站重连。"""

    def __init__(self, servers: Optional[list[str]] = None, verbose: bool = False):
        self.servers = servers or load_servers()
        self._ranked: Optional[list[str]] = None
        self.verbose = verbose
        super().__init__(host="")

    def _order(self) -> list[str]:
        if self._ranked is None:
            ranked = rank_servers(self.servers)
            if not ranked:
                raise RuntimeError("没有任何可用的通达信主站（检查网络/防火墙）")
            self._ranked = [ip for _, ip in ranked]
            if self.verbose:
                print("[tdxlive] 可用主站:", ", ".join(f"{ip}({ms}ms)" for ms, ip in ranked[:5]))
        return self._ranked

    def connect(self):
        last = None
        for ip in self._order()[:6]:
            try:
                self.host = ip
                super().connect()
                return self
            except Exception as e:
                last = e
                if self.verbose:
                    print(f"[tdxlive] {ip} 连接失败: {e}")
        raise RuntimeError(f"所有主站连接失败: {last}")

    def _with_retry(self, fn, *a, **kw):
        if self.sock is None:
            self.connect()
        try:
            return fn(self, *a, **kw)
        except Exception:
            self.close()
            self._ranked = None
            self.connect()
            return fn(self, *a, **kw)

    def quote(self, code, market=None):
        return self._with_retry(TdxConn.quote, code, market)

    def snap(self, code, market=None):
        return self._with_retry(TdxConn.snap, code, market)

    def bars(self, code, period="1m", count=240, start=0, market=None):
        return self._with_retry(TdxConn.bars, code, period, count, start, market)


class TdxPool:
    """多个主站连接并发——单只 ~78ms，N 路并发把自选股/全市场的刷新压到秒级。
    实测：12 路并发 5000 只耗时约 33 秒；自选股 30 只 12 路并发 < 0.3 秒。"""

    def __init__(self, n: int = 8, servers: Optional[list[str]] = None, verbose: bool = False):
        ranked = rank_servers(servers or load_servers())
        if not ranked:
            raise RuntimeError("没有可用主站")
        self.ips = [ip for _, ip in ranked][:n]
        self.conns = [TdxConn(ip) for ip in self.ips]
        self.ok = [False] * len(self.conns)
        if verbose:
            print(f"[tdxlive] Pool {len(self.conns)} 路 -> {self.ips}")

    def __enter__(self):
        for i, c in enumerate(self.conns):
            try:
                c.connect(); self.ok[i] = True
            except Exception:
                self.ok[i] = False
        return self

    def __exit__(self, *a):
        self.close()

    def batch(self, codes: list[str], fn, workers: Optional[int] = None) -> list:
        """fn(conn, code) 并发执行，返回与 codes 同序的结果（失败为 None）。"""
        conns = [c for c, o in zip(self.conns, self.ok) if o]
        if not conns:
            raise RuntimeError("Pool 内没有可用连接")
        out: list = [None] * len(codes)
        lock = threading.Lock()
        rr = {"i": 0}

        def work(idx_code):
            idx, code = idx_code
            with lock:
                c = conns[rr["i"] % len(conns)]
                rr["i"] += 1
            try:
                return idx, fn(c, code)
            except Exception:
                try:
                    c.close(); c.connect()
                    return idx, fn(c, code)
                except Exception:
                    return idx, None

        with ThreadPoolExecutor(max_workers=workers or len(conns)) as ex:
            for idx, val in ex.map(work, list(enumerate(codes))):
                out[idx] = val
        return out

    def close(self):
        for c in self.conns:
            c.close()


# ---------------------------------------------------------------- 自选股 / 交易日历

def _parse_blk(path: str) -> list[str]:
    """通达信 .blk 每行 7 字符 = 1 位市场号 + 6 位代码，例如 1600089 -> 600089。
    也兼容只有 6 位代码的写法。返回 6 位代码列表。"""
    txt = open(path, "rb").read().decode("gbk", "ignore")
    codes = []
    for tok in re.split(r"[\s,;|]+", txt):
        tok = tok.strip()
        if not tok:
            continue
        m = re.search(r"(\d{6,7})$", tok)
        if not m:
            continue
        t = m.group(1)
        codes.append(t[-6:] if len(t) == 7 else t)
    return list(dict.fromkeys(codes))


def read_watchlist(tdx_dir: str = TDX_DIR) -> list[str]:
    """读通达信客户端的自选股（T0002/blocknew/zxg.blk），返回 6 位代码列表。"""
    p = os.path.join(tdx_dir, "T0002", "blocknew", "zxg.blk")
    return _parse_blk(p) if os.path.exists(p) else []


def read_block(tdx_dir: str, block: str = "ZXG") -> list[str]:
    """读通达信自定义板块文件 T0002/blocknew/<block>.blk。"""
    p = os.path.join(tdx_dir, "T0002", "blocknew", f"{block.lower()}.blk")
    if not os.path.exists(p):
        return []
    return _parse_blk(p)


def is_trading_time(now=None) -> bool:
    """A 股连续竞价时段：09:30-11:30, 13:00-15:00，周一至周五。"""
    import datetime as _dt
    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)


if __name__ == "__main__":
    import sys
    codes = sys.argv[1:] or ["600519", "000001", "300750"]
    c = TdxLive(verbose=True).connect()
    for code in codes:
        q = c.quote(code)
        if q:
            print(f"{q['code']}  {q['price']:>9}  {q['change_pct']:>6}%  高{q['high']} 低{q['low']} "
                  f"量{q['vol']/1e4:.0f}万股  买一{q['bid'][0] if q['bid'] else '-'} 卖一{q['ask'][0] if q['ask'] else '-'}")
        else:
            print(f"{code} 无数据")
    b = c.bars(codes[0], "1m", 5)
    print(f"1分钟K线末根: {b[-1] if b else '空'}")
    c.close()
