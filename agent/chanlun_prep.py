# -*- coding: utf-8 -*-
"""缠论盘中扫描：采集自选股 -> 结构计算 -> 三类买卖点候选。

用法（必须用 tdx-live 的 venv）：
  E:/Hermes/tdx-live/.venv/Scripts/python.exe chanlun_prep.py --mode intraday
  E:/Hermes/tdx-live/.venv/Scripts/python.exe chanlun_prep.py --mode close
  ... --force      非交易时段也跑（调试用）

stdout = 精简 JSON，供 LLM（缠论技能）复核；完整版落在 out/prep_*.json。
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# tdx-live 的位置：默认与本仓库同级（../../tdx），可用 CHANLUN_TDX_LIVE 覆盖
TDX_LIVE = os.environ.get(
    "CHANLUN_TDX_LIVE",
    os.path.join(os.path.dirname(HERE), "tdx"),
)
sys.path.insert(0, TDX_LIVE)
sys.path.insert(0, HERE)

import chan_structure as cs                     # noqa: E402
from tdxlive import TdxLive, TdxPool, read_watchlist, is_trading_time, is_index_code  # noqa: E402

CONFIG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
OUT_DIR = os.path.join(HERE, CONFIG.get("out_dir", "out"))
STATE_PATH = os.path.join(HERE, "state", "seen.json")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- 状态去重

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(st):
    json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def _state_entry(v):
    """兼容历史格式：旧版 state 的值直接是「首次推送时间」字符串。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        return {"first": v, "last_push": v, "last_price": None, "pushes": 1}
    return {}


def mark_progress(r, now, state):
    """给候选判 is_new / is_update，并**只在「本轮确实该推」时**刷新推送基线。

    为什么不能每轮都刷新 last_push：复推阈值是「距上次推送 ≥ N 分钟 或 价格较上次推送
    变动 ≥ X%」。若每轮都刷新计时，阈值永远从头开始，复推永不触发 —— 这正是第一版的
    实际症状：10:01 推一条，之后一整天静默，即使股价当天又涨了 9%。

    阈值走 config.progress：min_interval_min（默认 60）、price_change_pct（默认 1.5）。
    注意「该推」只对 state ∈ {候选, 已确认} 成立：信息不足 / 已失效的候选记进 state
    只为去重，不占推送基线。
    """
    code = r.get("code")
    price = r.get("price")
    cfg = CONFIG.get("progress", {}) or {}
    min_min = float(cfg.get("min_interval_min", 60))
    pct = float(cfg.get("price_change_pct", 1.5))
    stamp = now.strftime("%Y-%m-%d %H:%M")

    for c in r.get("candidates", []):
        if c.get("type") in ("-", "中阴状态核验"):
            continue
        key = f"{code}|{c['type']}|{c.get('endpoint','')}|{c['state']}"
        c["key"] = key
        tradable = c.get("state") in ("候选", "已确认")
        ent = _state_entry(state.get(key))
        if ent:
            state[key] = ent      # 旧格式（字符串）转成 dict 后必须立刻回写：否则下面刷新的
                                  # 只是临时副本，基线更新会丢（实测：14:30 复推成功但 state 没变）

        if not ent:                                   # 首次见到这个 key
            c["is_new"], c["is_update"] = True, False
            state[key] = {"first": stamp,
                          "last_push": stamp if tradable else None,
                          "last_price": price if tradable else None,
                          "pushes": 1 if tradable else 0}
            c["push_count"] = state[key]["pushes"]
            continue

        c["is_new"] = False
        since = chg = None
        if ent.get("last_push"):
            try:
                since = (now - dt.datetime.strptime(ent["last_push"], "%Y-%m-%d %H:%M")
                         ).total_seconds() / 60
            except Exception:
                since = None
            if ent.get("last_price") and price:
                chg = (price / ent["last_price"] - 1) * 100
        c["since_last_push_min"] = int(since) if since is not None else None
        c["price_change_since_push_pct"] = round(chg, 2) if chg is not None else None
        c["is_update"] = bool(tradable and (
            (since is not None and since >= min_min)
            or (chg is not None and abs(chg) >= pct)))

        if c["is_update"]:
            bits = []
            if chg is not None:
                bits.append(f"现价 {price}，较上次推送价 {ent['last_price']} 变动 {chg:+.2f}%")
            if since is not None:
                bits.append(f"距上次推送 {int(since)} 分钟")
            c["progress"] = ("；".join(bits) + f"（仍为「{c['state']}」，非新信号，本条是进展更新）"
                             if bits else "进展更新")
            ent["last_push"] = stamp
            ent["last_price"] = price
            ent["pushes"] = int(ent.get("pushes", 0)) + 1
        c["push_count"] = int(ent.get("pushes", 0))


