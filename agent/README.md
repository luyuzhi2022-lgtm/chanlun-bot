# 缠论盘中自动扫描（三类买卖点 → 手机推送）

每半小时（A 股连续竞价时段）自动：读通达信自选股 → 取实时行情 → 算分型/笔/中枢/MACD
→ 生成三类买卖点**候选** → 由 10 个缠论技能逐只复核 → 有信号就推到你手机（ClawChat）。

> 不是荐股工具，也不产生"现在买入/卖出"指令。它输出的是**结构判断 + 成立/失效条件**，
> 下单是你的决定。原文里"必然盈利"是结构意义上的结论，不含 T+1、滑点、涨跌停、流动性约束。

## 目录

```
chan_structure.py    分型/笔/中枢/MACD（口径对齐 chanlun-structure-parser / -zhongshu-mapper / -divergence-diagnosis）
chanlun_prep.py      采集 + 结构 + 候选 → 精简 JSON（stdout，喂给 LLM）
chan_chart.py        单只标的缠论结构图 → out/<code>_chan.html（自包含，无外部依赖）
run_scan.sh          cron 入口（内部用 tdx-live 的 venv）
push.py              飞书推送（文本 / 交互卡片 / 签名校验）——**已停用**（.env: FEISHU_ENABLED=0）
push_signals.py      按 prep 数据组装正文并按 push.channel 投递（clawchat / feishu）
config.json          级别、K线根数、并发、推送通道（push.channel）
.env                 通道开关（FEISHU_ENABLED）+ 飞书凭据
out/                 每轮完整结果 prep_*.json + decide_*.json + msg_*.md（发出的正文）
state/seen.json      已推送信号去重表
```

## 推送通道（当前：ClawChat）

2026-10-16 起信号推送到 **ClawChat**（这个 profile 自己的 agent 与你的私聊），飞书通道停用。

```
config.json  push.channel = "clawchat"        # 换成 "feishu" 回旧通道
.env         FEISHU_ENABLED=0                 # 飞书闸门；=1 才可能发出去
```

投递方式：`push_signals.py` 把同一轮信号渲染成 markdown，写 `out/msg_<HHMM>.md`，再调

```bash
hermes -p chanlun send --to clawchat --file out/msg_<HHMM>.md --json
```

`--to clawchat`（裸平台名）= 该 profile 的 home channel，也就是激活时写入
`profiles/chanlun/.env` 的 `CLAWCHAT_HOME_CHANNEL`。不经过模型，Hermes 只做投递。

验证通道：

```bash
cd E:/Hermes/chanlun-agent
E:/Hermes/tdx-live/.venv/Scripts/python.exe push.py --selftest        # 应返回 skipped（飞书已停用）
hermes -p chanlun send --to clawchat "通道自检：缠论盯盘推送"          # 应返回 success: true
```

想恢复飞书：`.env` 改 `FEISHU_ENABLED=1`、`config.json` 改 `push.channel="feishu"`（飞书凭据仍在 `.env` 里）。

## 手动跑一轮

```bash
cd E:/Hermes/chanlun-agent
bash run_scan.sh intraday          # 交易时段外会直接返回 trading:false，不推
bash run_scan.sh intraday --force  # 调试：非交易时段也采集（取上一交易日收盘数据）
bash run_scan.sh close             # 收盘全览
```

`run_scan.sh` 必须用 `E:\Hermes\tdx-live\.venv` 里的 python（`tdxlive` 依赖那个环境）。

## 自选股

标的**实时读** `C:\new_tdx64\T0002\blocknew\zxg.blk`——在通达信客户端里加自选，
下一轮自动带上，不用改任何配置。

- `999999` 这类指数只作**大盘背景**（能拿涨跌家数），脚本会标 `is_index: true`，不出可执行买卖点。
- 想去掉的代码写进 `config.json` 的 `exclude_codes`。

## 输出契约

**状态只能四选一**：`候选 / 已确认 / 已失效 / 信息不足`。

推送只带 `候选` 与 `已确认`；`已失效`/`信息不足` 只在消息底部"本轮已排除"里一句话说明。
去重键 = `代码|类型|端点时间|状态`，记在 `state/seen.json`——同一信号不重复推，
只有状态升级（候选→已确认）才会再推一次。

## 结构算法的工程约定（不是原文口径，引用时要标注）

| 约定 | 说明 |
|---|---|
| 笔近似次级别走势段 | 用本级别「笔」代替「次级别走势类型」构中枢，所以算的是**本级别中枢** |
| 笔的最小间隔 | `min_gap=4`（合并K线索引差）= 两分型不共用K线且中间至少一根独立K线 |
| 中枢退化过滤 | 三段重叠区宽度 < 价格×0.25% 不算中枢，否则会出 `[17.16,17.17]` 这种宽 0.01 元的假中枢 |
| 中枢区间（**已锁定原文口径**） | ch20 原文：三段 A、B、C 的高/低点为 a1/a2、b1/b2、c1/c2，区间 = [max(a2,b2,c2), min(a1,b1,c1)]；因 A、C 段方向与中枢形成方向一致，可简化为 **[ZD, ZG] = [max(a2,c2), min(a1,c1)]**（= 前两个 Z 段，成立即锁定）。代码实现：ZG=min(高_A,高_C)、ZD=max(低_A,低_C)；随走势走宽的是 GG/DD，不是 ZG/ZD。**不得改用「所有段 / 三段重叠」口径作为三买卖点基准**（口径不同、公式不同，不可互换）。 |
| 三买的「已确认」 | 只表示回试段之后又走了一段；"回试是否结束"要看 5m/1m 的背驰或双次回拉 |

结构算法细节、10 个技能的应用次序、排障清单都写在技能 `chanlun-live-scan` 里。

## 排障

| 症状 | 原因 / 处理 |
|---|---|
| `trading:false` | 正常：非交易时段 / 周末 / 非交易日（用上证日线最后更新日期判断） |
| `自选股为空` | 通达信自选股文件没读到；客户端里加一只再看 |
| `30m 结构不足（K线 N 根）` | 主站没返回足够 K 线；跑 `tdx-live/watch.py scan` 看主站可用性 |
| `{"ok": false, "error": "找不到 hermes 可执行文件..."}` | PATH 里没有 `hermes`；在 `config.json` 的 `push.clawchat.hermes_cmd` 里写全路径 |
| `{"ok": false, "channel": "clawchat", "error": "Could not resolve ... on clawchat"}` | 目标名写错了。用裸 `clawchat`（home channel）；`clawchat:cnv_...` 这种显式 id 目前不被解析器接受 |
| ClawChat 侧没收到，但返回 `ok:true` | 看 `profiles/chanlun/logs/*.log` 里的 `standalone send ok` + `ack_received`；只有 `state=ready` 的连接才能发出 |
| 飞书 returncode 非 0 / `code != 0` | 通道已停用（`FEISHU_ENABLED=0` 会直接 skipped）；真要恢复先改 `.env` 与 `config.json` |
| 结构全是 0 个中枢 | 改了 `chan_structure.py` 之后没回看真实数据——跑 `--force` 核对每只票的 `zs_count` 与笔序列 |
