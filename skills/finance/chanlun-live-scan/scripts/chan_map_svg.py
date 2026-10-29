#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论结构示意图生成器（静态 Python -> 内联 SVG -> 自包含 HTML，无 JS 依赖）。

    fetch  : 取 30m/5m/1m 结构 + MACD 力度，落盘 map_data.json（一次取全，改图不再连 tdx）
    render : map_data.json + spec.json -> chan_map.html（::preview 直接显示）

必须用 tdx-live 的 venv（原生 Windows python）：
    E:/Hermes/tdx-live/.venv/Scripts/python.exe chan_map_svg.py fetch --codes 688270 001270 --index 880548 --out out/map_data.json
    E:/Hermes/tdx-live/.venv/Scripts/python.exe chan_map_svg.py render --data out/map_data.json --spec out/map_spec.json --out out/chan_map.html

spec.json 只描述“标什么”，画法固定在本文件里：
{
  "title": "缠论结构图 2026-10-23 收盘",
  "subtitle": "30m 为观察级别，5m 用于区间套与回试确认",
  "panels": [
    {"code": "688270", "level": "30m", "n": 240, "h": 320, "active_zs": 5, "bg_zs": 4,
     "A": 20, "C": 26, "read": "<b>背驰</b> A 11.04 -> C 7.90（0.72x）……",
     "marks": [["2026-10-23 11:00", 43.92, "cand", "一买候选 43.92"],
               ["2026-10-21 10:00", 50.58, "dead", "三买 已失效"]]}
  ],
  "table_head": ["标的", "一买", "二买", "三买", "三卖"],
  "table_rows": [["<b>688270</b><br><small>30m</small>", "★候选 43.92", "候选", "已失效", "不成立"]]
}