# ---------------------------------------------------------------- 结构

def structure(bars, level, min_gap=4):
    """bars -> 结构 + MACD。"""
    if not bars or len(bars) < 12:
        return None
    closes = [b["close"] for b in bars]
    dif, dea, hist = cs.macd(closes)
    s = cs.analyze(bars, min_gap=min_gap)
    if not s or not s.get("bi"):
        return {"level": level, "insufficient": True, "bars": len(bars)}
    bis = s["bi"]
    # 每笔的 MACD 力度
    for b in bis:
        p, n, ab, net = cs.bi_macd_area(b, hist, dif)
        b["macd_pos"], b["macd_neg"], b["macd_abs"], b["macd_net"] = p, n, ab, net
    # 同向笔序列里的"是否创新极值"（笔严格顶底交替，故前一同向笔 = bis[i-2]）
    for i, b in enumerate(bis):
        prev = bis[i - 2] if i >= 2 else None
        if prev is None:
            b["new_extreme"] = None
        elif b["dir"] == "down":
            b["new_extreme"] = b["p1"] < prev["p1"]
        else:
            b["new_extreme"] = b["p1"] > prev["p1"]
    zss = s["zs"]
    return {
        "level": level, "bars": len(bars), "bis": bis, "zs": zss,
        "fx": s["fx"], "merged": s["merged"],
        "macd": {"dif": round(dif[-1], 4), "dea": round(dea[-1], 4),
                 "hist": round(hist[-1], 4), "hist_prev": round(hist[-2], 4)},
        "last_bar": bars[-1]["datetime"],
    }


def bi_brief(b, tail=5):
    return {"dir": b["dir"], "t0": b["t0"], "p0": round(b["p0"], 3),
            "t1": b["t1"], "p1": round(b["p1"], 3),
            "macd": {"pos": b["macd_pos"], "neg": b["macd_neg"], "abs": b["macd_abs"]},
            "new_extreme": b.get("new_extreme"),
            "confirmed": b["dt_confirm"]}


def zs_brief(z):
    return {k: z[k] for k in ("ZD", "ZG", "GG", "DD", "G", "D", "dir", "seg_count",
                              "extended", "state", "level_up_hint", "t_start", "t_end",
                              "bi_start", "bi_end", "confirmed")} | ({"leave": z["leave"]} if z.get("leave") else {})


# ---------------------------------------------------------------- 买卖点候选
# 只产出"候选"，由 LLM 按 10 个缠论技能复核后才叫"已确认"。状态四态：候选/已确认/已失效/信息不足

