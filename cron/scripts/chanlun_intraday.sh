#!/usr/bin/env bash
# Hermes cron 入口：缠论盘中扫描（真正的实现在 E:\Hermes\chanlun-agent）
# cron 的 script 必须是 scripts/ 下的相对路径，所以这里只做一层转发。
#
# --wake-gate（2026-10-16 加）：非交易时段 / 无行情 / 没有「候选|已确认」信号时，
# chanlun_prep.py 会在 stdout 末行追加 {"wakeAgent": false}，cron 的 scheduler 读到后
# 直接跳过本次 agent 运行 —— 不唤醒模型、不投递、Bot Chat 不增长、零 token。
# 无信号的轮次本来就不需要模型复核（盘中 9 轮里绝大多数如此），这一层省掉 90% 空转。
# 手动调试想绕过：直接跑 bash E:/Hermes/chanlun-agent/run_scan.sh intraday --force。
AGENT="${CHANLUN_AGENT:-E:/Hermes/chanlun-agent}"
exec bash "$AGENT/run_scan.sh" intraday --wake-gate
