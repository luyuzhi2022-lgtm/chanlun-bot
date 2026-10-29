---
name: tdx-realtime-quotes
description: Use when pulling live 通达信/TDX A-share quotes or K lines.
tags: [tdx, 通达信, realtime, quotes, kline, data-source]
related_skills: [chanlun-structure-parser, chanlun-zhongshu-mapper]
---

# 通达信实时行情接入

## When to Use

- 要拿 A 股盘中实时价格/五档/分钟K线，或做全市场扫描
- 用了 pytdx / mootdx 拿不到数据（返回空）
- 想把行情接给缠论技能做盘中结构判断

不适用于：要 Level-2 十档/逐笔（需付费账号）、港股美股、期货主力连续。

本机可用实现：`E:\Hermes\tdx-live\`（`tdxlive.py` 核心库、`watch.py` CLI、`tdx_tq_strategy.py` 客户端策略）。
虚拟环境：`E:\Hermes\tdx-live\.venv`（Python 3.14.7 + tdxdata/pytdx/mootdx/pandas）。
通达信客户端：`C:\new_tdx64`。

## 先判断用哪条路

| 需求 | 走哪条 | 说明 |
|---|---|---|
| 盘中实时快照 / 分钟K线 / 全市场扫描，不想开客户端 | **主站新协议（tdxlive.py）** | 单只 78ms；全市场 1.17 万只 61 秒 |
| 毫秒级推送、要调通达信公式、要把预警弹回客户端 | **客户端 TQ 接口（tqcenter）** | 必须先开客户端并登录 |
| 历史日线/分钟线，盘后分析 | **mootdx Reader 读 vipdoc** | 只到最近一次「盘后数据下载」 |
| 看到 pytdx/mootdx 在线接口返回空 | 正常，别再排查 | 2026-09-10 起协议已改，见下 |

## 铁律

1. **pytdx / mootdx 的在线行情已失效。** 症状：TCP 7709 能连通，但 `get_security_quotes` /
   `get_security_bars` 返回 `[]`（不是报错）。别在网络、IP、超时上浪费时间。
2. **主站清单取本地客户端的 `C:\new_tdx64\connect.cfg`**（正则抓 IP），比库内置 IP 池靠谱得多。
   本机 73 个里 45 个可用，最快 ~15ms。写完先并发探活再连。
3. **批量报价服务端不认**：`build_quotes_req` 传多只只会得到空数组。只能单只循环 + 多连接并发。
4. **读响应必须用 select + 静默窗口**，不要用固定 recv 超时。tdxdata 原版固定 3s 窗口
   → 单只报价 4026ms；改成「有数据就收、静默 50ms 认为读完」→ **78ms**（50 倍差距）。
5. **K 线响应可能 zlib 压缩且跨多个 TCP 段**：压缩时保留帧头、对 `78 9c` 之后解压；
   读完的判据是「解压后 `buf[16:18]` 的 ret_count >= 请求 count」，不是固定静默时间
   （count=240 时固定 0.12s 静默会截断 → 解析出 0 条）。
6. **新协议里 vol / amount 是 f32**（老 pytdx 按 u32 解会得到天文数字）；
   请求里的版本标识要用 `18 7b 00 01` 而不是老 pytdx 的 `08 64 01 01`。
7. **市场号不能只看首字母**：`5`/`6`/`9` 开头→1（沪，含 510300 ETF、999999 指数）；
   `88` 开头（板块指数 880xxx）→1；`43/83/87/92` 开头（北交所）→2；
   `000300`/`000905` 等沪深指数是 1，但 `000001` 是深市平安银行 0 —— 指数要显式传 market。
8. **`snap()` 不是完整分时**（实测一盘只 62 个点且含无效填充）。要完整分钟序列用
   `bars(code, '1m', 240)`。
9. **自选股 `T0002/blocknew/zxg.blk` 每行 7 字符 = 1 位市场号 + 6 位代码**
   （`1600089` 是 600089、`1999999` 是 999999）。别用 6 位正则直接切。
10. 免费主站只有 **Level-1（五档）**；十档/逐笔要账号买了 L2。
11. **指数 K 线记录比个股多 4 字节**：vol/amount 之后还有 `u16 涨家数 + u16 跌家数`。
    用个股布局解指数只会正确解出第 1 根、后面全部错位（会读到 `2004-01-97 35:45` 这类日期）。
    必须按 `is_index_code()` 切到 index=True 布局；解对了还能顺带拿到涨跌家数。
    指数：`999xxx`（上证）、`399xxx`（深证成指/创业板指）、`88xxxx`（板块指数）、以及 SH_INDEX_CODES 里的 `000xxx`。

12. **`bars()` 返回字段是 `datetime / open / high / low / close / vol / amount`**（`datetime` 形如
    `2026-10-21 15:00`，日线是 `2026-10-21 00:00`），**没有 `t/c/h/l` 简写**——探针里按 `t`/`c` 取会
    直接 KeyError，先 `print(bars[-1])` 看一次键名再写。
13. **个股不要显式传 `market`**：`bars(code, period, count)` 默认 `infer_market(code)` 是对的；
    给沪市代码（688xxx/600xxx）传 `market=0` **不报错、静默返回 0 根**，很容易误判成「数据源挂了」。
    只有指数（999xxx/399xxx/88xxxx 及 SH_INDEX_CODES 的 000xxx）才需要显式 `market=1`（见第 7 条）。

## 握手时序（写自己的客户端时照抄，无歧义）

```
C02 = bytes.fromhex("0c0218940001030003000d0001")                     # init，服务器不回包
C03 = bytes.fromhex("0c031899000120002000db0f7464786c6576656c"
                    "000000295cf740110000000000000000000000000005")   # tdxlevel 认证，必须在 C02 之后
