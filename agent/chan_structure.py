# -*- coding: utf-8 -*-
"""缠论结构计算（工程实现）

口径来源：技能 chanlun-structure-parser / chanlun-zhongshu-mapper / chanlun-divergence-diagnosis。
凡原文未直接给出、属工程近似的，代码注释与输出里标注【工程约定】。
本模块只做结构，不下买卖指令、不预测涨跌。
"""
from __future__ import annotations

# ============================================================ 包含关系

def merge_klines(bars):
    """逐根处理 K 线包含关系（structure-parser 步骤2）。

    方向由已出现的相邻非包含关系确定：向上合并取高高，向下合并取低低。
    起始方向不足以确定时 dir=0（待定），按极值合并（保守）。
    返回 merged 列表，每项含 h/l 与原始 K 线索引区间 i0..i1。
    """
    m = []
    dir_ = 0
    for k, b in enumerate(bars):
        h, l = b["high"], b["low"]
        if not m:
            m.append({"h": h, "l": l, "i0": k, "i1": k, "dir": 0, "t0": b["datetime"], "t1": b["datetime"]})
            continue
        p = m[-1]
        contained = (h >= p["h"] and l <= p["l"]) or (h <= p["h"] and l >= p["l"])
        if contained:
            if dir_ > 0:
                p["h"], p["l"] = max(p["h"], h), max(p["l"], l)
            elif dir_ < 0:
                p["h"], p["l"] = min(p["h"], h), min(p["l"], l)
            else:
                p["h"], p["l"] = max(p["h"], h), min(p["l"], l)   # 方向待定
            p["i1"], p["t1"] = k, b["datetime"]
        else:
            dir_ = 1 if h > p["h"] else -1
            m.append({"h": h, "l": l, "i0": k, "i1": k, "dir": 0, "t0": b["datetime"], "t1": b["datetime"]})
    return m


# ============================================================ 分型

