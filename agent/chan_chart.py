# -*- coding: utf-8 -*-
"""生成 688270 缠论结构图（自包含 HTML，无外部依赖）。
锁定原文口径：ZG=min(高A,高C)、ZD=max(低A,低C)  —— A、C 为与中枢形成方向一致的段（前两个 Z 段）。
"""
import json
import os
import sys

sys.path[:0] = [
    os.environ.get("CHANLUN_TDX_LIVE", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tdx")),
    os.path.dirname(os.path.abspath(__file__)),
]
import chan_structure as cs  # noqa: E402
from tdxlive import TdxLive  # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "688270"
NAME = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", f"{CODE}_chan.html")
WIN = {"day": 75, "30m": 168}

c = TdxLive().connect()
q = c.quote(CODE)

data = {
    "code": CODE, "name": NAME,
    "date": "2026-10-15", "close": q["price"], "chg": q["change_pct"],
    "convention": "原文口径 ch20：ZG=min(高A,高C)、ZD=max(低A,低C)（A、C=前两个 Z 段，与中枢形成方向一致）",
    "levels": {},
    "markers": [
        {"panel": "day", "t": "2026-08-18", "p": 71.53, "label": "三卖 71.53", "kind": "sell"},
        {"panel": "day", "t": "2026-09-14", "p": 52.11, "label": "一买候选 52.11 已失效", "kind": "dead"},
        {"panel": "30m", "t": "2026-10-13 14:00", "p": 49.88, "label": "三卖 49.88", "kind": "sell"},
        {"panel": "30m", "t": "2026-09-16 10:00", "p": 58.80, "label": "中枢上沿波动 GG 58.70", "kind": "note"},
    ],
}

for period, cnt in (("day", 300), ("30m", 320)):
    bars = c.bars(CODE, period, cnt)
    s = cs.analyze(bars, min_gap=4)
    full_idx = {b["datetime"]: i for i, b in enumerate(bars)}
    n = WIN[period]
    off = max(0, len(bars) - n)
    win = bars[off:]
    zs_out = []
    for z in s["zs"]:
        if z["bi_end"] is None:
            continue
        i0 = full_idx.get(z["t_start"])
        i1 = full_idx.get(z["t_end"])
        if i0 is None or i1 is None or i1 < off:
            continue
        zs_out.append({k: z[k] for k in ("ZD", "ZG", "GG", "DD", "t_start", "t_end",
                                        "seg_count", "state", "level_up_hint")}
                      | {"i0": i0 - off, "i1": i1 - off})
    bi_out = []
    for b in s["bi"]:
        i0, i1 = full_idx.get(b["t0"]), full_idx.get(b["t1"])
        if i0 is None or i1 is None or i1 < off:
            continue
        bi_out.append({"i0": i0 - off, "i1": i1 - off, "p0": b["p0"], "p1": b["p1"],
                       "dir": b["dir"], "t0": b["t0"], "t1": b["t1"],
                       "cf": b["dt_confirm"]})
    data["levels"][period] = {
        "step": period,
        "bars": [{"t": b["datetime"], "o": b["open"], "h": b["high"],
                  "l": b["low"], "c": b["close"]} for b in win],
        "bis": bi_out, "zs": zs_out,
        "last_zs": ({k: s["zs"][-1][k] for k in ("ZD", "ZG", "GG", "DD", "state")}
                    if s["zs"] else None),
        "trend": cs.zhongshu_state(s["zs"]),
    }
c.close()

TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  .wrap{display:flex;flex-direction:column;gap:12px;width:1010px;}
  .head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;}
  .ttl{font-size:16px;font-weight:600;}
  .price{font-size:15px;font-weight:600;color:#e2493f;}
  .sub{font-size:11.5px;color:var(--muted-foreground);}
  .panel{border:1px solid var(--border);border-radius:9px;padding:8px 10px 6px;}
  .ptitle{font-size:12.5px;font-weight:600;margin:0 0 2px;display:flex;gap:8px;align-items:baseline;}
  .ptitle small{font-weight:400;color:var(--muted-foreground);}
  .legend{font-size:11px;color:var(--muted-foreground);display:flex;gap:14px;flex-wrap:wrap;padding:2px 0 4px;}
  .legend i{display:inline-block;width:14px;height:8px;border-radius:2px;vertical-align:middle;margin-right:4px;}
  table{border-collapse:collapse;font-size:12px;}
  th,td{border:1px solid var(--border);padding:5px 9px;text-align:center;}
  th{color:var(--muted-foreground);font-weight:500;}
  td.lv{font-weight:600;text-align:left;}
  .ok{color:#0e9a63;font-weight:600;}
  .bad{color:#e2493f;font-weight:600;}
  .dead{color:var(--muted-foreground);text-decoration:line-through;}
  .none{color:var(--muted-foreground);}
  .warn{color:#e8710a;font-weight:600;}
  .ladder{display:flex;gap:6px;flex-wrap:wrap;font-size:11px;}
  .chip{border:1px solid var(--border);border-radius:6px;padding:3px 8px;}
  .chip b{font-weight:600;}
  .site{font-size:11px;color:var(--muted-foreground);}
</style></head>
<body><div class="wrap" id="wrap"></div>
<script>const DATA = __DATA__; __JS__</script></body></html>
"""

JS = r"""
const RED="#e2493f", GRN="#0e9a63", ORG="#e8710a", GRY="rgba(128,128,128,.85)";
const W=988, H=380, PL=8, PR=58, PT=16, PB=26;
const UP=RED, DN=GRN;              // A股惯例：红涨绿跌

function el(tag, attrs, inner){
  const a = Object.entries(attrs||{}).map(([k,v])=>k+'="'+v+'"').join(' ');
  return '<'+tag+(a?' '+a:'')+'>'+(inner||'')+'</'+tag+'>';
}

function chart(node, lv){
  const bars = lv.bars, N = bars.length;
  const lo = Math.min(...bars.map(b=>b.l)), hi = Math.max(...bars.map(b=>b.h));
  const pad = (hi-lo)*0.06, mn = lo-pad, mx = hi+pad;
  const step = (W-PL-PR)/N;
  const x = i => PL + (i+0.5)*step;
  const y = p => PT + (mx-p)/(mx-mn)*(H-PT-PB);
  let s = '';

  // 横网格 + 价位
  for(let k=0;k<=5;k++){
    const p = mn + (mx-mn)*k/5, yy=y(p);
    s += el('line',{x1:PL,y1:yy.toFixed(1),x2:W-PR,y2:yy.toFixed(1),
                    stroke:'var(--border)','stroke-width':1,'stroke-dasharray':'3 4'});
    s += el('text',{x:W-PR+5,y:(yy+3.5).toFixed(1),fill:'var(--muted-foreground)','font-size':10},p.toFixed(2));
  }

  // 中枢：先画外围波动 [DD,GG]（虚线），再画核心 [ZD,ZG]（实底）
  lv.zs.forEach(z=>{
    if (z.DD > mx || z.GG < mn) return;          // 整段在可视区间外，跳过
    const x0=x(Math.max(0,z.i0)), x1=x(Math.min(N-1,z.i1));
    s += el('rect',{x:x0.toFixed(1),y:y(z.GG).toFixed(1),width:(x1-x0).toFixed(1),
                    height:Math.abs(y(z.DD)-y(z.GG)).toFixed(1),fill:'none',
                    stroke:'var(--muted-foreground)','stroke-width':1,'stroke-dasharray':'4 3','opacity':.75});
    s += el('rect',{x:x0.toFixed(1),y:y(z.ZG).toFixed(1),width:(x1-x0).toFixed(1),
                    height:Math.abs(y(z.ZD)-y(z.ZG)).toFixed(1),fill:'rgba(120,140,210,.22)',
                    stroke:'#6d86c8','stroke-width':1.1});
    s += el('text',{x:(x0+4).toFixed(1),y:(y(z.ZG)+11).toFixed(1),fill:'#6d86c8','font-size':10},
            '[ZD '+z.ZD+', ZG '+z.ZG+']'+(z.state==='离开'?' 离开':' '+z.state));
  });

  // 最后一中枢的 ZD/ZG 延伸到右边缘（当下位置基准），标签靠右轴，避免压住中枢标签
  const lz = lv.last_zs;
  if(lz){
    [[lz.ZD,'ZD '+lz.ZD],[lz.ZG,'ZG '+lz.ZG]].forEach(([p,txt])=>{
      s += el('line',{x1:PL,y1:y(p).toFixed(1),x2:W-PR,y2:y(p).toFixed(1),stroke:'#6d86c8',
                      'stroke-width':1,'stroke-dasharray':'7 4','opacity':.55});
      s += el('text',{x:W-PR-4,y:(y(p)-3).toFixed(1),fill:'#6d86c8','font-size':10,
                      'text-anchor':'end'},txt);
    });
  }

  // K线
  const bw = Math.max(1.2, step*0.62);
  bars.forEach((b,i)=>{
    const up = b.c>=b.o, col = up?UP:DN, cx=x(i);
    s += el('line',{x1:cx.toFixed(1),y1:y(b.h).toFixed(1),x2:cx.toFixed(1),y2:y(b.l).toFixed(1),
                    stroke:col,'stroke-width':1});
    const y0=y(Math.max(b.o,b.c)), y1=y(Math.min(b.o,b.c));
    s += el('rect',{x:(cx-bw/2).toFixed(1),y:y0.toFixed(1),width:bw.toFixed(1),
                    height:Math.max(1,(y1-y0)).toFixed(1),fill:up?'none':col,stroke:col,'stroke-width':1});
  });

  // 笔（折线）
  lv.bis.forEach(b=>{
    s += el('line',{x1:x(Math.max(0,b.i0)).toFixed(1),y1:y(b.p0).toFixed(1),
                    x2:x(Math.min(N-1,b.i1)).toFixed(1),y2:y(b.p1).toFixed(1),
                    stroke:'rgba(128,128,128,.9)','stroke-width':1.25});
  });

  // 买卖点标记
  DATA.markers.filter(m=>m.panel===lv.step).forEach(m=>{
    const key = lv.step==='day' ? m.t.slice(0,10) : m.t;
    const i = bars.findIndex(b=>b.t===key || b.t.slice(0,10)===key.slice(0,10) && lv.step==='day');
    if(i<0) return;
    let col = ORG, mark='▼';
    if(m.kind==='dead'){ col=GRY; mark='✕'; }
    if(m.kind==='note'){ col='#6d86c8'; mark='◇'; }
    const dy = m.kind==='sell' ? -9 : (m.kind==='note' ? 17 : 17);
    const ty = m.kind==='sell' ? -12 : (m.kind==='note' ? -8 : 27);
    s += el('text',{x:x(i).toFixed(1),y:(y(m.p)+dy).toFixed(1),fill:col,'font-size':11,
                    'text-anchor':'middle'}, mark);
    s += el('text',{x:Math.min(W-PR-4, x(i)+6).toFixed(1),y:(y(m.p)+ty).toFixed(1),fill:col,
                    'font-size':10.5}, m.label);
  });

  // 现价线
  s += el('line',{x1:PL,y1:y(DATA.close).toFixed(1),x2:W-PR,y2:y(DATA.close).toFixed(1),
                  stroke:'var(--accent)','stroke-width':1.2,'stroke-dasharray':'2 3'});
  s += el('text',{x:PL+2,y:(y(DATA.close)+12).toFixed(1),fill:'var(--accent)','font-size':10.5},
          '现价 '+DATA.close+' ('+DATA.chg+'%)');

  // 时间轴
  const tickEvery = Math.ceil(N/7);
  for(let i=0;i<N;i+=tickEvery){
    s += el('text',{x:x(i).toFixed(1),y:H-8,fill:'var(--muted-foreground)','font-size':10,
                    'text-anchor':'middle'}, bars[i].t.slice(5,10));
  }
  return el('svg',{width:W,height:H,viewBox:'0 0 '+W+' '+H},s);
}

const wrap = document.getElementById('wrap');
const head = '<div class="head"><span class="ttl">'+DATA.code+' '+DATA.name+'</span>'
  +'<span class="price">'+DATA.close+' ('+DATA.chg+'%)</span>'
  +'<span class="sub">数据截至 '+DATA.date+' 收盘 · 口径：原文 ch20 [ZD,ZG]=[max(低A,低C), min(高A,高C)]</span></div>';
wrap.insertAdjacentHTML('beforeend', head);

['day','30m'].forEach(k=>{
  const lv = DATA.levels[k];
  const nm = k==='day' ? '日线' : '30 分钟';
  const zt = lv.last_zs ? '最后中枢 [ZD '+lv.last_zs.ZD+', ZG '+lv.last_zs.ZG+'] '+lv.last_zs.state
                        : '未形成中枢';
  const box = document.createElement('div');
  box.className='panel';
  box.innerHTML = '<div class="ptitle">'+nm+'<small>'+zt+' · 走势状态：'+lv.trend+'</small></div>'
    + '<div class="legend">'
    + '<span><i style="background:rgba(120,140,210,.55);border:1px solid #6d86c8"></i>中枢核心区 [ZD,ZG]</span>'
    + '<span><i style="background:none;border:1px dashed var(--muted-foreground)"></i>中枢波动区 [DD,GG]</span>'
    + '<span><i style="background:rgba(128,128,128,.9);height:2px"></i>笔</span>'
    + '<span style="color:'+ORG+'">▼ 三类卖点</span>'
    + '<span class="dead">✕ 已失效候选</span>'
    + '</div>';
  box.insertAdjacentHTML('beforeend', chart(box, lv));
  wrap.insertAdjacentHTML('beforeend', box.outerHTML);
});

const rows = [
  ['日线','<span class="dead">已失效 52.11</span>','<span class="none">—</span>','<span class="none">—</span>',
   '<span class="ok">已确认 71.53</span><br><span class="site">08-18，已兑现 −37%</span>',
   '<span class="warn">信息不足</span><br><span class="site">等 58.70 起离开段的回抽</span>'],
  ['30 分钟','<span class="none">—</span>','<span class="none">—</span>','<span class="none">—</span>',
   '<span class="ok">已确认 49.88</span><br><span class="site">10-13，已兑现</span>',
   '<span class="warn">尚无</span><br><span class="site">向下一笔未完成</span>'],
  ['5 分钟','<span class="none">—</span>','<span class="none">—</span>','<span class="none">—</span>',
   '<span class="site">中枢 [47.20,48.30] 延伸 9 段<br>→ 级别升级提示（ch33/ch96）</span>',
   '<span class="warn">尚无</span>'],
];
let t = '<table><tr><th>级别</th><th>一类买点</th><th>二类买点</th><th>三类买点</th><th>三类卖点（已确认）</th><th>下一个结构事件</th></tr>';
rows.forEach(r=>{ t += '<tr><td class="lv">'+r[0]+'</td>'+r.slice(1).map(c=>'<td>'+c+'</td>').join('')+'</tr>'; });
t += '</table>';
wrap.insertAdjacentHTML('beforeend', '<div class="panel">'+t
  +'<div class="site" style="padding-top:6px">状态四态：候选 / 已确认 / 已失效 / 信息不足（脚本只产「候选」，已确认由技能复核后给出）。日线与 30 分钟均无任何买点结构。</div></div>');

const ladder = [['44.80','10-15 最低','var(--muted-foreground)'],['48.38','10-14 反抽高','var(--muted-foreground)'],
  ['49.88','30m 三卖点','#e8710a'],['55.50','日线 ZD（反弹不破→日线三卖）','#6d86c8'],
  ['56.88','30m ZD（站上才失效）','#6d86c8'],['58.70','日线 ZG / 30m 波动 GG','#6d86c8']];
wrap.insertAdjacentHTML('beforeend','<div class="panel"><div class="ladder">'
  + ladder.map(([p,d,col])=>'<span class="chip"><b style="color:'+col+'">'+p+'</b> '+d+'</span>').join('')
  + '</div></div>');
"""

html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False)).replace("__JS__", JS)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write(html)
print("written:", OUT, len(html), "bytes")
print("day bars:", len(data["levels"]["day"]["bars"]), "30m bars:", len(data["levels"]["30m"]["bars"]))
print("day zs in window:", [(z["ZD"], z["ZG"]) for z in data["levels"]["day"]["zs"]])
print("30m zs in window:", [(z["ZD"], z["ZG"]) for z in data["levels"]["30m"]["zs"]])