marks: [时间, 价格, 类型(cand/dead/note/buy/sell), 标签, 可选 "left" 表示标签放左侧]
level: "30m" | "5m" | "1m"；is_index 的票不出可执行买卖点，只做背景面板。
"""
import argparse
import json
import sys

W = 1000
RED, GRN = "#e2493f", "#0e9a63"
BLU, PUR, GOLD, GRY, ORG = "#2f5fa8", "#6d86c8", "#c8901a", "#8a8f98", "#e8710a"

TDX_DIR = r"E:\Hermes\tdx-live"
PROJ_DIR = r"E:\Hermes\chanlun-agent"
LEVELS = (("30m", 400), ("5m", 480), ("1m", 480))


# ------------------------------------------------------------------ fetch
def cmd_fetch(a):
    sys.path[:0] = [TDX_DIR, PROJ_DIR]
    import chan_structure as cs
    from tdxlive import TdxLive

    conn = TdxLive().connect()
    res = {"stocks": {}}
    targets = [(c, False) for c in a.codes] + [(c, True) for c in a.index]
    for code, is_idx in targets:
        q = conn.quote(code) or {}
        item = {"code": code, "is_index": is_idx,
                "price": q.get("price"), "chg": q.get("change_pct"), "levels": {}}
        for period, cnt in LEVELS:
            bars = conn.bars(code, period, cnt)
            if not bars:
                item["levels"][period] = None
                continue
            st = cs.analyze(bars, min_gap=4)
            dif, dea, hist = cs.macd([b["close"] for b in bars])
            pos_by_t = {b["datetime"]: i for i, b in enumerate(bars)}
            bis = []
            for i, b in enumerate(st["bi"]):
                pos, neg, ab, net = cs.bi_macd_area(b, hist, dif)
                bis.append({"i": i, "dir": b["dir"], "t0": b["t0"], "p0": b["p0"],
                            "t1": b["t1"], "p1": b["p1"], "cf": b["dt_confirm"],
                            "macd": {"pos": pos, "neg": neg, "abs": ab, "net": net}})
            zss = []
            for z in st["zs"]:
                item_z = {k: z.get(k) for k in (
                    "ZD", "ZG", "DD", "GG", "G", "D", "dir", "seg_count", "extended",
                    "state", "level_up_hint", "t_start", "t_end", "bi_start", "bi_end", "confirmed", "leave")}
                item_z["i0"] = pos_by_t.get(z["t_start"])
                item_z["i1"] = pos_by_t.get(z["t_end"])
                zss.append(item_z)
            item["levels"][period] = {
                "bars": [{"t": b["datetime"], "o": b["open"], "h": b["high"],
                          "l": b["low"], "c": b["close"], "v": b.get("volume")} for b in bars],
                "bi": bis, "zs": zss,
                "trend": cs.zhongshu_state(st["zs"]),
                "last_macd": {"dif": round(dif[-1], 4), "dea": round(dea[-1], 4), "hist": round(hist[-1], 4)},
                "bi_count": len(bis), "fx_count": len(st["fx"]), "last_bar": bars[-1]["datetime"],
            }
        res["stocks"][code] = item
    conn.close()
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    for code, it in res["stocks"].items():
        for p, lv in it["levels"].items():
            if lv:
                print(code, p, len(lv["bars"]), "bi", lv["bi_count"], "zs", len(lv["zs"]), lv["last_bar"])
    print("written", a.out)


# ------------------------------------------------------------------ render
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Panel:
    """一个级别的 K 线面板：坐标、图元、图元层序。"""

    def __init__(self, lv, start, height, y_pad=0.06):
        self.bars = lv["bars"]
        self.s = max(0, start)
        win = self.bars[self.s:]
        lo = min(b["l"] for b in win)
        hi = max(b["h"] for b in win)
        pad = (hi - lo) * y_pad or 1.0
        self.mn, self.mx = lo - pad, hi + pad
        self.H = height
        self.PL, self.PR, self.PT, self.PB = 6, 62, 14, 22
        self.step = (W - self.PL - self.PR) / max(1, len(win))
        self.parts = []
        self._idx = {b["t"]: i for i, b in enumerate(self.bars)}

    def x(self, i):
        return self.PL + (i - self.s + 0.5) * self.step

    def y(self, p):
        p = min(max(p, self.mn), self.mx)
        return self.PT + (self.mx - p) / (self.mx - self.mn) * (self.H - self.PT - self.PB)

    def idx(self, t):
        return self._idx.get(t)

    def line(self, x1, y1, x2, y2, stroke, w=1, dash=None, op=None):
        a = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op else ""
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                          f'stroke="{stroke}" stroke-width="{w}"{a}{o}/>')

    def rect(self, x, y, w, h, fill="none", stroke="none", sw=1, dash=None, op=None):
        a = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op else ""
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0.5):.1f}" height="{max(h, 0.5):.1f}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{a}{o} rx="1"/>')

    def text(self, x, y, txt, fill, size=10, anchor="start", weight="400"):
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
                          f'text-anchor="{anchor}" font-weight="{weight}" '
                          f'style="paint-order:stroke;stroke:var(--card);stroke-width:2.6px;stroke-linejoin:round">'
                          f'{esc(txt)}</text>')

    # ---- 图元
    def grid(self):
        for k in range(4):            # 4 档刻度，5 档必挤
            p = self.mn + (self.mx - self.mn) * k / 3
            yy = self.y(p)
            self.line(self.PL, yy, W - self.PR, yy, "var(--border)", 1, "3 4")
            self.text(W - self.PR + 4, yy + 3.4, f"{p:.2f}", "var(--muted-foreground)", 9.5)

    def zhongshus(self, lv, active=None, bg=None):
        for zi, z in enumerate(lv["zs"]):
            i0, i1 = self.idx(z["t_start"]), self.idx(z["t_end"])
            if i0 is None or i1 is None:
                continue
            x0, x1 = self.x(i0), self.x(i1)
            if z.get("GG") is not None:
                self.rect(x0, self.y(z["GG"]), x1 - x0, self.y(z["DD"]) - self.y(z["GG"]),
                          "none", "var(--muted-foreground)", 0.9, "4 3", .7)
            fill = "rgba(120,140,210,.24)" if zi == active else "rgba(120,140,210,.13)"
            self.rect(x0, self.y(z["ZG"]), x1 - x0, self.y(z["ZD"]) - self.y(z["ZG"]),
                      fill, PUR if zi != active else BLU, 1.5 if zi == active else 0.9)
            tag = "B 中枢" if zi == bg else ("中枢" if zi == active else "")
            if tag and (x1 - x0) > 46:      # 标签放框外上方，箱太窄不标
                top = min(self.y(z.get("GG") or z["ZG"]), self.y(z["ZG"]))
                self.text(x0 + 3, max(self.PT + 9, top - 3),
                          f'{tag} [{z["ZD"]:.2f}, {z["ZG"]:.2f}]',
                          BLU if zi in (active, bg) else PUR, 9.5, weight="600")

    def candles(self):
        bw = max(1.0, self.step * 0.6)
        for i in range(self.s, len(self.bars)):
            b = self.bars[i]
            up = b["c"] >= b["o"]
            col = RED if up else GRN
            cx = self.x(i)
            self.line(cx, self.y(b["h"]), cx, self.y(b["l"]), col, 1)
            y0, y1 = self.y(max(b["o"], b["c"])), self.y(min(b["o"], b["c"]))
            self.rect(cx - bw / 2, y0, bw, y1 - y0, "none" if up else col, col, 1)

    def bis(self, lv):
        for b in lv["bi"]:
            i0, i1 = self.idx(b["t0"]), self.idx(b["t1"])
            if i0 is None or i1 is None:
                continue
            self.line(self.x(i0), self.y(b["p0"]), self.x(i1), self.y(b["p1"]),
                      "rgba(128,128,128,.85)", 1.2)

    def seg(self, lv, bi_index, color, label, lw=3, dy=-9):
        b = lv["bi"][bi_index]
        i0, i1 = self.idx(b["t0"]), self.idx(b["t1"])
        if i0 is None or i1 is None:
            return
        x0, y0, x1, y1 = self.x(i0), self.y(b["p0"]), self.x(i1), self.y(b["p1"])
        self.line(x0, y0, x1, y1, color, lw)
        self.rect(x1 - 1.5, y1 - 1.5, 3, 3, color, color, 1)
        self.text((x0 + x1) / 2 + 4, (y0 + y1) / 2 + dy, label, color, 10, weight="700")

    def level(self, p, label, color, dash="7 4"):
        yy = self.y(p)
        self.line(self.PL, yy, W - self.PR, yy, color, 1, dash, .75)
        self.text(W - self.PR - 3, yy - 3, label, color, 9.5, anchor="end", weight="600")

    def mark(self, t, p, kind, label, place="right"):
        i = self.idx(t)
        if i is None:
            return
        cx, cy = self.x(i), self.y(p)
        col, glyph, gy, ty = {
            "cand": (GOLD, "★", -13, -8), "dead": (GRY, "✕", 15, 14),
            "note": (BLU, "○", -10, -8), "buy": (GRN, "▲", 16, 16),
            "sell": (ORG, "▼", -12, -10),
        }.get(kind, (GRY, "·", 0, 12))
        self.text(cx, cy + gy, glyph, col, 13, anchor="middle", weight="700")
        if place == "left":
            self.text(cx - 7, cy + ty, label, col, 10, anchor="end", weight="600")
        else:
            self.text(cx + 7, cy + ty, label, col, 10, weight="600")

    def axis(self):
        every = max(1, int((len(self.bars) - self.s) / 8))
        for i in range(self.s, len(self.bars), every):
            self.text(self.x(i), self.H - 6, self.bars[i]["t"][5:16], "var(--muted-foreground)", 9, anchor="middle")

    def curr(self, price):
        yy = self.y(price)
        self.line(self.PL, yy, W - self.PR, yy, GOLD, 1.2, "2 3")
        self.text(self.PL + 3, yy + 12, f"收 {price}", GOLD, 10, weight="700")

    def svg(self):
        return (f'<svg width="{W}" height="{self.H}" viewBox="0 0 {W} {self.H}" '
                'font-family="ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif">'
                + "".join(self.parts) + "</svg>")


def panel_html(it, spec, d):
    level = spec.get("level", "30m")
    lv = it["levels"][level]
    p = Panel(lv, len(lv["bars"]) - spec.get("n", 240), spec.get("h", 320))
    p.grid()
    p.zhongshus(lv, active=spec.get("active_zs"), bg=spec.get("bg_zs"))   # 框先画，K 线后画
    p.candles()
    p.bis(lv)
    for key, color, dflt_dy in (("A", BLU, -9), ("C", ORG, 14)):
        if spec.get(key) is None:
            continue
        b = lv["bi"][spec[key]]
        comp = "neg" if b["dir"] == "down" else "pos"
        val = abs(b["macd"][comp])
        assert val > 0, f'{it["code"]} {level} {key} 段力度为 0，检查索引是否沿用旧批次'
        p.seg(lv, spec[key], color, f'{key} {b["p0"]:.2f}->{b["p1"]:.2f} |{comp}| {val:.2f}', dy=dflt_dy)
    if spec.get("active_zs") is not None:
        z = lv["zs"][spec["active_zs"]]
        p.level(z["ZG"], f'ZG {z["ZG"]:.2f}', PUR)
        p.level(z["ZD"], f'ZD {z["ZD"]:.2f}', PUR)
    for m in spec.get("marks", []):
        t, price, kind, label = m[:4]
        p.mark(t, price, kind, label, place=(m[4] if len(m) > 4 else "right"))
    p.curr(it["price"])
    p.axis()
    tag = "（只作大盘背景，不出可执行买卖点）" if it["is_index"] else ""
    head = (f'<div class="pt"><b>{it["code"]}</b> {level}{tag} · 收 {it["price"]}'
            f'（{it["chg"]:+.2f}%）</div>')
    read = f'<div class="rd">{spec["read"]}</div>' if spec.get("read") else ""
    return f'<div class="panel">{head}{read}{p.svg()}</div>'


CSS = """
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--foreground);background:transparent}
.wrap{display:flex;flex-direction:column;gap:9px;width:1022px}
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.ttl{font-size:16px;font-weight:700}.sub{font-size:11.5px;color:var(--muted-foreground)}
.panel{border:1px solid var(--border);border-radius:9px;padding:7px 10px 4px}
.pt{font-size:12.5px;font-weight:600}.pt small{font-weight:400;color:var(--muted-foreground);font-size:11px}
.rd{font-size:11px;color:var(--muted-foreground);line-height:1.55;padding:2px 0 4px}.rd b{color:var(--foreground)}
.lg{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted-foreground);padding:2px 0 4px}
.sw{display:inline-block;width:14px;height:8px;border-radius:2px;margin-right:4px;vertical-align:middle}
table{border-collapse:collapse;font-size:12px;width:100%;line-height:1.45}
th,td{border:1px solid var(--border);padding:6px 9px;vertical-align:top;text-align:left}
th{color:var(--muted-foreground);font-weight:500}
td small{color:var(--muted-foreground)}.cand{color:#c8901a;font-weight:700}.dead{color:var(--muted-foreground);text-decoration:line-through}.none{color:var(--muted-foreground)}
"""

LEGEND = ('<div class="panel"><div class="lg">'
          '<span><i class="sw" style="background:rgba(120,140,210,.24);border:1px solid #6d86c8"></i>中枢核心区 [ZD,ZG]</span>'
          '<span><i class="sw" style="border:1px dashed var(--muted-foreground)"></i>中枢波动区 [DD,GG]</span>'
          '<span style="color:#2f5fa8"><b>A</b> 背驰比较的进入段</span>'
          '<span style="color:#e8710a"><b>C</b> 背驰比较的离开段（应弱于 A）</span>'
          '<span style="color:#c8901a">★ 候选买卖点</span>'
          '<span style="color:#8a8f98">✕ 已失效</span>'
          '<span style="color:#2f5fa8">○ 关键观察位</span>'
          '</div></div>')


def cmd_render(a):
    d = json.load(open(a.data, encoding="utf-8"))
    spec = json.load(open(a.spec, encoding="utf-8"))
    parts = [panel_html(d["stocks"][s["code"]], s, d) for s in spec["panels"]]
    rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in spec.get("table_rows", []))
    head = "".join(f"<th>{h}</th>" for h in spec.get("table_head", []))
    table = f'<div class="panel"><div class="pt"><b>状态表</b></div><table><tr>{head}</tr>{rows}</table></div>' if rows else ""
    html = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{esc(spec["title"])}</title>'
            f'<style>{CSS}</style></head><body><div class="wrap">'
            f'<div class="top"><span class="ttl">{esc(spec["title"])}</span>'
            f'<span class="sub">{esc(spec.get("subtitle", ""))}</span></div>'
            + LEGEND + "".join(parts) + table +
            '<div class="sub">结构判断，非投资建议；不含 T+1、滑点、涨跌停与流动性约束。</div>'
            '</div></body></html>')
    open(a.out, "w", encoding="utf-8").write(html)
    print("written", a.out, len(html), "bytes")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--codes", nargs="+", default=[])
    f.add_argument("--index", nargs="*", default=[])
    f.add_argument("--out", required=True)
    f.set_defaults(fn=cmd_fetch)
    r = sub.add_parser("render")
    r.add_argument("--data", required=True)
    r.add_argument("--spec", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(fn=cmd_render)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
