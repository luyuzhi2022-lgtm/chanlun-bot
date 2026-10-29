# -*- coding: utf-8 -*-
"""
tdx_tq_strategy.py — 在「通达信客户端内部」运行的官方 TQ 策略，做真实时推送采集。

这是通达信客户端自带的官方 Python 接口（PYPlugins/tqcenter.py + TPythClient.dll），
与"连行情服务器"那条路完全不同：它直接读客户端内存里的行情，并支持服务端推送订阅，
是延迟最低、最实时的方式；缺点是必须让客户端保持运行并登录。

【部署步骤】
1. 把这个文件复制到   C:\\new_tdx64\\PYPlugins\\user\\
2. 打开通达信客户端 → 「功能」→「 Python 策略」（或 TQ 策略管理器）→ 添加并运行本脚本
3. 跑起来后，数据会持续写到 OUT_DIR：
       tq_ticks_YYYYMMDD.jsonl   订阅推送到的每一次更新（真实时，非轮询）
       tq_latest.json            最新快照汇总
   外部程序（含 Hermes / 缠论分析）读这两个文件即可，不需要联网行情服务器。

要点/坑（来自 PYPlugins/user/tdxdata_test.py 官方示例）：
  * tq.initialize(__file__) 必须调用，且传脚本自身路径
  * 订阅 (subscribe_hq) 上限 100 只；回调签名必须是 on_data(datas)，datas 是 JSON 字符串
  * 回调里再调 tq.get_market_snapshot(code) 取最新盘口（曾用名 get_full_tick / get_report_data）
  * get_market_data 需要客户端已下载对应盘后数据；1m/5m/1d 支持 refresh_kline 预热
  * 可以直接调用通达信公式：tq.formula_zb(formula_name='MACD', formula_arg='12,26,9')
  * 可以把信号推回客户端界面：tq.send_warn(...)  —— 缠论买卖点预警可以这样弹到客户端
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime

from tqcenter import tq

# ------------------------------------------------------------------ 配置
OUT_DIR = r"E:\Hermes\tdx-live\data\tq"
# 留空 = 自动读取客户端里你当前的自选股（list_type=0 -> 自选股）
CODES: list[str] = []
# 订阅推送上限 100 只
MAX_SUB = 100
# 额外的定时快照间隔（秒）；0 表示只依赖推送
SNAPSHOT_EVERY = 30
# 每只每次推送最多保留在内存里的分钟数（用于外部读取）
WRITE_JSONL = True

os.makedirs(OUT_DIR, exist_ok=True)


def _day() -> str:
    return datetime.now().strftime("%Y%m%d")


def _append_jsonl(name: str, rec: dict):
    if not WRITE_JSONL:
        return
    with open(os.path.join(OUT_DIR, name), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _atomic_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ 主流程
tq.initialize(__file__)          # 所有策略连接客户端都必须先初始化

if not CODES:
    # list_type=0 -> 自选股；5 -> 所有A股；1 -> 持仓股
    CODES = tq.get_stock_list("0", list_type=0) or []
    print(f"[tq] 自选股 {len(CODES)} 只: {CODES}")

CODES = [c for c in CODES if c][:MAX_SUB]
if not CODES:
    tq.send_message("TQ策略：自选股为空，请先在客户端添加自选股或在脚本里填写 CODES")
    raise SystemExit("没有可订阅的代码")

LATEST: dict = {}
LAST_REPORT: dict = defaultdict(dict)


def on_data(data_str: str):
    """订阅回调：某只票有更新时客户端会回调这里（真实时推送）。
    datas 形如 {"Code":"600519.SH","ErrorId":"0"}"""
    try:
        j = json.loads(data_str)
    except Exception:
        return
    code = j.get("Code")
    if not code:
        return
    # 解析出 6 位代码，供外部程序统一使用
    pure = code.split(".")[0]
    snap = tq.get_market_snapshot(code) or {}
    rec = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": pure,
        "raw_code": code,
        "snapshot": snap,
    }
    LATEST[pure] = rec
    if snap:
        LAST_REPORT[pure] = snap
        _append_jsonl(f"tq_ticks_{_day()}.jsonl", rec)
        _atomic_json(os.path.join(OUT_DIR, "tq_latest.json"),
                     {"updated": rec["ts"], "count": len(LATEST), "ticks": LATEST})
    return None


print(f"[tq] 订阅 {len(CODES)} 只 ...")
tq.subscribe_hq(stock_list=CODES, callback=on_data)
print(f"[tq] 当前订阅列表: {tq.get_subscribe_hq_stock_list()}")
tq.send_message(f"TQ实时采集已启动：订阅 {len(CODES)} 只，输出目录 {OUT_DIR}")

# 订阅是推送式的，主线程只需要保持存活 + 定时兜底快照
last_snap = 0.0
while True:
    try:
        time.sleep(1)
        # 兜底：万一某只票长时间没推送（如停牌），也定期拉一次快照
        if SNAPSHOT_EVERY and time.time() - last_snap >= SNAPSHOT_EVERY:
            last_snap = time.time()
            for c in CODES:
                try:
                    s = tq.get_market_snapshot(c)
                    if s:
                        LAST_REPORT[c.split(".")[0]] = s
                except Exception:
                    pass
            _atomic_json(os.path.join(OUT_DIR, "tq_report.json"),
                         {"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          "count": len(LAST_REPORT), "report": LAST_REPORT})
            if not LATEST:
                print(f"[tq] 兜底快照已更新 {len(LAST_REPORT)} 只（暂无推送）", flush=True)
    except KeyboardInterrupt:
        break

tq.unsubscribe_hq(stock_list=CODES)
tq.close()
print("[tq] 已退出")
