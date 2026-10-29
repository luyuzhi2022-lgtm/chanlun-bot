#!/usr/bin/env bash
# 缠论收盘全览（每交易日 15:05）
cd "$(dirname "$0")" || exit 1
PY="${TDX_PY:-E:/Hermes/tdx-live/.venv/Scripts/python.exe}"
if [ ! -x "$PY" ]; then
  echo "{\"trading\": false, \"reason\": \"venv python 不存在: $PY\"}"
  exit 1
fi
exec "$PY" chanlun_prep.py --mode close "$@"
