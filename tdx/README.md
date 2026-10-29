# 通达信实时行情接入（本机实测可用方案）

> 结论先说：**pytdx / mootdx 的在线行情已经不能用**（2026-09-10 通达信服务端改协议）。
> 现在本机能实时拿到通达信行情的是下面这条路，实测 **单只 78ms**、**全市场 11700 只 61 秒**。

目录：`E:\Hermes\tdx-live\`　虚拟环境：`.venv`（Python 3.14.7）

---

## 一、三条路，各管一段

| 路线 | 数据源 | 是否要开客户端 | 延迟 | 用途 | 本机状态 |
|---|---|---|---|---|---|
| **A. 连行情主站（新协议）** | 通达信服务器 TCP 7709 | 不用 | 实测 **78ms/只**；全市场 61s | 盘中实时快照、分钟K线、全市场扫描 | ✅ 已跑通 |
| **B. 客户端 TQ 接口** | 你本机通达信客户端内存 | **必须开且登录** | 推送式，最低 | 真实时订阅（≤100只）、调用通达信公式、预警回弹 | ⚠️ 需客户端运行才能验证 |
| **C. 读本地 vipdoc** | 客户端落地文件 | 不用 | 盘后数据 | 历史日线/分钟线（本机 6015 根日线） | ✅ 已跑通，但数据只到 10/03 |
| ~~pytdx / mootdx 在线~~ | — | — | — | — | ❌ **已失效** |

---

## 二、为什么 pytdx 不能用了（实测证据）

```
$ python -c "from pytdx.hq import TdxHq_API; ..."      # 对 5 个主站、逐个命令测
101.35.121.35    quotes=0 日线bars=0 1min=0     ← TCP 能连上，数据请求返回空
122.51.120.217   quotes=0 日线bars=0 1min=0
123.60.73.44     quotes=0 日线bars=0 1min=0
```
- TCP 握手正常（端口 7709 通），但老命令一个字节数据都不返回 → 不是网络问题，是协议变了。
- mootdx 同样报 `ValueError: not enough values to unpack (expected 2, got 0)`。
- 新协议变化：**版本标识 + 命令族 + zlib 流式压缩**；老 pytdx 的 K线/报价命令把版本标识
  换成 `18 7b 00 01` 就能复用，且响应里 **vol / amount 从 u32 变成了 f32**（不改会读出天文数字）。

---

## 三、快速上手（路线 A）

```bash
cd E:\Hermes\tdx-live
.venv\Scripts\python.exe watch.py scan                        # 测主站速度（本机 73 个里 45 个可用）
.venv\Scripts\python.exe watch.py watchlist                   # 读通达信客户端的自选股
.venv\Scripts\python.exe watch.py quote 600519 000001 -v      # 实时报价含五档
.venv\Scripts\python.exe watch.py bars 600519 --period 1m --count 240   # 导出分钟K线 CSV
.venv\Scripts\python.exe watch.py collect --interval 3        # 盘中轮询采集（默认自选股）
.venv\Scripts\python.exe watch.py market --conn 16            # 全市场快照
```

盘中开盘时直接打开一个窗口跑 `collect` 即可，输出在 `./data/`：

```
data/
  ticks_20261016.csv     每次轮询一行的快照（价/涨跌幅/量额/五档）
  bars1m_20261016.csv    每根 1 分钟K线收盘后追加（喂缠论用）
  latest.json            最新快照汇总（外部程序/缠论分析直接读这个）
  market_*.csv           全市场快照
```

`latest.json` 结构：

```json
{"updated":"2026-10-16 09:31:03","trading":true,
 "stocks":{"600519":{"price":1312.97,"change_pct":2.1,"last_close":1286.0,
                     "open":1286.0,"high":1317.76,"low":1282.0,
                     "vol":5767700,"amount":7537705984.0,
                     "bid":[1312.31,...],"ask":[...],"bid_vol":[...],"ask_vol":[...]}}}
```

Python 里直接调：

```python
from tdxlive import TdxLive, TdxPool, read_watchlist

c = TdxLive().connect()            # 自动从 connect.cfg 挑最快主站
c.quote('600519')                  # 盘口（含五档）
c.bars('600519', '1m', 240)        # 当日 1 分钟 K 线
c.bars('600519', 'day', 200)       # 日线
c.close()

# 多路并发：自选股 30 只刷新一次 < 0.3 秒
with TdxPool(n=8) as p:
    prices = p.batch(read_watchlist(), lambda c, code: c.quote(code))
