# 缠论结构示意图：生成、自检、交付

用户口径是「**分析过程不简化，结果简单点表达，最好能出个示意图**」。图承担细节（中枢、笔、背驰段、四态买卖点、关键位），
正文只给结论与成立/失效条件——不要把过程里的数字再抄一遍进正文。

## 何时出图

- 用户要求“示意图 / 画出来 / 标一下买卖点中枢背驰段”。
- 收盘复盘同时覆盖 ≥2 只标的、且结论依赖多级别（30m + 5m）结构。
- 只做 **30m（观察级别）+ 5m（区间套与回试）+ 指数背景**，不要堆 1m 面板；1m 只在正文里报回抽低点。

## 两步流水线

**1) 一次取数落盘**（后续改图不再连 tdx）：`scripts/chan_map_svg.py fetch`

```bash
E:/Hermes/tdx-live/.venv/Scripts/python.exe scripts/chan_map_svg.py fetch \
  --codes 688270 001270 --index 880548 --out E:/Hermes/chanlun-agent/out/map_data_<日期>.json
```

产物每级别带：`bars[t,o,h,l,c,v]`、`bi[i,dir,t0,p0,t1,p1,cf,macd{pos,neg,abs}]`、
`zs[ZD,ZG,DD,GG,DD/GG,seg_count,extended,state,level_up_hint,t_start,t_end,bi_start,bi_end,i0,i1]`、`trend`、`last_macd`。
力度用 `cs.bi_macd_area(b, hist, dif)`，结构用 `cs.analyze(bars, min_gap=4)`，MACD 用 `cs.macd(closes)`。

**2) 出图**：`scripts/chan_map_svg.py render`，输入 data JSON + spec JSON（spec 只写“标什么”，画法固定在脚本里）。

## 画法（已定型，不要每轮重新发明）

- 铅笔灰折线 1.2px；中枢：核心区 `ZG..ZD` 实线填充 `rgba(120,140,210,.24)`，波动区 `GG..DD` 细虚线框。
- **中枢标签写在框外上方**，箱宽 < 46px 不标——写在框内会压 K 线和笔。
- 背驰对比固定双色：**A 段蓝 `#2f5fa8`、C 段橙 `#e8710a`**，标签写 `p0→p1 |neg| 数值`；正文用同样的“蓝 A / 橙 C”称呼，读者才对得上。
- 标记五类：★候选（金 `#c8901a`）/ ✕已失效（灰+删除线）/ ○关键观察位（蓝）/ ▲ 已确认买 / ▼ 已确认卖。
- 关键位：`ZG`、`ZD` 水平虚线 + 右端标签；`Z` 中轴单列一条；右侧价格刻度 **4 档**（5 档必挤）。
- 面板尺寸：30m 高 320、5m 高 240、指数 190，宽 1000；面板左对齐靠内容撑宽，不套居中容器（预览框按首个内容宽度测量）。
- 面板下方一行 `extra_reads` 承载背驰比值、级别二义、Zn 序列——这些数字只出现在图上，正文不再重复。
- 收尾一张状态表：行 = 标的×级别，列 = 一买/二买/三买/三卖 + 关键价位（成立/失效）。

## 自检（必须做，不能只看代码跑通）

1. **先数值自检**：打印每段 `p0,p1,|neg|,ratio`，断言 `abs(neg) > 0`；图里和正文里的数字必须同源。
2. **无头 Edge 截图**（本机有 Edge，`--headless=new` 不碰登录态）：

```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --hide-scrollbars --screenshot="E:/Hermes/chanlun-agent/out/map_shot.png" \
  --window-size=1024,2350 "file:///E:/Hermes/chanlun-agent/out/chan_map.html"
```

3. `vision_analyze` 检查，但**整页 1024×2300 会超时**：用 `region=[x1,y1,x2,y2]` 逐面板裁图问，**一次只问一件事**（标签是否压 K 线 / A 段是否画对 / 表格是否溢出）。裁图跨面板时，会看到下一个面板的说明文字，别被误导。
4. 别让本地浏览器会话参与：`browser_exec(local=True)` 需要关闭用户正在用的 Edge 配置，直接走无头截图更快。

## 交付

- 桌面聊天：`::preview{file="E:/.../chan_map.html"}` 单独一行，正文放在图前（先结论后图）。
- 另存 PNG 供手机查看/存档，路径写在正文末行；原始数据 JSON 也一起报路径，便于下一轮复用。
- 图里的状态词只能是 `候选 / 已确认 / 已失效 / 信息不足`；“待确认”“观察中”写进括号，不另立状态。

## 坑

- **笔索引不能跨批次复用**：同一票取 320 根 vs 400 根，`bi` 下标整体平移（30m 同一段在 320 根序列是 bi14，在 400 根里是 bi20）。
  定位背驰段一律**按端点价 (p0, p1) 搜索**，不要沿用旧下标——画错段时若该段 `|neg|=0`，面积比还会直接 ZeroDivisionError。
- **窗口起点用 `len(bars) - n`**，不要按日期过滤：停牌、半天交易日、集合竞价缺失都会让日期窗口错位。
- **中枢/笔的绘制层序**：中枢矩形在 K 线之下画（先画框、后画 K 线），否则半透明填充会盖住实体。
- 一只票的 30m 与 5m 面板上下相邻，5m 的 `active_zs` 用**最后一个中枢**的下标而不是固定值——中枢数量随取数根数变化。
