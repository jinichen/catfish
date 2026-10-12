"""小鲶 2D 角色设定页生成器: 以 branding/logo-mascot.svg (5 月定稿) 为母版, 不改轮廓,
出 渐变版 (产品现用) + 扁平版 (短视频 / 小尺寸) × 6 个表情, 写 sheet.html + 每个表情的独立 SVG。
用法: python3 build_sheet.py   (输出在本目录)"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))

def defs(p):
    return f'''<defs>
<radialGradient id="{p}body" cx="50%" cy="40%" r="65%"><stop offset="0%" stop-color="#1A8A95"/><stop offset="60%" stop-color="#0E5F66"/><stop offset="100%" stop-color="#0A464C"/></radialGradient>
<linearGradient id="{p}belly" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#FAF1E4" stop-opacity="0"/><stop offset="100%" stop-color="#FAF1E4" stop-opacity="0.85"/></linearGradient>
<clipPath id="{p}clip"><ellipse cx="220" cy="240" rx="170" ry="135"/></clipPath>
</defs>'''

# 两套配色: grad = 现有产品版; flat = 短视频品牌色 (#0f3d44 / #1f8a8f / #e8864a / #f6f3ea)
STYLE = {
 'grad': dict(body=lambda p: f'url(#{p}body)', dark='#0A464C', tail2='#0E5F66', wh=lambda p: f'url(#{p}wh)', whr=lambda p: f'url(#{p}whr)',
              tip='#F47B3D', ink='#1A2E33', blush='#F47B3D', belly='grad', finsvg='<ellipse cx="155" cy="310" rx="35" ry="20" fill="#0A464C" transform="rotate(-25 155 310)"/>', mw=3.5, bo=0.45),
 'flat': dict(body=lambda p: '#1f8a8f', dark='#0f3d44', tail2='#16565f', wh=lambda p: '#0f3d44', whr=lambda p: '#0f3d44',
              tip='#e8864a', ink='#0f3d44', blush='#e8864a', belly='flat', finsvg='<path d="M 150 268 Q 196 262 214 296 Q 178 306 150 268 Z" fill="#16565f"/>', mw=5.5, bo=0.8),
}

# 10/12: 须从嘴角向前张开、尖端微翘 (方案 D); 渐变按 根→尖 (userSpaceOnUse), 修掉母版上须渐变反了的 bug
UPPER = {'normal': 'M 92 236 Q 50 214 26 222 Q 10 228 12 212', 'raised': 'M 92 236 Q 52 200 34 196 Q 18 194 20 176'}
UPPER_TIP = {'normal': (12, 212), 'raised': (20, 176)}
LOWER, LOWER_TIP = 'M 96 248 Q 56 262 34 284 Q 20 300 30 312', (30, 312)
def grad(gid, root, tip):
    return (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{root[0]}" y1="{root[1]}" x2="{tip[0]}" y2="{tip[1]}">'
            '<stop offset="0%" stop-color="#0E5F66"/><stop offset="100%" stop-color="#F47B3D"/></linearGradient>')

def fish(style, p, eye='open', mouth='smile', whisker='normal', extra=''):
    s = STYLE[style]
    belly = (f'<ellipse cx="220" cy="290" rx="140" ry="80" fill="url(#{p}belly)" opacity="0.6"/>' if s['belly'] == 'grad'
             else f'<ellipse cx="225" cy="330" rx="160" ry="80" fill="#f6f3ea" opacity="0.92" clip-path="url(#{p}clip)"/>')
    tx, ty = UPPER_TIP[whisker]
    eyes = {
      'open': f'<circle cx="135" cy="200" r="32" fill="#fff"/><circle cx="142" cy="206" r="18" fill="{s["ink"]}"/><circle cx="148" cy="200" r="6" fill="#fff"/><circle cx="138" cy="214" r="3" fill="#fff" opacity="0.7"/>',
      'up':   f'<circle cx="135" cy="200" r="32" fill="#fff"/><circle cx="146" cy="188" r="17" fill="{s["ink"]}"/><circle cx="151" cy="183" r="5.5" fill="#fff"/>',
      'happy': f'<path d="M 106 210 Q 135 178 164 210" stroke="{s["ink"]}" stroke-width="10" fill="none" stroke-linecap="round"/>',
      'wide': f'<circle cx="135" cy="200" r="34" fill="#fff"/><circle cx="138" cy="202" r="13" fill="{s["ink"]}"/><circle cx="142" cy="197" r="4.5" fill="#fff"/><path d="M 104 156 Q 132 140 162 152" stroke="{s["dark"]}" stroke-width="7" fill="none" stroke-linecap="round"/>',
      'worried': f'<circle cx="135" cy="202" r="30" fill="#fff"/><circle cx="140" cy="210" r="16" fill="{s["ink"]}"/><circle cx="145" cy="205" r="5" fill="#fff"/><path d="M 106 166 Q 128 168 160 152" stroke="{s["dark"]}" stroke-width="7" fill="none" stroke-linecap="round"/>',
      'sleep': f'<path d="M 108 204 Q 135 222 162 204" stroke="{s["ink"]}" stroke-width="8" fill="none" stroke-linecap="round"/>',
    }[eye]
    mouths = {
      'smile': f'<path d="M 95 235 Q 105 248 120 244" stroke="{s["dark"]}" stroke-width="{s["mw"]}" fill="none" stroke-linecap="round"/>',
      'grin':  f'<path d="M 92 234 Q 106 256 124 242 Z" fill="{s["dark"]}"/>',
      'o':     f'<ellipse cx="104" cy="244" rx="8" ry="10" fill="{s["dark"]}"/>',
      'wavy':  f'<path d="M 94 246 Q 101 240 108 246 Q 115 252 122 246" stroke="{s["dark"]}" stroke-width="{s["mw"]}" fill="none" stroke-linecap="round"/>',
      'tiny':  f'<path d="M 98 240 Q 106 246 114 242" stroke="{s["dark"]}" stroke-width="{s["mw"]}" fill="none" stroke-linecap="round"/>',
    }[mouth]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 0 500 470">{defs(p)}
<path d="M 380 240 L 460 180 L 445 240 L 460 300 Z" fill="{s['dark']}" opacity="0.9"/><path d="M 380 240 L 455 200 L 440 240 L 455 285 Z" fill="{s['tail2']}"/>
<ellipse cx="220" cy="240" rx="170" ry="135" fill="{s['body'](p)}"/>{belly}
<path d="M 180 110 Q 220 70 270 105 Q 250 115 220 115 Q 195 115 180 110 Z" fill="{s['dark']}"/>
<path d="M 130 220 Q 145 240 130 260" stroke="{s['dark']}" stroke-width="3" fill="none" opacity="0.5" stroke-linecap="round"/>
{s['finsvg']}
<defs>{grad(p+'wh', (92,236), (tx,ty))}{grad(p+'whr', (96,248), LOWER_TIP)}</defs>
<path d="{UPPER[whisker]}" stroke="{s['wh'](p)}" stroke-width="7" fill="none" stroke-linecap="round"/><circle cx="{tx}" cy="{ty}" r="9" fill="{s['tip']}"/>
<path d="{LOWER}" stroke="{s['whr'](p)}" stroke-width="7" fill="none" stroke-linecap="round"/><circle cx="{LOWER_TIP[0]}" cy="{LOWER_TIP[1]}" r="9" fill="{s['tip']}"/>
{eyes}<ellipse cx="115" cy="255" rx="18" ry="10" fill="{s['blush']}" opacity="{s['bo']}"/>{mouths}{extra}</svg>'''

T, O = '#1f8a8f', '#e8864a'
EXPR = [
 ('default', '默认 · 打招呼', '对话头像、登录页、空状态', dict(eye='open', mouth='smile',
   extra=f'<circle cx="190" cy="80" r="6" fill="{T}" opacity="0.5"/><circle cx="170" cy="55" r="4" fill="{T}" opacity="0.4"/><circle cx="155" cy="35" r="2.5" fill="{T}" opacity="0.3"/>')),
 ('thinking', '思考中', '模型处理中、加载、工具调用', dict(eye='up', mouth='o',
   extra=f'<circle cx="250" cy="70" r="10" fill="{T}" opacity="0.55"/><circle cx="285" cy="42" r="14" fill="{T}" opacity="0.45"/><circle cx="328" cy="22" r="18" fill="{T}" opacity="0.35"/>')),
 ('done', '完成了', '任务完成、报价单生成好了', dict(eye='happy', mouth='grin',
   extra=f'<circle cx="345" cy="95" r="34" fill="#059669"/><path d="M 330 96 L 342 108 L 362 84" stroke="#fff" stroke-width="8" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M 300 50 l 6 -14 l 6 14 l 14 6 l -14 6 l -6 14 l -6 -14 l -14 -6 Z" fill="{O}"/>')),
 ('alert', '提醒你', '跟进雷达、到期提醒、推到微信', dict(eye='wide', mouth='o', whisker='raised',
   extra=f'<rect x="300" y="40" width="64" height="64" rx="32" fill="{O}"/><rect x="328" y="52" width="8" height="26" rx="4" fill="#fff"/><circle cx="332" cy="89" r="5" fill="#fff"/>')),
 ('sorry', '出错了 · 抱歉', '调用失败、断网、需要人工', dict(eye='worried', mouth='wavy',
   extra=f'<path d="M 300 120 Q 288 146 300 154 Q 312 146 300 120 Z" fill="#7cc4d0"/>')),
 ('sleep', '休息中', '离线、电脑睡眠、夜间', dict(eye='sleep', mouth='tiny',
   extra=f'<text x="290" y="100" font-family="Inter,Arial" font-weight="800" font-size="40" fill="{T}" opacity="0.7">Z</text><text x="330" y="66" font-family="Inter,Arial" font-weight="800" font-size="30" fill="{T}" opacity="0.5">z</text><text x="360" y="40" font-family="Inter,Arial" font-weight="800" font-size="22" fill="{T}" opacity="0.35">z</text>')),
]

svgs = {}
for style in ('grad', 'flat'):
    for key, *_ , kw in EXPR:
        svg = fish(style, f'{style}-{key}-', **kw)
        svgs[(style, key)] = svg
        open(os.path.join(HERE, f'xiaonian-{style}-{key}.svg'), 'w').write(svg)

def cards(style):
    return ''.join(f'<figure class="c"><div class="art">{svgs[(style,k)]}</div><figcaption><b>{n}</b><span>{u}</span></figcaption></figure>' for k, n, u, _ in EXPR)
def sizes(style):
    return ''.join(f'<div class="sz"><div style="width:{w}px;height:{w}px">{svgs[(style,"default")].replace("<svg ", "<svg width=\"100%\" height=\"100%\" ", 1)}</div><small>{w}px</small></div>' for w in (24, 36, 64, 120))
ref = open(os.path.join(HERE, '..', 'logo-mascot.svg')).read().split('?>', 1)[-1]
html = f'''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小鲶 2D 角色设定</title><style>
:root{{--bg:#f6f3ea;--ink:#0f3d44;--pri:#1f8a8f;--mut:#6b7b7d;--card:#fff;--bd:rgba(31,138,143,.22)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#10191d;--ink:#e9e6dc;--mut:#9aa7a8;--card:#18242a;--bd:rgba(31,138,143,.35)}}}}
*{{box-sizing:border-box;margin:0;padding:0}}body{{background:var(--bg);color:var(--ink);font-family:-apple-system,"PingFang SC","Noto Sans SC",sans-serif;padding:32px 16px 64px}}
main{{max-width:1180px;margin:0 auto}}h1{{font-size:28px}}h2{{font-size:19px;margin:36px 0 6px}}p.d{{color:var(--mut);font-size:14px;line-height:1.6;max-width:760px;margin-top:6px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px;margin-top:14px}}
.c{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px}}.art{{aspect-ratio:1;display:grid;place-items:center;background:#f6f3ea;border-radius:10px}}.art svg{{width:88%;height:auto}}
figcaption{{margin-top:10px;display:flex;flex-direction:column;gap:3px}}figcaption b{{font-size:15px}}figcaption span{{font-size:12.5px;color:var(--mut)}}
.row{{display:flex;gap:22px;align-items:flex-end;flex-wrap:wrap;background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:18px;margin-top:14px}}
.sz{{display:flex;flex-direction:column;align-items:center;gap:6px}}.sz>div{{background:#f6f3ea;border-radius:8px}}.sz small{{color:var(--mut);font-size:12px}}
.ref{{display:flex;gap:18px;align-items:center;background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin-top:14px}}.ref .art{{width:150px;flex:none}}
ul{{margin:8px 0 0 20px;color:var(--mut);font-size:14px;line-height:1.7}}
</style></head><body><main>
<h1>小鲶 · 2D 角色设定 v1</h1><p class="d"><b>10/12 定: 渐变版 (A) 为标准形象。</b>扁平版 (B) 仅留作参考, 不作为正式素材。</p>
<p class="d">母版是 5 月定稿的 <code>branding/logo-mascot.svg</code> (产品里对话头像 / 登录页正在用)。轮廓、比例、长须和配色都不改, 只加表情和一套扁平配色。</p>
<div class="ref"><div class="art">{ref}</div><div><b>母版 · 现用</b><ul><li>圆胖身体 + 两根带橙色须尖的长须 + 大圆眼 + 腮红</li><li>深青渐变 + 暖橙点缀</li></ul></div></div>
<h2>A · 渐变版 (产品现用风格)</h2><p class="d">放在 Companion 里: 对话头像、空状态、加载、提醒。跟现有界面无缝。</p><div class="g">{cards('grad')}</div>
<h2>B · 扁平版 (参考, 未采用)</h2><p class="d">去掉渐变, 换成短视频系列的品牌色 (墨青 #0f3d44 · 青绿 #1f8a8f · 橙 #e8864a · 米白腹部)。跟四条短片的扁平风格一致, 小尺寸更清楚。</p><div class="g">{cards('flat')}</div>
<h2>小尺寸检查</h2><p class="d">24px 是任务栏 / 列表头像的大小, 36px 是登录页现用尺寸。</p>
<div class="row"><b style="align-self:center;width:70px">渐变版</b>{sizes('grad')}</div><div class="row"><b style="align-self:center;width:70px">扁平版</b>{sizes('flat')}</div>
</main></body></html>'''
open(os.path.join(HERE, 'sheet.html'), 'w').write(html)
print('ok', len(svgs), 'svgs')
