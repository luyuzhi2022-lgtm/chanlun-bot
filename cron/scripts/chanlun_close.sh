#!/usr/bin/env bash
# Hermes cron 入口：缠论收盘全览（真正的实现在 E:\Hermes\chanlun-agent）
AGENT="${CHANLUN_AGENT:-E:/Hermes/chanlun-agent}"
exec bash "$AGENT/run_close.sh"