def third_point(bis, z):
    """第三类买卖点：中枢破坏点（ch20/61 结构：前三段构成中枢、第四段离开、第五段回试）。

    判据：
      离开段 = 中枢形成三段之后，第一笔终点离开中枢区间的段（向上 p1 > ZG / 向下 p1 < ZD）
      回试段 = 紧随其后的一笔；买点要求其低点 > ZG，卖点要求其高点 < ZD
      回试是否已完成 = 回试段之后是否还有笔（依据次级别背驰或双次回拉，由 LLM 复核）
    返回 {"买": {...}, "卖": {...}}，各自可能缺省。
    """
    ZG, ZD, n = z["ZG"], z["ZD"], len(bis)
    out = {}
    for side, key in (("up", "买"), ("down", "卖")):
        j = None
        for k in range(z["bi_start"] + 3, n):
            b = bis[k]
            if side == "up":
                if b["dir"] == "down" and b["p1"] < ZD:
                    break                       # 先向下离开 -> 不是三买
                if b["dir"] == "up" and b["p1"] > ZG:
                    j = k
                    break
            else:
                if b["dir"] == "up" and b["p1"] > ZG:
                    break                       # 先向上离开 -> 不是三卖
                if b["dir"] == "down" and b["p1"] < ZD:
                    j = k
                    break
        if j is None:
            continue
        leave = bis[j]
        r = j + 1
        if r >= n:
            out[key] = {"state": "信息不足", "leave": leave, "retest": None, "extent": None,
                        "why": ("已有一段向上离开中枢，但向下回试段尚未出现" if side == "up"
                                else "已有一段向下离开中枢，但向上回抽段尚未出现")}
            continue
        retest = bis[r]
        if retest["dir"] != ("down" if side == "up" else "up"):
            continue
        done = r + 1 < n
        if side == "up":
            lo = min(retest["p0"], retest["p1"])
            ok, extent = lo > ZG, lo
            why = (f"中枢[{ZD}, {ZG}]之后第{j+1}段向上离开"
                   f"（{leave['t0']} {round(leave['p0'],3)}→{leave['t1']} {round(leave['p1'],3)}），"
                   f"第{r+1}段向下回试低点 {round(lo,3)} "
                   + ("未跌破 ZG → 中枢破坏成立" if ok else "已跌回中枢区间 → 中枢破坏未成立"))
        else:
            hi = max(retest["p0"], retest["p1"])
            ok, extent = hi < ZD, hi
            why = (f"中枢[{ZD}, {ZG}]之后第{j+1}段向下离开"
                   f"（{leave['t0']} {round(leave['p0'],3)}→{leave['t1']} {round(leave['p1'],3)}），"
                   f"第{r+1}段向上回抽高点 {round(hi,3)} "
                   + ("未升破 ZD → 中枢破坏成立" if ok else "已升回中枢区间 → 中枢破坏未成立"))
        if not ok:
            state = "已失效"
        else:
            state = "已确认" if done else "候选"
        out[key] = {"state": state, "leave": leave, "retest": retest, "extent": extent,
                    "done": done, "why": why}
    return out


