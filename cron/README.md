# 两个 cron 任务

盯盘的全部自动行为都由这两个任务驱动。**核心设计：脚本先跑，模型后跑**——
脚本负责取数、算结构、过滤噪声；只要没东西可看，就输出 `{"wakeAgent": false}` 把这一轮掐掉，
不唤醒模型、不投递、不产生对话记录、零 token。

| 任务 | 表达式 | 脚本 | 何时跑 |
|---|---|---|---|
| 缠论收盘全览 | `5 15 * * 1-5` | `chanlun_close.sh` | 每个交易日 15:05，**每天必推一份** |
| 缠论盘中扫描 | `0,30 10-11,13-14 * * 1-5` | `chanlun_intraday.sh` | 交易时段每半小时，**只在有新信号/进展时推** |

## 建任务

先把两个脚本放到 Hermes 的 scripts 目录（cron 的 `--script` 只接受 `~/.hermes/scripts/` 下的相对路径）：

```bash
mkdir -p ~/AppData/Local/hermes/profiles/<profile>/scripts
cp cron/scripts/chanlun_*.sh ~/AppData/Local/hermes/profiles/<profile>/scripts/
chmod +x ~/AppData/Local/hermes/profiles/<profile>/scripts/chanlun_*.sh
```

收盘全览：

```bash
hermes -p <profile> cron create '5 15 * * 1-5' "$(cat cron/prompts/close.txt)" \
  --name '缠论收盘全览' \
  --script chanlun_close.sh \
  --skill chanlun-live-scan \
  --deliver 'bot-chat' \
  --workdir E:/Hermes/chanlun-bot
```

盘中扫描（默认建议先 `--paused`，等你确认收盘全览跑通、且能接受盘中被叫醒再 resume）：

```bash
hermes -p <profile> cron create '0,30 10-11,13-14 * * 1-5' "$(cat cron/prompts/intraday.txt)" \
  --name '缠论盘中扫描' \
  --script chanlun_intraday.sh \
  --skill chanlun-live-scan \
  --deliver 'bot-chat' \
  --workdir E:/Hermes/chanlun-bot \
  --paused
```

- `--deliver bot-chat`：把结果投进该 profile 的 Bot Chat（你的 ClawChat 私聊）。如果你在其他平台用，换成对应 target。
- `--skill chanlun-live-scan`：**只预加载这一个技能**。另外 10 个缠论技能由 prompt 指示按需 `skill_view` 读入——全预加载会每轮多烧几万 token。
- 设 `--model` / `--provider` 可把任务钉死在便宜模型上（否则跟随 `cron.model`）。

## 为什么 prompt 那么长

`cron/prompts/*.txt` 是这些任务的实际 prompt，不是文档。它们规定死了：

1. **门闸**：`trading=false` / `stocks` 为空 → 只回 `[SILENT]` 七个字符，这是 Hermes 的内置令牌，抑制投递。
2. **复核次序**：哪个技能管哪条细则，不许凭记忆判。
3. **状态机**：`候选 / 已确认 / 已失效 / 信息不足`，候选不得当确认。
4. **推送契约**：`decide_*.json` 的字段、`push_signals.py` 的调用方式、`{"ok":true,"sent":true}` 才算成功。
5. **纪律**：禁止输出「现在买入/卖出」，只给结构判断与失效条件。

改 prompt 时别删这三条：`[SILENT]` 令牌、状态四选一、禁止买卖指令。

## 排障

| 症状 | 处理 |
|---|---|
| 到点没动静 | `hermes -p <profile> cron list` 看 state；`cron runs` 看最近执行；`cron doctor` 查常见病 |
| 每轮都唤醒但没内容 | 检查 `chanlun_prep.py` 是否真输出 `{"wakeAgent": false}`（脚本改动后最容易破） |
| 有信号但没收到 | 看 `push_signals.py` 返回；`ok:true` 但没收到 → `profiles/<profile>/logs/*.log` 里找 `standalone send ok` |
| 数据全空 | 先跑 `bash agent/run_scan.sh intraday --force` 手测，再跑 `tdx/watch.py scan` 看主站 |