```
顺序发 C02 → C03，中间的等待时间不影响结果（0ms~600ms 组合实测都成功）；认证回包 195B。
先发包时不 sleep 也可以，但**必须把 C03 的 195B 收干再发数据请求**，否则连接会被服务器重置。

## 分时快照请求

```
SN = 0c090041030027002700d10f + [market, 0] + code(6字节ASCII) + 后续29B固定后缀
```
返回体从偏移 50 开始是 `float32` 价格序列（越界值占 2 字节，需要按 0.1<v<100000 跳读）。

## 客户端 TQ 接口（路线 B）

`C:\new_tdx64\PYPlugins\` 是通达信自带官方 Python 接口；`sys/tqcenter.py` 是 API，
`user/tdxdata_test.py` 是官方示例（所有接口的用法都注释在里面，改需求先读它）。

- 外部 Python 可以 `ctypes.CDLL('PYPlugins/TPythClient.dll')` 并 `import tqcenter`（需要 numpy/pandas）。
- `tq.initialize(__file__)` 在客户端没开时报「初始化错误：请确认是否打开通达信客户端」。
  **唯一前提就是客户端开着并登录**；轮询式用法不需要把脚本注册进客户端。
- 推送式订阅只有 ≤100 只：`tq.subscribe_hq(stock_list, callback)`，回调签名必须是 `on_data(datas)`，
  回调里再取 `tq.get_market_snapshot(code)`。
- 现成脚本：`E:\Hermes\tdx-live\tdx_tq_strategy.py` → 复制到 `PYPlugins\user\` 运行。

## 常用命令

```bash
cd E:\Hermes\tdx-live
.venv\Scripts\python.exe watch.py scan                              # 主站测速
.venv\Scripts\python.exe watch.py quote 600519 000001 -v            # 实时报价+五档
.venv\Scripts\python.exe watch.py bars 600519 --period 1m --count 240
.venv\Scripts\python.exe watch.py collect --interval 3              # 盘中采自选股
.venv\Scripts\python.exe watch.py market --conn 16                  # 全市场快照
```

`collect` 的产物 `data/latest.json` 是喂给缠论分析的接口（每只票的价/涨跌幅/量额/五档）。
需要 1 分钟级别走势时读 `data/bars1m_YYYYMMDD.csv`。

## 与缠论分析的衔接

`data/latest.json`（快照） + `data/bars1m_??????.csv`（分钟线）足够支撑
`chanlun-structure-parser` / `chanlun-zhongshu-mapper` 等技能对盘中走势做分型/笔/中枢判断。
要跨级别（30 分钟、日线）用 `tdxlive.TdxLive().bars(code, '30m', 240)` 取，不必等 CSV 累积。