```

---

## 四、实测性能数据（2026-10-16 凌晨，非交易时段取上一交易日收盘数据）

| 项目 | 结果 |
|---|---|
| 主站来源 | 本机 `C:\new_tdx64\connect.cfg`，73 个 IP，**45 个可用** |
| 最快主站 | 101.35.121.35 / 123.60.73.44 ≈ **13–15 ms** |
| 单只 quote（含五档） | **78.3 ms** 平均（连续 30 次 0 失败） |
| 8 路并发 16 只 | **157 ms**（≈102 只/秒） |
| 16 路并发全市场 11727 个代码 | **61.3 s**，成功 9100 只（≈148 只/秒） |
| 各周期 K 线 | 1m/5m/15m/30m/60m/日/周 全部正常，数据到 2026-10-15 收盘；指数（999999/399xxx/880xxx）同样可解，且额外带 `up_count/down_count` 涨跌家数 |

关键点：**78ms 不是服务器慢，是库的读循环问题**。原始 tdxdata 用固定 3 秒 recv 窗口，
`TdxClient()` 单只报价要 **4026 ms**；改成 `select` + “静默 50ms 就认为读完”后降到 **78ms**（50 倍）。
K 线响应大且 zlib 压缩、分多个 TCP 段到达，必须按“条数到齐”判定读完，不能用固定静默（否则解析出 0 条）。

---

## 五、路线 B：客户端 TQ 接口（最实时，官方）

你机器上 `C:\new_tdx64\PYPlugins\` 就是通达信自带的 Python 接口：

```
PYPlugins/sys/tqcenter.py       官方 API（版本 1.0.16 / 2026-07-24）
PYPlugins/TPythClient.dll       与客户端通信的 DLL
PYPlugins/user/tdxdata_test.py  官方示例（各种接口的用法都注释在里面）
```

实测：外部 Python 能 `ctypes.CDLL` 加载该 DLL、能 `import tqcenter`；
客户端没开时 `tq.initialize()` 明确报 **「初始化错误：请确认是否打开通达信客户端」**
—— 说明这条路唯一的前提就是把客户端开起来并登录。

能做什么（都写在官方示例里）：

```python
from tqcenter import tq
tq.initialize(__file__)

tq.get_stock_list('0', list_type=0)          # 自选股（5=所有A股，1=持仓股）
tq.get_market_snapshot('600519.SH')          # 实时快照（曾用名 get_full_tick / get_report_data）
tq.get_market_data(stock_list=['600519.SH'], period='1m', count=240)
tq.subscribe_hq(stock_list=[...], callback=on_data)   # 推送式订阅，上限 100 只 ← 真实时
tq.formula_zb(formula_name='MACD', formula_arg='12,26,9')  # 直接调通达信公式
tq.send_warn(stock_list=[...], ...)          # 把预警弹回客户端界面
tq.send_user_block(block_code='ZXG', stock_list=[...])     # 写回自选股
```

已经给你写好一个现成的采集策略：**`tdx_tq_strategy.py`**。
复制到 `C:\new_tdx64\PYPlugins\user\`，在客户端「Python 策略」里添加运行，
它会把推送实时写成 `data/tq/tq_ticks_YYYYMMDD.jsonl` 和 `tq_latest.json`，外部程序直接读。

---

## 六、路线 C：读本地 vipdoc

```python
from mootdx.reader import Reader
r = Reader.factory(market='std', tdxdir=r'C:\new_tdx64')
r.daily(symbol='600519')     # 6015 根日线
r.minute(symbol='600519')    # 分钟线（本机 minline 目录是空的，需先在客户端「盘后数据下载」）
```
本机现状：`vipdoc/sh/lday` 6043 个文件、`sz/lday` 5890 个，但最后更新是 **10月3日**；
`vipdoc/sh/minline` 为空。→ 盘中不实时，盘前记得在客户端做一次「盘后数据下载」。

---

## 七、踩坑清单（都是本机实测踩出来的）

1. **pytdx / mootdx 在线接口已废**，别再花时间；看到 “TCP 连得上但返回 0 条”就是这个原因。
2. **`connect.cfg` 就是主站清单**，直接用它比用库内置 IP 池靠谱（内置的多数已下线）。
3. **批量报价（一次请求多只）服务端不认**，只返回空 → 必须单只循环 + 多连接并发。
4. **延迟取决于读循环，不取决于服务器**：`select` + 静默 50ms = 78ms；固定 3s recv 窗口 = 4026ms。
5. **K 线响应可能 zlib 压缩**（响应体里找 `78 9c`，前面的帧头要保留），且 count 大时分多段到达，
   要按 `帧头16B + ret_count` 判定读完整，否则解析出 0 条。
6. **新协议 vol / amount 是 f32**，老 pytdx 按 u32 解会得到天文数字。
7. **市场号不能只看首字母**：`510300` 是沪市(1)、`880660` 板块指数是沪市(1)、
   `000300` 沪深300 指数是沪市(1) 而 `000001` 是深市平安银行(0)，北交所 43/83/87/92 开头是 2。
7b. **指数 K 线记录比个股多 4 字节**：在 vol/amount 之后还有 `u16 涨家数 + u16 跌家数`。
   用个股的布局解指数，只会正确解出第 1 根、后面全部错位（会读到 2004-01-97 这种日期）。
   `tdxlive.py` 里按 `is_index_code()` 自动切布局（`parse_bars(..., index=True)`），
   指数日线因此能拿到涨跌家数：`2026-10-15 上证指数 190涨/2148跌`。
8. **`snap()` 的分时不是完整当日分时**（实测一盘只 62 个点，且含无效填充），
   要完整分钟序列请用 `bars(code, '1m', 240)`。
9. **自选股文件 `T0002/blocknew/zxg.blk` 每行 7 字符 = 1 位市场号 + 6 位代码**
   （如 `1600089` 表示 600089、`1999999` 表示 999999 上证指数），按 6 位正则切会切错。
10. 免费主站行情是 **Level-1（五档）**；`l2plugin.cfg` 里 `HasLevel2Engine=1`，但十档/逐笔要看账号是否买了 L2。