def find_fenxing(m):
    """在合并 K 线上识别顶/底分型（三根相邻，中间为极值，高低点同时满足）。

    时间双轨：dt = 极值所在时间；dt_confirm = 该分型最早可被确认的时间（第三根合并K线结束时）。
    """
    fx = []
    for i in range(1, len(m) - 1):
        a, b, c = m[i - 1], m[i], m[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"] and b["l"] > a["l"] and b["l"] > c["l"]:
            fx.append({"i": i, "type": "顶", "price": b["h"], "dt": b["t0"],
                       "dt_confirm": c["t1"], "bar": m[i]["i0"]})
        elif b["l"] < a["l"] and b["l"] < c["l"] and b["h"] < a["h"] and b["h"] < c["h"]:
            fx.append({"i": i, "type": "底", "price": b["l"], "dt": b["t0"],
                       "dt_confirm": c["t1"], "bar": m[i]["i0"]})
    return fx


# ============================================================ 笔

def build_bi(fx, min_gap=4, m=None):
    """连接笔：顶底交替 + 相邻分型间至少一根独立 K 线。

    min_gap=4（合并K线索引差）等价于"两个分型不共用K线且中间至少一根独立K线"【工程约定】。
    规则（避免级联删除）：
      · 与前一个已保留分型同类型 -> 保留更极端者（相等则保留较早者）
      · 类型不同且间隔 >= min_gap -> 保留，构成一笔端点
      · 类型不同但间隔不足 -> 丢弃后来者（它不可能成为合法笔端点）
    跨类型比价是错误的（顶价恒高于底价），会让整个笔序列被删空。
    """
    cur = []
    for f in fx:
        f = dict(f)
        while cur:
            last = cur[-1]
            if last["type"] == f["type"]:
                better = f["price"] > last["price"] if f["type"] == "顶" else f["price"] < last["price"]
                if better:
                    cur.pop()
                    continue
                f = None
                break
            if f["i"] - last["i"] >= min_gap:
                break
            f = None            # 间隔不足，丢弃后来者
            break
        if f is not None:
            cur.append(f)

    out = []
    for a, b in zip(cur, cur[1:]):
        ra, rb = (m[a["i"]]["i0"], m[b["i"]]["i1"]) if m else (a["bar"], b["bar"])
        out.append({
            "dir": "up" if b["type"] == "顶" else "down",
            "t0": a["dt"], "p0": a["price"], "t1": b["dt"], "p1": b["price"],
            "dt_confirm": b["dt_confirm"], "mi0": a["i"], "mi1": b["i"],
            "bar0": ra, "bar1": rb,
        })
    return out, cur


# ============================================================ 中枢

def _overlap(a, b):
    return min(a[1], b[1]) > max(a[0], b[0])


def build_zhongshu(bis, min_width_pct=0.0025):
    """中枢识别。【工程约定】用本级别"笔"近似"次级别走势段"。

    原文 ch20 口径：中枢由前三个连续次级别走势重叠确定，Z 走势段 A、C 同向，
    ZG = min(high_A, high_C)，ZD = max(low_A, low_C)；随走势走宽的是 GG/DD 而非 ZG/ZD。

    退化过滤【工程约定】：A、B、C 三段重叠区数值上非空但过窄时（宽度 < 价格 × min_width_pct），
    不是中枢，只是一次穿越。不加这道门槛会产出 [17.16, 17.17] 这种宽 0.01 元的假中枢。
    """
    out = []
    i, n = 0, len(bis)
    while i + 2 < n:
        a, b, c = bis[i], bis[i + 1], bis[i + 2]
        if a["dir"] != c["dir"]:
            i += 1
            continue
        a_r = (min(a["p0"], a["p1"]), max(a["p0"], a["p1"]))
        b_r = (min(b["p0"], b["p1"]), max(b["p0"], b["p1"]))
        c_r = (min(c["p0"], c["p1"]), max(c["p0"], c["p1"]))
        three = (max(a_r[0], b_r[0], c_r[0]), min(a_r[1], b_r[1], c_r[1]))
        ZG, ZD = min(a_r[1], c_r[1]), max(a_r[0], c_r[0])
        ref = c_r[1]
        if ZD >= ZG or three[0] >= three[1] or (ZG - ZD) < ref * min_width_pct:
            i += 1
            continue
        zs = {
            "ZD": round(ZD, 3), "ZG": round(ZG, 3), "dir": "up" if a["dir"] == "up" else "down",
            "t_start": a["t0"], "t_end": c["t1"], "bi_start": i, "g": [], "d": [],
            "GG": None, "DD": None, "G": None, "D": None, "confirmed": c["dt_confirm"],
            "ZDZ_left": None, "extended": 0, "state": "延续", "leave": None,
        }
        # 遍历 Z 段（与中枢同向的段）计算 GG/DD/G/D，并向后延伸
        j = i
        segs = []           # 与中枢有交集、被吸收的段
        while j < len(bis):
            bi = bis[j]
            if j >= i + 3:
                lo, hi = min(bi["p0"], bi["p1"]), max(bi["p0"], bi["p1"])
                if hi <= ZD or lo >= ZG:          # 与 [ZD,ZG] 无重叠 -> 离开中枢
                    zs["state"] = "离开"
                    zs["leave"] = {"dir": bi["dir"], "t": bi["t0"], "p0": bi["p0"],
                                   "p1": bi["p1"], "t1": bi["t1"], "bi_index": j,
                                   "side": "上" if lo >= ZG else "下"}
                    break
                zs["extended"] += 1
            if bi["dir"] == zs["dir"]:
                zs["g"].append(max(bi["p0"], bi["p1"]))
                zs["d"].append(min(bi["p0"], bi["p1"]))
            segs.append(j)
            j += 1
        if zs["g"]:
            zs["GG"] = round(max(zs["g"]), 3)
            zs["G"] = round(min(zs["g"]), 3)
            zs["D"] = round(max(zs["d"]), 3)
            zs["DD"] = round(min(zs["d"]), 3)
        zs["bi_end"] = max(segs) if segs else i + 2
        zs["seg_count"] = zs["bi_end"] - zs["bi_start"] + 1
        zs["t_last"] = bis[zs["bi_end"]]["t1"] if bis else zs["t_end"]
        # 延伸 >= 6 段（3+6=9）触发级别升级提示（ch33 / ch96）
        zs["level_up_hint"] = zs["extended"] >= 6
        out.append(zs)
        i = max(zs["bi_end"] + 1, i + 3)
    return out


def zhongshu_state(zss):
    """前后两个同级别中枢的关系（ch20/21 原文口径）。

    扩张：后 ZG < 前 ZD 且 后 GG ≥ 前 DD，或 后 ZD > 前 ZG 且 后 DD ≤ 前 GG → 形成更大级别中枢。
    趋势：后 GG < 前 DD 判为下跌及其延续；后 DD > 前 GG 判为上涨及其延续。
    """
    if not zss:
        return "无中枢（结构未成）"
    if len(zss) < 2:
        return "单一中枢（盘整）"
    p, q = zss[-2], zss[-1]
    if None in (p["GG"], p["DD"], q["GG"], q["DD"]):
        return "无法确认"
    if q["GG"] < p["DD"]:
        return "下跌趋势（后中枢整体低于前中枢）"
    if q["DD"] > p["GG"]:
        return "上涨趋势（后中枢整体高于前中枢）"
    if (q["ZG"] < p["ZD"] and q["GG"] >= p["DD"]) or (q["ZD"] > p["ZG"] and q["DD"] <= p["GG"]):
        return "扩张（正在形成更大级别中枢）"
    return "新生（同级别新中枢）"


# ============================================================ MACD

def ema(vals, n):
    k, out, e = 2.0 / (n + 1), [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """标准 MACD(12,26,9)。返回 dif/dea/hist 三个等长列表。"""
    if not closes:
        return [], [], []
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, signal)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def bi_macd_area(bi, hist, dif):
    """单笔区间的 MACD 力度：柱面积（按数值正负求和，不按颜色）。"""
    i0, i1 = bi.get("bar0", 0), bi.get("bar1", 0)
    seg = hist[i0:i1 + 1] if i1 >= i0 else []
    pos = sum(x for x in seg if x > 0)
    neg = sum(x for x in seg if x < 0)
    return round(pos, 4), round(neg, 4), round(abs(sum(seg)), 4), round(sum(seg), 4)


# ============================================================ 便捷入口

def analyze(bars, macd_hist=None, min_gap=4, min_width_pct=0.0025):
    """一次跑完 合并 -> 分型 -> 笔 -> 中枢。返回 dict。"""
    if not bars:
        return {}
    m = merge_klines(bars)
    fx = find_fenxing(m)
    bis, pts = build_bi(fx, min_gap, m)
    zss = build_zhongshu(bis, min_width_pct)
    return {"merged": m, "fx": fx, "bi": bis, "points": pts, "zs": zss,
            "bars": bars}
