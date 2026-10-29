# 缠论盯盘 Bot

> 一个自托管的 A 股「缠论结构」盯盘机器人：**通达信自选股 → 实时行情 → 分型/笔/中枢/MACD → 三类买卖点候选 → 10 个缠论技能逐条复核 → 推送到手机**。
>
> 它跑在 [Hermes Agent](https://hermes-agent.nousresearch.com/docs) 上，用 cron 定时唤醒，用 ClawChat 投递。
> 判定结果只有四种状态：`候选 / 已确认 / 已失效 / 信息不足`。

## ⚠️ 先说清楚：这不是荐股工具

- 输出的是**结构判断 + 成立/失效条件**，不是「现在买入/卖出」。
- 不承诺盈利。缠论原文里的「必然」是结构意义上的结论，**不含** T+1、滑点、涨跌停、流动性约束。
- 所有判断都可能错。自己下单的决定权、后果，都在你自己手上。
- 仅供个人研究与技术学习，不构成任何投资建议。

## 它做什么

| 环节 | 实现 | 说明 |
|---|---|---|
| 标的来源 | `tdx_live.read_watchlist()` | 直接读通达信客户端自选股 `T0002/blocknew/zxg.blk`，客户端里加票即生效 |
| 行情 | `tdx-live/tdxlive.py` | pytdx 多主站直连，30m / 5m / 1m / day 四级 K 线 |
| 结构 | `agent/chan_structure.py` | 分型 → 笔 → 中枢（ZD/ZG）→ MACD 力度 → 三类买卖点候选 |
| 复核 | `skills/finance/chanlun-*` | 10 个缠论技能，由模型按固定次序逐条打勾（结构合法性、中枢、背驰三前提、中阴、区间套、中枢震荡、小转大、多义性、背驰后情景、买卖点） |
| 门闸 | `agent/chanlun_prep.py --wake-gate` | 非交易时段 / 无行情 / 无新候选 → 输出 `{"wakeAgent": false}`，cron 直接跳过，**不唤醒模型、零 token** |
| 去重 | `agent/state/seen.json` | 键 = `代码|类型|端点时间|状态`，同一信号不重复推；候选→已确认才再推一次 |
| 推送 | `agent/push_signals.py` | 渲染 markdown → `hermes -p <profile> send --to clawchat`，直达你的 ClawChat 私聊；也支持飞书自定义机器人 |

典型一轮（收盘全览）的输出长这样：每只票给 ① 当日走势类型与中枢状态 ② 中枢区间 `[ZD, ZG]` 及是第几个中枢 ③ 最后一个买卖点的类型/状态/位置 ④ 明日要盯的成立条件与失效条件 ⑤ 背驰后的三种情景。

## 架构

```
cron (15:05 收盘 / 盘中每半小时)
  │  script: cron/scripts/chanlun_close.sh
  ▼
agent/run_close.sh  ──►  tdx/python  ──►  agent/chanlun_prep.py
                                             │  读通达信自选股 → tdxlive 取 K 线
                                             │  chan_structure.py 算结构
                                             ▼
                                    stdout: 精简 JSON（候选 + 上证背景）
                                             │  {"wakeAgent": false} 时到此为止
                                             ▼
                              Hermes agent（SOUL.md + 10 个缠论技能）
                                             │  逐只复核，四选一给状态
                                             ▼
                                    out/decide_*.json
                                             ▼
                              agent/push_signals.py ──► ClawChat / 飞书
```

## 目录结构

```
SOUL.md                        Bot 的人格与纪律（这个 bot「是谁、能做什么、绝不做什么」）
agent/                         结构算法 + 采集 + 推送（核心引擎）
  chan_structure.py            分型/笔/中枢/MACD/三类买卖点
  chanlun_prep.py              采集 + 结构 + 候选 → 精简 JSON（stdout 喂给模型）
  chan_chart.py                单只标的缠论结构图 → 自包含 HTML
  push_signals.py              组装正文并按通道投递
  push.py                      飞书通道（默认停用）
  run_scan.sh / run_close.sh   本地入口（必须用 tdx 的 venv）
  config.json                  级别、K线根数、并发、推送通道
tdx/                           行情层（tdxlive.py / watch.py / verify.py）
skills/finance/                12 个技能：10 个缠论 + chanlun-live-scan（总调度）+ tdx-realtime-quotes
cron/                          两个 cron 任务的转发脚本与 prompt
config/                        Hermes profile 配置示例
memories/                      Hermes 记忆文件模板（放你自己的偏好等）
.env.example                   推送凭据模板（复制成 .env，永不提交）
```

## 依赖

| 依赖 | 说明 |
|---|---|
| Windows + 通达信 PC 客户端（64 位） | 行情与自选股都从本地客户端读。默认路径 `C:\new_tdx64`，用环境变量 `TDX_DIR` 覆盖 |
| Python 3.10+（实测 3.14） | 独立 venv，装 `pytdx` `mootdx` `pandas` `numpy` |
| Hermes Agent | 提供 cron、技能、模型调用、ClawChat 投递 |
| ClawChat（可选） | 推送通道；不要就改 `config.json` 的 `push.channel="feishu"` 或自己实现 |

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r tdx/requirements.txt
```

## 安装

1. **放代码**：把本仓库放到例如 `E:/Hermes/chanlun-bot`，`agent/` 和 `tdx/` 保持同级（`chanlun_prep.py` 默认就在同级找 `tdx/`；不在同级就设 `CHANLUN_TDX_LIVE`）。
2. **装 venv**：见上一节，路径记下来，后面要用。
3. **确认行情能出**：
   ```bash
   E:/Hermes/chanlun-bot/.venv/Scripts/python.exe tdx/watch.py scan      # 主站可用性
   ```
4. **确认自选股读得到**：通达信客户端里加一两支票，然后
   ```bash
   bash agent/run_scan.sh intraday --force        # 非交易时段加 --force 取上一交易日数据
   ```
   正常会打印一段 JSON：`{"trading": ..., "stocks": [...], "candidates": [...]}`。
5. **装技能**：把 `skills/finance/*` 复制到 Hermes 的技能目录
   ```bash
   cp -r skills/finance/* ~/AppData/Local/hermes/profiles/<profile>/skills/finance/
   ```
   （或放到全局 `~/.hermes/skills/finance/`，看你用哪个 profile。）
6. **装人格与配置**：`SOUL.md` 放到 profile 根目录；`config/hermes-profile.example.yaml` 里的 `model` / `platforms.clawchat` / `cron` 三块按你自己的情况填进 `profiles/<profile>/config.yaml`。
7. **配推送**：`cp .env.example .env`，需要飞书就填 webhook；走 ClawChat 只需把 `agent/config.json` 里 `push.clawchat.profile` 和 `hermes_home`（Hermes 根目录）填对。
8. **建 cron 任务**：见 `cron/README.md`，两个任务（收盘全览必开，盘中扫描按需）。

## 手动跑一轮

```bash
cd E:/Hermes/chanlun-bot
bash agent/run_scan.sh intraday            # 交易时段外会返回 trading:false
bash agent/run_scan.sh intraday --force    # 调试：非交易时段也采集
bash agent/run_close.sh                    # 收盘全览数据
```

`run_scan.sh` 用 `TDX_PY` 环境变量指定解释器（默认 `E:/Hermes/tdx-live/.venv/Scripts/python.exe`）；`cron/scripts/*.sh` 用 `CHANLUN_AGENT` 指定引擎目录。

## 把候选推成一条消息

```bash
# 1) 模型复核后写出决定文件
cat > agent/out/decide_1505.json <<'EOF'
{"title":"缠论盯盘 15:05","summary":"上证 30m 仍在第 3 个中枢内震荡",
 "approve":[{"code":"600089","type":"三类买点","note":"失效条件：跌回 17.43"}],
 "reject":[{"code":"002555","type":"二类买点","reason":"一买未过背驰三前提"}],
 "footer":"非投资建议；仅结构判断"}
EOF

# 2) 投递
cd E:/Hermes/chanlun-bot/agent
<tdex-python> push_signals.py --prep-latest --decide out/decide_1505.json
# 返回 {"ok":true,"sent":true} 才算成功
```

## 几个工程取舍（踩过坑才有的设计）

- **wake gate**：盘中 9 轮里绝大多数没有新信号。没有候选时脚本输出 `{"wakeAgent": false}`，调度器直接跳过，不烧模型。实测省掉约 90% 空转（每轮约 3.7 万 token）。
- **笔近似次级别走势段**：用本级别「笔」代替「次级别走势类型」构中枢，算的是**本级别中枢**。引用时要说清这个近似。
- **中枢退化过滤**：三段重叠宽度 < 价格 × 0.25% 不算中枢，否则会出 `[17.16, 17.17]` 这种宽 1 分钱的假中枢。
- **中枢区间锁原文口径**：`[ZD, ZG] = [max(低_A, 低_C), min(高_A, 高_C)]`（A、C 为与中枢形成方向一致的前两个段）。**不要**改成「所有段重叠」口径当三买卖点基准——口径不同、公式不同，不可互换。
- **指数只作背景**：`is_index: true`（如 `999999`）不出可执行买卖点，只给大盘涨跌家数。
- **状态机纪律**：`候选 / 已确认 / 已失效 / 信息不足`，不得把候选当确认；判断不了就写「信息不足」，不为凑结论而编。

## 已知限制

- 只能跑 Windows + 通达信客户端；纯 Linux 服务器要自己换行情源（`tdx/tdxlive.py` 是唯一耦合点）。
- pytdx / mootdx 在线行情在部分网络环境会失效，`tdx/watch.py scan` 是排查入口。
- 结构算法是「原文口径的可执行近似」，边界情形（段被破坏、扩展中枢、多义性）最终仍需人看。
- 推送依赖 ClawChat 连接处于 `state=ready`；返回 `ok:true` 但没收到时先看 profile 的 `logs/*.log`。

## 许可

MIT，见 `LICENSE`。缠论原文的著作权归原作者；本仓库只包含对原文的工程化实现与解读笔记。