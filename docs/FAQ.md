# FAQ / 排障

README 里那六条之外的问题都在这里。**按症状查，别从头读。**

---

## 行情连不上

**症状**：`tdx/watch.py scan` 输出多行 `[timeout]` / `[fail]`，或 `run_scan.sh` 报 `全采集失败`。

按这个顺序试：

1. **再扫一次。** pytdx 主站是随机挑的，第一次全挂第二次能连上是常事。
2. **换网络**：手机热点。部分宽带/公司网会拦 7709 端口。
3. **看是不是只有某几个主站挂**：`scan` 会把每个主站的耗时打出来，能用的是 `data[ok] N根`，看它挑了哪个。
4. **确认是真的连不上，而不是代码问题**：`verify.py` 走同一套连接，两个都挂就是网络。

> `tdxlive.py` 是唯一和「通达信/网络」耦合的文件。想换数据源（Tushare、AkShare、本地 CSV），改这一个文件的 `get_bars()`，其余全部不用动。

---

## 自选股读不到

**症状**：`自选股为空` 或 `zxg.blk` 找不到。

```bash
# 1) 文件到底在不在
dir C:\new_tdx64\T0002\blocknew\zxg.blk
```

- **不存在**：你的通达信不在 `C:\new_tdx64`。找一下：右键客户端图标 → 属性 → 「打开文件所在位置」，然后在 `config.json` 或环境变量里设 `TDX_DIR` 指向那个目录（**指向含 `T0002` 的那一层**）。
- **存在但还是空**：`zxg.blk` 是二进制块文件，只在你**改过自选股之后**才会刷新。客户端里随便加一只票、退出客户端让文件落盘，再跑。
- **有多个自选股分组**：本仓库只读默认的 `zxg.blk`。要读别的分组，改 `tdx_live.read_watchlist()` 里的文件名（`blocknew/` 下还有 `*.blk`，用文本编辑器能看出代码）。

---

## 结构算不出来 / 结果明显不对

**症状**：`30m 结构不足（K线 N 根）`、笔数为 0、中枢是 `[ZD 0, ZG 0]`。

| 原因 | 判断 | 处理 |
|---|---|---|
| K 线根数不够 | 报错里 `K线 N 根`，N < 100 | 调大 `config.json` 的 `bars_count.30m`（默认 320） |
| 新股/停牌 | 上市不足 60 个交易日，或长期停牌 | 本身就没有结构，`exclude_codes` 挪走 |
| 主站给了脏数据 | 同一只票两次跑结论差很远 | `tdx/verify.py` 交叉验一下；换主站重跑 |
| 假中枢 | 中枢宽度只有一两分钱 | 已有退化过滤（< 价格 × 0.25% 丢弃）。仍出现就把阈值调大 |

**中枢到底是第几个？** `seg` 是进中枢的段数，`ext` 是延伸出的段数。`seg ≥ 9` 说明中枢扩张、级别升级，此时「第几个中枢」的计数会跟你直觉不一致——这是缠论的口径，不是 bug。

---

## 推送说 ok 但手机没收到

**症状**：`push_signals.py` 返回 `{"ok": true}`，或 cron 显示成功，手机安静。

```bash
# 1) 通道自检（绕开 cron 和脚本，直接发一句）
hermes -p <profile> send --to clawchat "通道自检"
```

- **自检也收不到** → 通道问题，往下看。
- **自检能收到，脚本收不到** → 脚本问题：看 `agent/config.json` 的 `push.clawchat.profile` 是否等于你实际在用的 profile 名。

通道排查：

1. 看日志：`$HERMES_HOME/profiles/<profile>/logs/*.log`，搜 `standalone send ok` 和 `ack_received`。
2. 有 `send ok` 没 `ack_received` → 连接没进 `state=ready`（ClawChat 掉线/没登录）。重启 Hermes 或重新激活该 agent。
3. 连 `send ok` 都没有 → `hermes_home` 填错了，脚本调的是另一个 Hermes 实例。

> 重要：`{"ok": true}` 只代表「脚本没抛异常」，**不代表送出去了**。这正是 `push_signals.py` 必须回 `{"ok":true,"sent":true}` 才算成功的原因——改代码时别把这个语义改掉。

---

## 每半小时都被唤醒，但没有内容

**症状**：cron 每轮都跑，手机没消息，账上 token 在掉。

这是 wake gate 失效——最贵的那个 bug。

```bash
# 直接看脚本末行
bash agent/run_scan.sh intraday --wake-gate | tail -1
# 期望：{"wakeAgent": false}（没有候选时）
```

- 没有这一行 → `chanlun_prep.py` 的 `--wake-gate` 分支被改动过。
- 有 `{"wakeAgent": true}` 但确实没候选 → 门闸条件写错了（应该只在有新候选/有进展时才 true）。

改完之后 `hermes -p <profile> cron doctor` 看有没有其他常见病。

---

## 状态与去重

**为什么同一只票昨天说过今天还说？**
状态变了就该再说一次（`候选 → 已确认` 是重要变化）。真的重复刷屏时看 `state/seen.json` 的键 `代码|类型|端点时间|状态`——端点时间不同就是不同信号，属正常。

**想让它重新推一遍历史信号？**
删掉 `agent/state/seen.json`（或只删对应代码的行）。下一轮会当成新信号。

**`信息不足` 是什么意思？**
数据不够或结构有二义，判断不了。**这是合法结果，不是失败。** 宁可写「信息不足」也不许编一个结论——这是状态机存在的意义。

---

## 成本 / token

| 场景 | 大致消耗 |
|---|---|
| wake gate 掐掉的一轮 | 0（不调模型） |
| 有 1~3 只候选的一轮 | 3~4 万 token |
| 收盘全览（10 只票逐只复核） | 8~15 万 token |

省法：`--wake-gate` 别关；技能只预加载 `chanlun-live-scan`（`--skill` 只写这一个），其余让模型按需 `skill_view`——全预加载每轮多烧几万。

---

## 能不能改成别的市场 / 别的理论

- **别的市场**：动 `tdx/tdxlive.py` 的 `get_bars()`。美股/期货只要给出 `(datetime, open, high, low, close, volume)` 就都能算结构。
- **别的理论**：`chan_structure.py` 是纯函数（输入 K 线、输出分型/笔/中枢），可以单独拿去接你自己的想法。`chanlun_prep.py` 和 `push_signals.py` 都不用改。
- **别的推送**：`push.channel` 加一个分支即可，`push_signals.py` 里 channel 是分派点。

---

## 安全与隐私

- `.gitignore` 已排除 `.env`、`state/`、`out/`、`memories/MEMORY.md`、`USER.md`、`owner.md`。
- **`agent/config.json` 是会被提交的模板**，里面不要填 webhook、token、你自己的 `hermes_home` 真实用户名（`C:/Users/xxx`）。要填走 `.env`。
- 推送内容会经过 Hermes 的模型调用，**别把持仓截图丢进去**——模型只该看到结构 JSON。

---

## 还是不行

开 Issue 时贴：

1. 报错原文（文字，不是截图）
2. `python --version`、`hermes --version`
3. `bash agent/run_scan.sh intraday --force` 输出的**前 40 行**（JSON 可以截断，但保留 `trading` 和 `stocks` 字段）
4. 系统版本、通达信安装路径（`C:\new_tdx64` 这类即可）

**不要贴**：`.env`、`config.json` 里的 `hermes_home`、`owner.md`、任何 webhook 或 token。