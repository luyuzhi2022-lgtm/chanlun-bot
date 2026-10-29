# -*- coding: utf-8 -*-
"""按 prep 数据组装信号正文并推送。

通道由 config.json 的 push.channel 决定：
    "clawchat"（现状）→ 渲染成 markdown，用 `hermes -p chanlun send --to clawchat` 发到
                        ClawChat 的 home channel（= 该 profile 激活时建立的私聊）。
    "feishu"          → 组装飞书 interactive 卡片（已停用：.env 里 FEISHU_ENABLED=0）。

为什么要这个脚本：让模型每半小时手抄一遍中文理由，早晚抄错。这里模型只输出"决定"，
正文（why / need / ZG / ZD）直接从 prep JSON 里取，保证推送内容与计算结果一致。

用法：
    python push_signals.py --prep out/prep_intraday_20261016_1000.json --decide decide.json
    python push_signals.py --prep-latest --decide decide.json          # 取最新一份 prep

decide.json 格式（模型只需要产出这个）：
{
  "title": "缠论盯盘 10:00 候选信号",
  "summary": "大盘背景一句话（可选）",
  "approve":  [{"code": "600089", "type": "三类买点", "note": "补充/失效条件（可选）"}],
  "reject":   [{"code": "688270", "type": "二类卖点", "reason": "被否掉的原因"}],
  "footer": "非投资建议；仅结构判断，不含 T+1/滑点/涨跌停约束"
}
approve 里的 code+type 必须在 prep JSON 里能找到，否则报错并列出可选项。
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import push  # noqa: E402

ICON = {"三类买点": "🟢 三买", "三类卖点": "🔴 三卖",
        "一类买点": "🟢 一买", "一类卖点": "🔴 一卖",
        "二类买点": "🟢 二买", "二类卖点": "🔴 二卖"}


def latest_prep():
    """取「最新一轮」prep —— 按文件名里的时间戳排序，**不能按字典序**。

    踩过的坑：字典序里 'close' < 'intraday'，所以 sorted()[-1] 永远取到当天的最后一轮
    **盘中** prep，15:05 的收盘全览会拿错数据（实测取到 14:33 的盘中 prep）。
    """
    files = glob.glob(os.path.join(HERE, "out", "prep_*.json"))
    if not files:
        raise SystemExit("out/ 下没有 prep_*.json")

    def _stamp(p):
        m = re.search(r"_(\d{8})_(\d{4})\.json$", os.path.basename(p))
        return (m.group(1) + m.group(2)) if m else ""
    return max(files, key=lambda p: (_stamp(p), os.path.getmtime(p)))


def index_candidates(prep):
    idx = {}
    for s in prep.get("stocks", []):
        if "error" in s:
            continue
        for c in s.get("candidates", []):
            key = (s["code"], c.get("type"))
            # 同一 code+type 只可能有多个状态，后出现的（更新的）覆盖
            idx.setdefault(key, []).append((s, c))
    return idx


def pick(idx, code, typ):
    got = idx.get((code, typ))
    if not got:
        return None
    # 优先取 候选/已确认，其次其他状态
    for pref in ("候选", "已确认", "最弱形态", "信息不足", "已失效"):
        for s, c in got:
            if c.get("state") == pref:
                return s, c
    return got[-1]


def load_config():
    try:
        return json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
    except Exception:
        return {}


def push_config():
    return (load_config().get("push") or {})


def render_text(decide, body, reject_txt, ts):
    """把同一轮信号渲染成 ClawChat 用的 markdown（飞书卡片 → 纯文本）。"""
    out = [f"**{decide.get('title') or f'缠论盯盘 {ts}'}**"]
    if decide.get("summary"):
        out += ["", str(decide["summary"])]
    if body:
        out += ["", body]
    if reject_txt:
        out += ["", "本轮已排除：" + reject_txt]
    out += ["", (decide.get("footer") or "非投资建议；仅结构判断，不含 T+1/滑点/涨跌停约束")]
    return "\n".join(out).strip() + "\n"


def hermes_exe(cfg):
    """定位 hermes 可执行文件：config.json 的 push.clawchat.hermes_cmd → PATH → 本机默认路径。"""
    cand = cfg.get("hermes_cmd") or ""
    if cand and os.path.exists(cand):
        return cand
    found = shutil.which("hermes")
    if found:
        return found
    default = os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "hermes", "hermes-agent",
                           ".hermes", "bin", "hermes")
    return default if os.path.exists(default) else ""


def send_clawchat(text, ts):
    """用 Hermes CLI 投递到 ClawChat（不经过模型）。

    `hermes send` 只做投递：读该 profile 的 .env / config.yaml 里的 ClawChat 凭据，
    `--to clawchat`（裸平台名）即 home channel —— 激活时写入 profile .env 的
    CLAWCHAT_HOME_CHANNEL，也正是该 agent 与主人的私聊。
    """
    cfg = push_config().get("clawchat") or {}
    target = cfg.get("target") or "clawchat"
    profile = cfg.get("profile") or ""
    home = cfg.get("hermes_home") or ""
    exe = hermes_exe(cfg)
    if not exe:
        return {"ok": False, "error": "找不到 hermes 可执行文件（可在 config.json 的 push.clawchat.hermes_cmd 指定）"}

    digits = re.sub(r"\D", "", ts)
    tag = digits[8:12] if len(digits) >= 12 else (digits[-4:] or time.strftime("%H%M"))
    msg_path = os.path.join(HERE, "out", f"msg_{tag}.md")
    os.makedirs(os.path.dirname(msg_path), exist_ok=True)
    with open(msg_path, "w", encoding="utf-8") as f:
        f.write(text)

    cmd = [exe] + (["-p", profile] if profile else []) + \
          ["send", "--to", target, "--file", msg_path, "--json"]
    env = dict(os.environ)
    if home:
        env["HERMES_HOME"] = home
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=int(cfg.get("timeout_s") or 120), env=env)
    except Exception as e:
        return {"ok": False, "channel": "clawchat", "error": f"{type(e).__name__}: {e}", "msg_file": msg_path}

    raw = (p.stdout or "").strip()
    j = {}
    if "{" in raw:
        try:
            j = json.loads(raw[raw.index("{"):])
        except Exception:
            j = {}
    if j.get("success"):
        return {"ok": True, "channel": "clawchat", "target": target,
                "message_id": j.get("message_id"), "msg_file": msg_path}
    return {"ok": False, "channel": "clawchat", "exit": p.returncode, "msg_file": msg_path,
            "error": (j.get("error") or p.stderr or raw or "发送失败")[:400]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep")
    ap.add_argument("--prep-latest", action="store_true")
    ap.add_argument("--decide", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    prep_path = latest_prep() if a.prep_latest else a.prep
    if not prep_path:
        raise SystemExit("需要 --prep 或 --prep-latest")
    # 注意：本脚本跑在原生 Windows python 下，MSYS 风格路径（/tmp/x.json）解析不了，
    # 传参必须用 E:/... 或相对路径。这里把错误转成可读 JSON，方便调用方自我纠正。
    try:
        prep = json.load(open(prep_path, encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"读不到 prep 文件 {prep_path!r}: {type(e).__name__}: {e}",
                          "hint": "路径要用 Windows 风格（E:/Hermes/... 或 out/xxx.json），不要用 /tmp 这类 MSYS 路径"},
                         ensure_ascii=False))
        return 2
    try:
        decide = json.load(open(a.decide, encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"读不到 decide 文件 {a.decide!r}: {type(e).__name__}: {e}",
                          "hint": "路径要用 Windows 风格；decide 建议写在 out/ 目录里"},
                         ensure_ascii=False))
        return 2
    idx = index_candidates(prep)

    signals, missing = [], []
    for item in decide.get("approve", []):
        got = pick(idx, item.get("code"), item.get("type"))
        if not got:
            missing.append(f"{item.get('code')} {item.get('type')}")
            continue
        s, c = got
        why = c.get("why", "")
        if item.get("note"):
            why = why + "\n　▸ " + str(item["note"])
        signals.append({
            "code": s["code"],
            "type": c.get("type"),
            "state": c.get("state"),
            "why": why,
            "need": c.get("need", ""),
            "price": s.get("price"),
            "chg_pct": s.get("chg_pct"),
            "endpoint": c.get("endpoint", ""),
        })
    if missing:
        avail = sorted({f"{s['code']} {c.get('type')}" for (_, _t), v in idx.items() for s, c in v})
        print(json.dumps({"ok": False, "error": f"prep 里找不到: {missing}", "available": avail},
                         ensure_ascii=False))
        return 2

    if not signals and not decide.get("freeform"):
        print(json.dumps({"ok": True, "sent": False, "reason": "approve 为空且无 freeform，未推送"}, ensure_ascii=False))
        return 0

    lines = []
    ts = prep.get("time", "")
    for s in signals:
        head = f"**{s['code']}　{ICON.get(s['type'], s['type'])}**　{s['state']}"
        if s.get("price") is not None:
            head += f"　现价 {s['price']}（{s.get('chg_pct')}%）"
        lines.append(f"{head}\n{s['why']}")
        if s.get("need"):
            lines.append(f"　▸ 待核验：{s['need']}")
        lines.append("")
    body = "\n".join(lines)
    if decide.get("freeform"):
        body = (body + "\n" + str(decide["freeform"])).strip()

    reject_txt = ""
    if decide.get("reject"):
        reject_txt = "；".join(f"{r.get('code')} {r.get('type')}：{r.get('reason', '')}"
                              for r in decide["reject"])[:400]

    channel = str(push_config().get("channel") or "feishu").lower()

    # ---- ClawChat 通道（现状）：渲染 markdown -> hermes send 投递，不经过模型 ----
    if channel != "feishu":
        text = render_text(decide, body, reject_txt, ts)
        if a.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "channel": channel, "text": text},
                             ensure_ascii=False))
            return 0
        r = send_clawchat(text, ts)
        print(json.dumps({"ok": r.get("ok"), "sent": r.get("ok"), "channel": channel,
                          "count": len(signals), "message_id": r.get("message_id"),
                          "resp": r.get("error")}, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    # ---- 飞书通道（已停用）----
    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue",
                       "title": {"tag": "plain_text",
                                 "content": (decide.get("title") or f"缠论盯盘 {ts}")[:80]}},
            "elements": (
                ([{"tag": "div", "text": {"tag": "lark_md", "content": decide["summary"]}},
                  {"tag": "hr"}] if decide.get("summary") else [])
                + [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
                + ([{"tag": "hr"},
                    {"tag": "note", "elements": [{"tag": "plain_text",
                                                  "content": "本轮已排除：" + reject_txt}]}]
                   if reject_txt else [])
                + [{"tag": "note", "elements": [{"tag": "plain_text",
                                                 "content": (decide.get("footer")
                                                             or "非投资建议；仅结构判断，不含 T+1/滑点/涨跌停约束")[:400]}]}]
            ),
        },
    }
    if a.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "card": card}, ensure_ascii=False))
        return 0
    r = push.send(card)
    print(json.dumps({"ok": r.get("ok"), "sent": r.get("ok"), "count": len(signals),
                      "resp": r.get("resp") or r.get("error")}, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
