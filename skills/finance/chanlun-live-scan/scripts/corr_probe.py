"""多票相关性与结构同步探针（缠论盯盘辅助）。

用途：用户给出「某票是强相关股，可以互相验证」时，先用数据把这句口头判断量化，再看结构同步度。

用法（必须用 tdx-live 的 venv，且路径写成原生 Windows 风格）：
    E:/Hermes/tdx-live/.venv/Scripts/python.exe \
        "<HERMES_HOME>/profiles/chanlun/skills/finance/chanlun-live-scan/scripts/corr_probe.py" \
        001270 688270

可选参数：
    --ref 999999     对照指数（默认 999999）
    --prep <path>    指定 prep JSON；默认取 E:/Hermes/chanlun-agent/out/ 里文件名时间戳最新的那份

输出：
    [1] 票间日收益相关系数（近 20/40/60/全样本）
    [2] 各自 vs 参照指数（远低于票间相关才说明是真配对，而不是「都跟着大盘走」）
    [3] 30m 收益相关系数
    [4] 30m 笔端点时间对齐（差几根 30m）—— 结构同步度，比相关系数更能证明「同一驱动」
    [5] 最近 5 笔的力度（pos/neg/abs；abs 是净面积，判力度按方向取分量）

前置：跑过 `bash run_scan.sh intraday --force`，让 out/ 里有一份含 bi_all 的 prep JSON。
"""
import sys
import os
import json
import glob
import math
from datetime import datetime

sys.path[:0] = ['E:/Hermes/tdx-live', 'E:/Hermes/chanlun-agent']

PROJ = 'E:/Hermes/chanlun-agent'
OUT = os.path.join(PROJ, 'out')


def latest_prep():
    """按文件名里的 YYYYMMDD_HHMM 取最新，不能用字典序（prep_close_* 会排在 prep_intraday_* 前面）。"""
    fs = glob.glob(os.path.join(OUT, 'prep_*.json'))
    if not fs:
        return None

    def stamp(f):
        parts = os.path.basename(f).split('_')
        return parts[-2] + parts[-1].split('.')[0]

    return max(fs, key=stamp)


def load_bars(conn, code, period, n):
    """不要传 market：个股/ETF 默认 infer_market(code) 才对，传错市场号会静默返回 0 根。
    指数（999xxx/399xxx/88xxxx）也交给 infer_market，不要手写 market=0。
    返回字段：datetime / open / high / low / close / vol / amount（没有 t/c/h/l 简写）。"""
    try:
        return conn.bars(code, period, n)
    except Exception as e:  # 单只失败不影响整张表
        print('  ! %s %s 取数失败：%s' % (code, period, e))
        return []


def rets(bars):
    out = []
    for i in range(1, len(bars)):
        p0, p1 = bars[i - 1]['close'], bars[i]['close']
        if p0 and p1:
            out.append((bars[i]['datetime'][:16], (p1 / p0 - 1) * 100))
    return out


def corr(x, y):
    n = len(x)
    if n < 3:
        return float('nan')
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return float('nan')
    return sxy / math.sqrt(sxx * syy)


def corr_table(ra, rb, label):
    da, db = dict(ra), dict(rb)
    keys = sorted(set(da) & set(db))
    xa = [da[k] for k in keys]
    xb = [db[k] for k in keys]
    line = '  %-26s 共同样本 %3d' % (label, len(xa))
    for n in (20, 40, 60):
        if len(xa) >= n:
            line += '   近%2d % .3f' % (n, corr(xa[-n:], xb[-n:]))
    if len(xa) >= 3:
        line += '   全样本 % .3f' % corr(xa, xb)
    print(line)


def parse_args(argv):
    ref, prep, codes = '999999', None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--ref' and i + 1 < len(argv):
            i += 1
            ref = argv[i]
        elif a == '--prep' and i + 1 < len(argv):
            i += 1
            prep = argv[i]
        else:
            codes.append(a)
        i += 1
    return codes, ref, prep


def main(argv):
    codes, ref, prep = parse_args(argv)
    if len(codes) < 2:
        print('用法: corr_probe.py 代码A 代码B [代码C ...] [--ref 999999] [--prep out/prep_xxx.json]')
        return 2

    from tdxlive import TdxLive
    conn = TdxLive().connect()

    day = {}
    print('[0] 数据概览')
    for c in codes + [ref]:
        day[c] = load_bars(conn, c, 'day', 200)
        b = day[c]
        print('  %-8s 日线 %3d 根  %s -> %s  收 %s' % (
            c, len(b),
            b[0]['datetime'][:10] if b else '-',
            b[-1]['datetime'][:10] if b else '-',
            b[-1]['close'] if b else '-'))

    print('\n[1] 票间日收益相关系数')
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            corr_table(rets(day[codes[i]]), rets(day[codes[j]]), '%s vs %s' % (codes[i], codes[j]))

    print('\n[2] 各自 vs 参照 %s（远低于票间相关才说明是真配对）' % ref)
    for c in codes:
        corr_table(rets(day[c]), rets(day[ref]), '%s vs %s' % (c, ref))

    print('\n[3] 30m 收益相关系数')
    m30 = {c: load_bars(conn, c, '30m', 320) for c in codes}
    for c in codes:
        print('  %-8s 30m %3d 根' % (c, len(m30[c])))
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            corr_table(rets(m30[codes[i]]), rets(m30[codes[j]]), '%s vs %s' % (codes[i], codes[j]))

    pf = prep or latest_prep()
    if not pf or not os.path.exists(pf):
        print('\n[4][5] 跳过：找不到 prep JSON，先跑 bash run_scan.sh intraday --force')
        return 0

    data = json.load(open(pf, encoding='utf-8'))
    by = {s.get('code'): s for s in data.get('stocks', [])}

    print('\n[4] 30m 笔端点时间对齐（%s）' % os.path.basename(pf))
    eps = {}
    for c in codes:
        s = by.get(c) or {}
        main_d = s.get('main') or {}
        pts = set()
        for b in main_d.get('bi_all') or []:
            pts.add((b['t0'], b['p0']))
            pts.add((b['t1'], b['p1']))
        if pts:
            eps[c] = sorted(pts)
        else:
            print('  %s：prep 里没有 main.bi_all（该票取数失败或未在自选股）' % c)

    def dt(s):
        return datetime.strptime(s, '%Y-%m-%d %H:%M')

    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            a, b = eps.get(codes[i]), eps.get(codes[j])
            if not a or not b:
                continue
            print('  -- %s vs %s（各取近 10 个端点）--' % (codes[i], codes[j]))
            for t, p in a[-10:]:
                near = min(b, key=lambda y: abs((dt(y[0]) - dt(t)).total_seconds()))
                gap = int((dt(near[0]) - dt(t)).total_seconds() / 1800)
                print('     %s %10.2f  <->  %s %10.2f   差 %+d 根30m' % (t, p, near[0], near[1], gap))

    print('\n[5] 最近 5 笔力度（abs 是净面积=|pos+neg|；判力度：上涨笔看 pos，下跌笔看 |neg|）')
    for c in codes:
        s = by.get(c) or {}
        main_d = s.get('main') or {}
        if not main_d.get('bi_all'):
            continue
        print('  %s' % c)
        for b in main_d['bi_all'][-5:]:
            m = b.get('macd') or {}
            print('     %-4s %s %8.2f -> %s %8.2f   pos %8.4f  neg %8.4f  abs %8.4f' % (
                b['dir'], b['t0'], b['p0'], b['t1'], b['p1'],
                m.get('pos', 0.0), m.get('neg', 0.0), m.get('abs', 0.0)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