def candidates(main, sub, code, state, now):
    out = []
    bis, zss = main["bis"], main["zs"]
    if not zss:
        out.append({"type": "-", "state": "信息不足",
                    "why": f"{main['level']}级别尚未形成「前三笔重叠」的中枢，不具备判三类买卖点的前置条件",
                    "need": "等中枢形成"})
        return out

    z = zss[-1]
    ZD, ZG = z["ZD"], z["ZG"]
    trend = cs.zhongshu_state(zss)

    # ---------- 第三类买卖点：中枢破坏点 ----------
    tp = third_point(bis, z)
    for key, type_name in (("买", "三类买点"), ("卖", "三类卖点")):
        t = tp.get(key)
        if not t:
            continue
        rec = {"type": type_name, "state": t["state"], "why": t["why"],
               "level": main["level"], "ZG": ZG, "ZD": ZD,
               "zs_index_in_order": len(zss),
               "zs_extended": z["extended"], "zs_level_up_hint": z["level_up_hint"]}
        if t["retest"] is not None:
            rec["endpoint"] = t["retest"]["t1"]
            rec["retest_extent"] = round(t["extent"], 3)
            rec["need"] = ("需核验：①中枢已确认 ②次级别走势离开 ③符合要求的回试 ④回试与 ZG/ZD 的关系 "
                           "⑤回试是否已完成（次级别背驰或双次回拉）"
                           + ("" if t["done"] else "。⚠️ 回试段可能仍在走，回试未完成前不得视为已确认"))
        else:
            rec["endpoint"] = t["leave"]["t1"]
            rec["need"] = "等回试/回抽段出现"
        out.append(rec)

    # ---------- 第一类买卖点：趋势背驰点 ----------
    downs = [b for b in bis if b["dir"] == "down"]
    ups = [b for b in bis if b["dir"] == "up"]
    if downs:
        last = downs[-1]
        prev = downs[-2] if len(downs) >= 2 else None
        if prev and last["new_extreme"] and last["macd_abs"] > 0:
            ratio = round(last["macd_abs"] / prev["macd_abs"], 3) if prev["macd_abs"] else None
            if ratio is not None and ratio < 0.85:
                out.append({
                    "type": "一类买点", "state": "候选",
                    "why": (f"{main['level']}最后一段向下创新低（{last['t1']} {last['p1']} < {prev['p1']}），"
                            f"MACD 力度 {last['macd_abs']} 弱于前一同向段 {prev['macd_abs']}（比值 {ratio}）→ 趋势背驰候选"),
                    "need": "需确认：①属趋势还是盘整 ②A/C 同级别 ③B 段是否回拉 0 轴 ④是否处于中阴状态",
                    "level": main["level"], "endpoint": last["t1"],
                    "macd_ratio": ratio, "price_low": round(last["p1"], 3),
                    "trend": trend,
                })
        elif prev is None:
            out.append({"type": "一类买点", "state": "信息不足",
                        "why": "同向段不足两段，无法做背驰比较", "need": "等结构展开"})

    if ups:
        last = ups[-1]
        prev = ups[-2] if len(ups) >= 2 else None
        if prev and last["new_extreme"] and last["macd_abs"] > 0 and prev["macd_abs"]:
            ratio = round(last["macd_abs"] / prev["macd_abs"], 3)
            if ratio < 0.85:
                out.append({
                    "type": "一类卖点", "state": "候选",
                    "why": (f"{main['level']}最后一段向上创新高（{last['t1']} {last['p1']} > {prev['p1']}），"
                            f"MACD 力度 {last['macd_abs']} 弱于前一同向段 {prev['macd_abs']}（比值 {ratio}）→ 顶背驰候选"),
                    "need": "需确认：①属趋势还是盘整 ②A/C 同级别 ③B 段是否回拉 0 轴",
                    "level": main["level"], "endpoint": last["t1"],
                    "macd_ratio": ratio, "price_high": round(last["p1"], 3),
                    "trend": trend,
                })

    # ---------- 第二类买卖点：一买后的次级别回试结束点 ----------
    # 笔严格顶底交替：[..., 一买段(down), 向上笔(up), 当前回试段(down)]
    # 前提闸门：参考段必须本身创新极值（否则不是"一买后的回试"，只是普通折返）
    if (len(bis) >= 3 and bis[-1]["dir"] == "down" and bis[-2]["dir"] == "up"
            and bis[-3]["dir"] == "down" and bis[-3].get("new_extreme")):
        retest, prior, first_low = bis[-1], bis[-2], bis[-3]
        low_ref = first_low["p1"]
        not_new_low = retest["p1"] > low_ref
        out.append({
            "type": "二类买点", "state": "候选",
            "variant": ("一般不创新低" if not_new_low else "最弱形态：跌破一买（ch101，此处一般构成盘整背驰）"),
            "why": (f"一买段（{first_low['t1']} {round(low_ref,3)}，创出前一同向段新低）后的次级别回抽"
                    f"已走出向上段（{prior['t1']} {round(prior['p1'],3)}），"
                    f"当前向下回试段低点 {round(retest['p1'],3)} "
                    + ("未跌破一买低点 → 满足二买条件①，还需核②不创新低/盘整背驰" if not_new_low
                       else "已跌破一买低点 → ch101「最弱形态」，此处一般构成盘整背驰")),
            "need": ("需确认：①一买本身是否成立（趋势背驰）②回试是否结束（5m 底分型/双次回拉）"
                     "③二买落在中枢的上/中/下决定力度"),
            "level": main["level"], "endpoint": retest["t1"],
            "retest_low": round(retest["p1"], 3), "low_ref": round(low_ref, 3),
            "zs_position": ("中枢之下（力度可疑，易出现扩张性中枢）" if retest["p1"] < ZD else
                            "中枢之中（扩张与新生机会对半）" if retest["p1"] <= ZG else
                            "中枢之上（新生机会很大）"),
        })
    if (len(bis) >= 3 and bis[-1]["dir"] == "up" and bis[-2]["dir"] == "down"
            and bis[-3]["dir"] == "up" and bis[-3].get("new_extreme")):
        retest, prior, first_high = bis[-1], bis[-2], bis[-3]
        high_ref = first_high["p1"]
        not_new_high = retest["p1"] < high_ref
        out.append({
            "type": "二类卖点", "state": "候选",
            "variant": ("一般不创新高" if not_new_high else "最弱形态：突破一卖"),
            "why": (f"一卖段（{first_high['t1']} {round(high_ref,3)}，创出前一同向段新高）后的次级别回抽"
                    f"已走出向下段（{prior['t1']} {round(prior['p1'],3)}），"
                    f"当前向上回抽段高点 {round(retest['p1'],3)} "
                    + ("未创新高 → 满足二卖条件①" if not_new_high
                       else "已创新高 → 需看是否构成盘整背驰")),
            "need": "需确认：①一卖本身是否成立（趋势背驰）②回抽是否结束（5m 顶分型/双次回拉）",
            "level": main["level"], "endpoint": retest["t1"],
            "retest_high": round(retest["p1"], 3), "high_ref": round(high_ref, 3),
            "zs_position": ("中枢之下" if retest["p1"] < ZD else
                            "中枢之中" if retest["p1"] <= ZG else "中枢之上"),
        })

    return out


