# -*- coding: utf-8 -*-
"""飞书自定义机器人推送。

凭据放 .env（本机文件，不进聊天记录/不进 git）：
    FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
    FEISHU_SECRET=               # 机器人开启"签名校验"时填，没开就留空

用法：
    python push.py --text "一句话"
    python push.py --card signals.json      # 卡片（结构化信号）
    python push.py --selftest               # 发一条连通性测试
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env = {}
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("FEISHU_WEBHOOK", "FEISHU_SECRET", "FEISHU_ENABLED"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def feishu_enabled() -> bool:
    """飞书通道开关。2026-10-16 起推送改走 ClawChat，.env 里设 FEISHU_ENABLED=0 停用。

    留这个闸门（而不是删掉 webhook）是为了：任何一条漏改的调用路径都不会真的发到飞书，
    同时又保留凭据、想恢复推送只需把 0 改成 1。
    """
    v = str(ENV.get("FEISHU_ENABLED", "1")).strip().lower()
    return v not in ("0", "false", "no", "off", "disabled")


ENV = load_env()


def _sign(secret: str, ts: int) -> str:
    """飞书自定义机器人签名：key = f"{timestamp}\\n{secret}"，data = ""，HMAC-SHA256 后 base64。"""
    key = f"{ts}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")


def send(payload: dict, timeout: int = 15) -> dict:
    if not feishu_enabled():
        return {"ok": True, "sent": False, "skipped": "飞书已停用（FEISHU_ENABLED=0）；信号推送走 ClawChat"}
    url = ENV.get("FEISHU_WEBHOOK", "")
    if not url:
        return {"ok": False, "error": f"未配置 FEISHU_WEBHOOK（写到 {os.path.join(HERE, '.env')}）"}
    if ENV.get("FEISHU_SECRET"):
        ts = int(time.time())
        payload = dict(payload, timestamp=str(ts), sign=_sign(ENV["FEISHU_SECRET"], ts))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
        except Exception:
            return {"ok": False, "error": f"非 JSON 响应: {raw[:300]}"}
        code = j.get("code", j.get("StatusCode", -1))
        return {"ok": code in (0, None) and j.get("StatusCode", 0) == 0, "resp": j}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_text(text: str) -> dict:
    return send({"msg_type": "text", "content": {"text": text}})


def _line(sig: dict) -> str:
    t = sig.get("type", "?")
    st = sig.get("state", "?")
    code = sig.get("code", "")
    tag = {"三类买点": "🟢三买", "三类卖点": "🔴三卖", "一类买点": "🟢一买", "一类卖点": "🔴一卖",
           "二类买点": "🟢二买", "二类卖点": "🔴二卖"}.get(t, t)
    return f"**{code} {tag}**　{st}\n{sig.get('why', '')}"


def build_card(title: str, signals: list, summary: str = "", footer: str = "") -> dict:
    """信号卡片。signals: [{code,type,state,why,need,price,...}]"""
    els = []
    if summary:
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": summary}})
        els.append({"tag": "hr"})
    for s in signals[:20]:
        els.append({"tag": "div", "text": {"tag": "lark_md", "content": _line(s)}})
        if s.get("need"):
            els.append({"tag": "note", "elements": [{"tag": "plain_text",
                                                     "content": "待核验：" + str(s["need"])[:200]}]})
        els.append({"tag": "hr"})
    if footer:
        els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    return {"msg_type": "interactive",
            "card": {"config": {"wide_screen_mode": True},
                     "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:80]}},
                     "elements": els}}


def send_card(title: str, signals: list, summary: str = "", footer: str = "") -> dict:
    return send(build_card(title, signals, summary, footer))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--card", help="卡片 JSON 文件，或 - 从 stdin 读")
    ap.add_argument("--title", default="缠论盯盘")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        r = send_text(f"✅ 缠论盯盘推送通道连通测试\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    if a.text:
        r = send_text(a.text)
    elif a.card:
        raw = sys.stdin.read() if a.card == "-" else open(a.card, encoding="utf-8").read()
        d = json.loads(raw)
        if isinstance(d, list):
            r = send_card(a.title, d)
        elif d.get("signals") is not None:
            r = send_card(a.title, d["signals"], d.get("summary", ""), d.get("footer", ""))
        else:
            r = send(d)
    else:
        ap.error("需要 --text / --card / --selftest")
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
