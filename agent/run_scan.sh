#!/usr/bin/env bash
# 缠论盯盘：采集自选股 -> 结构计算 -> 三类买卖点候选
# 用法: run_scan.sh [intraday|close]
# 必须用 tdx-live 的 venv（tdxlive 依赖它），所以不能直接让 Hermes 的 python 跑。
cd "$(dirname "$0")" || exit 1
PY="${TDX_PY:-E:/Hermes/tdx-live/.venv/Scripts/python.exe}"
if [ ! -x "$PY" ]; then
  echo "{\"trading\": false, \"reason\": \"venv python 不存在: $PY\"}"
  exit 1
fi
exec "$PY" chanlun_prep.py --mode "${1:-intraday}" "${@:2}"