# ---------------------------------------------------------------- 单只处理

def handle(code, conn, now):
    """对单只标的取数 + 结构 + 候选。"""
    lv = CONFIG["levels"]
    cnt = CONFIG["bars_count"]
    q = conn.quote(code)
    if not q or not q.get("price"):
        return {"code": code, "error": "行情取不到"}
    data = {}
    for tag, period in (("main", lv["main"]), ("sub", lv["sub"]), ("micro", lv["micro"])):
        try:
            data[tag] = conn.bars(code, period, cnt.get(period, 240))
        except Exception as e:
            data[tag] = []
            log(f"  ! {code} {period} bars 失败: {e}")
    try:
        data["day"] = conn.bars(code, lv.get("trend", "day"), cnt.get("day", 250))
    except Exception:
        data["day"] = []

    def trim(bars, level):
        """丢掉当前未收盘的那根 period K 线（结构只用已收盘K线）。"""
        if not bars:
            return bars, None
        step = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}.get(level)
        last = bars[-1]["datetime"]
        try:
            t = dt.datetime.strptime(last, "%Y-%m-%d %H:%M")
        except Exception:
            return bars, None
        pending = None
        if step:
            end = t + dt.timedelta(minutes=step)
            if now < end:
                pending = bars[-1]
                bars = bars[:-1]
        elif t.date() == now.date() and (now.hour * 100 + now.minute) < 1500:
            pending = bars[-1]
            bars = bars[:-1]
        return bars, pending

    main_bars, pend = trim(data["main"], lv["main"])
    sub_bars, _ = trim(data["sub"], lv["sub"])
    micro_bars, _ = trim(data["micro"], lv["micro"])

    main = structure(main_bars, lv["main"])
    sub = structure(sub_bars, lv["sub"])
    micro = structure(micro_bars, lv["micro"]) if CONFIG.get("compute_micro", True) else None
    if not main or main.get("insufficient"):
        return {"code": code, "error": f"{lv['main']} 结构不足（K线 {len(main_bars) if main_bars else 0} 根）"}

    rec = {
        "code": code,
        "is_index": is_index_code(code),
        "price": q["price"], "chg_pct": q.get("change_pct"),
        "high": q.get("high"), "low": q.get("low"), "vol": q.get("vol"),
        "quote_bid1": (q.get("bid") or [None])[0], "quote_ask1": (q.get("ask") or [None])[0],
        "main": {
            "level": lv["main"], "last_bar": main["last_bar"],
            "pending_bar": (pend or {}).get("datetime"),
            "trend": cs.zhongshu_state(main["zs"]),
            "macd": main["macd"],
            "zs_last": zs_brief(main["zs"][-1]) if main["zs"] else None,
            "zs_count": len(main["zs"]),
            # 完整中枢序列 + 完整笔序列：只在 full_json 落盘里给，compact() 不取，
            # 所以不增加 stdout/prompt 成本。判「趋势 vs 盘整」必须看全序列的 ZG/ZD 是否重叠，
            # 只给 zs_last 会逼着复核方每次写探针脚本重跑 cs.analyze（踩过）。
            "zs_all": [zs_brief(z) for z in main["zs"]],
            "bi_all": [bi_brief(b) for b in main["bis"]],
            "bi_tail": [bi_brief(b) for b in main["bis"][-6:]],
            "fx_tail": [{"type": f["type"], "price": round(f["price"], 3),
                         "t": f["dt"], "confirmed": f["dt_confirm"]} for f in main["fx"][-4:]],
        },
        "sub": {
            "level": lv["sub"],
            "bi_tail": [bi_brief(b) for b in sub["bis"][-4:]] if sub and sub.get("bis") else [],
            "fx_tail": [{"type": f["type"], "price": round(f["price"], 3), "t": f["dt"]}
                        for f in sub["fx"][-3:]] if sub else [],
            "macd": sub["macd"] if sub else None,
        } if sub else None,
        "micro": {
            "level": lv["micro"], "last_bar": micro.get("last_bar") if micro else None,
            "bi_tail": [{"dir": b["dir"], "p0": round(b["p0"], 3), "p1": round(b["p1"], 3),
                         "t1": b["t1"], "new_extreme": b.get("new_extreme")}
                        for b in micro["bis"][-3:]] if micro and micro.get("bis") else [],
            "macd": micro["macd"] if micro else None,
        } if micro else None,
    }
    rec["candidates"] = candidates(main, sub, code, None, now)
    if any(c["type"].startswith(("一类", "二类")) for c in rec["candidates"]):
        rec["zhongyin_check"] = ("ch101：只有回升的中阴状态下才有一、二类买卖点；中阴状态结束后，"
                                 "所有的中枢震荡只存在第三类买卖点以及中枢震荡的买卖点 —— "
                                 "判一/二类买卖点前必须先确认当下是否处于中阴")
    # 时效性提示：最后一笔的确认时间距今多久（避免拿隔夜结构当盘中信号）
    last_bi = main["bis"][-1]
    try:
        lag = now - dt.datetime.strptime(last_bi["dt_confirm"], "%Y-%m-%d %H:%M")
        rec["structure_lag_min"] = int(lag.total_seconds() // 60)
    except Exception:
        pass
    return rec


# ---------------------------------------------------------------- 交易日

def is_trading_day(conn, now):
    """用上证指数日线最后一根的日期判断今天是否交易日（无节假日日历时的可靠代理）。"""
    if now.weekday() >= 5:
        return False, "周末"
    try:
        b = conn.bars("999999", "day", 5, market=1)
        if b:
            last = b[-1]["datetime"][:10]
            if last < now.strftime("%Y-%m-%d"):
                return False, f"非交易日（上证日线最后更新 {last}）"
            return True, ""
    except Exception as e:
        log(f"  ! 交易日判断失败: {e}")
    return True, ""


# ---------------------------------------------------------------- 主流程

def compact(r):
    """注入 cron prompt 用的精简版：只留判定必需的字段，完整版在 out/ 的 JSON 里。

    有候选的才带结构尾巴（4 笔主级别 + 5m 尾部 + MACD），没候选的只留中枢与趋势，
    这样 4 只票的 stdout 从 ~18KB 压到 ~6KB。
    """
    if "error" in r:
        return {"code": r["code"], "error": r["error"]}
    m = r["main"]
    cands = r.get("candidates", [])
    hot = any(c.get("state") in ("候选", "已确认") or c.get("type", "-").startswith(("一类", "二类", "三类"))
              for c in cands)
    out = {
        "code": r["code"], "price": r["price"], "chg_pct": r.get("chg_pct"),
        "main": {
            "level": m["level"], "last_bar": m["last_bar"], "pending_bar": m.get("pending_bar"),
            "trend": m["trend"], "macd": m["macd"], "zs_count": m["zs_count"],
            "zs_last": m["zs_last"],
        },
        "candidates": cands,
    }
    if r.get("zhongyin_check"):
        out["zhongyin_check"] = r["zhongyin_check"]
    if r.get("structure_lag_min") is not None:
        out["structure_lag_min"] = r["structure_lag_min"]
    if hot:
        out["main"]["bi_tail"] = m["bi_tail"][-4:]
        out["sub"] = r.get("sub")
    return out


def safe_handle(code, conn, now):
    """单个标的失败不能整只凭空消失——异常必须变成可见的 error 记录 + stderr traceback。

    踩过的坑：TdxPool.batch 的 work() 捕获异常后返回 None，而 main 里 `if not r: continue`
    会把这只票悄悄跳过，结果是"4 只自选股只出了 1 只"且没有任何报错。
    """
    import traceback
    try:
        return handle(code, conn, now)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


def wake_gate_needed(result):
    """非交易时段 / 无行情 / 没有「该推的信号」→ 叫 cron 跳过整个 agent 运行。

    cron 的 wake gate 只看脚本 stdout 的**最后一行**：为 ``{"wakeAgent": false}`` 时
    scheduler 直接跳过本次运行（不唤醒模型、不投递、Bot Chat 不增长、零 token）。

    「该推」= state ∈ {候选, 已确认} 且（is_new 或 is_update）。只剩一个挂着的老候选、
    既不是新信号、也没到复推时机时，唤醒模型只是白跑一轮（实测每轮约 3.7 万 token）。
    """
    if not result.get("trading"):
        return True
    stocks = result.get("stocks") or []
    if not stocks:
        return True
    for s in stocks:
        if s.get("error"):
            continue
        for c in s.get("candidates", []):
            if c.get("state") not in ("候选", "已确认"):
                continue
            if c.get("is_new") or c.get("is_update"):
                return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["intraday", "close"], default="intraday")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--wake-gate", action="store_true",
                    help="无信号的轮次在 stdout 末行追加 {\"wakeAgent\": false}，让 cron 跳过 agent")
    a = ap.parse_args()

    def emit(obj):
        """打印结果；开启 --wake-gate 且本轮无信号时，末行追加 cron 的 wake gate。"""
        print(json.dumps(obj, ensure_ascii=False))
        if a.wake_gate and wake_gate_needed(obj):
            print('{"wakeAgent": false}')

    now = dt.datetime.now()
    codes = [c for c in a.codes.replace(",", " ").split() if c] or read_watchlist()
    codes = [c for c in codes if c not in CONFIG.get("exclude_codes", [])]

    result = {"mode": a.mode, "time": now.strftime("%Y-%m-%d %H:%M:%S"),
              "trading": False, "reason": "", "stocks": []}

    if not a.force:
        if a.mode == "intraday" and not is_trading_time(now):
            result["reason"] = "非交易时段（连续竞价 09:30-11:30 / 13:00-15:00）"
            emit(result)
            return
        # 收盘全览只在 15:00-16:30 之间有意义
        if a.mode == "close" and not (1500 <= now.hour * 100 + now.minute <= 1630):
            result["reason"] = "非收盘全览时段"
            emit(result)
            return

    if not codes:
        result["reason"] = "自选股为空"
        emit(result)
        return

    live = TdxLive().connect()
    ok, why = is_trading_day(live, now)
    if not ok and not a.force:
        result["reason"] = why
        live.close()
        emit(result)
        return
    live.close()

    result["trading"] = True
    state = load_state()
    with TdxPool(n=CONFIG.get("conn", 6), servers=None) as pool:
        rows = pool.batch(codes, lambda c, code: safe_handle(code, c, now))

    for r in rows:
        if not r:
            continue
        if "error" in r:
            log(f"  ! {r['code']} 采集失败: {r['error']}")
        # 去重 + 进展复推标记（is_new / is_update；只在真该推的轮次刷新推送基线）
        mark_progress(r, now, state)
        result["stocks"].append(r)

    save_state(state)
    stamp = now.strftime("%Y%m%d_%H%M")
    full = os.path.join(OUT_DIR, f"prep_{a.mode}_{stamp}.json")
    json.dump(result, open(full, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    slim = dict(result)
    slim["stocks"] = [compact(r) for r in result["stocks"]]
    slim["full_json"] = full
    slim["_note"] = ("本输出为精简版（判定必需字段）。需要分型/完整笔序列/1分钟结构时读 full_json。"
                     "candidates 里 is_new=false 表示该信号上一轮已推送过，不要重复推。")
    emit(slim)


if __name__ == "__main__":
    main()
