"""Local SVG comparisons of unchanged and varied physical paths."""
from html import escape
import math


COLORS = ('#2166ac', '#c05b17', '#23875c', '#8d5db0', '#c34563', '#607d20', '#008694')


def render_lap_comparison(report):
    variant = report.get('route_variation')
    if variant is None:
        return ''
    baseline = report['models']['jog']['track']
    model = variant['model']
    changed = model['track']
    lat0 = sum(p['lat'] for p in changed) / len(changed)
    lon0 = sum(p['lon'] for p in changed) / len(changed)
    sx = 111195 * math.cos(math.radians(lat0))

    def xy(point):
        return (point['lon']-lon0)*sx, (point['lat']-lat0)*111195

    flat = [xy(p) for p in baseline + changed]
    xs, ys = zip(*flat)
    bounds = min(xs), max(xs), min(ys), max(ys)

    def panel(title, track, colored=False, zoom=False, green=False):
        left, right, bottom, top = bounds
        if zoom:
            bottom = top - (top-bottom)*.36
        width, height = 500, 390
        scale = min((width-55)/max(right-left, 1), (height-55)/max(top-bottom, 1))
        center_x, center_y = (left+right)/2, (bottom+top)/2

        def screen(point):
            x, y = xy(point)
            return width/2+(x-center_x)*scale, height/2-(y-center_y)*scale

        def polyline(points, color, stroke=1.4, dashed=False):
            values = ' '.join(f'{x:.3f},{y:.3f}' for x, y in map(screen, points))
            dash = 'stroke-dasharray="4 4"' if dashed else ''
            return f'<polyline points="{values}" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linejoin="round" stroke-linecap="round" {dash}/>'

        parts = [f'<div class="lap-card"><h3>{escape(title)}</h3>',
                 f'<svg class="lap-svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
                 polyline(baseline, '#b6c1ca', 1, True)]
        if colored:
            groups = []
            current, points = track[0].get('lap', 0), []
            for point in track:
                lap = point.get('lap', 0)
                if lap != current and points:
                    groups.append((current, points))
                    points = [points[-1]]
                    current = lap
                points.append(point)
            groups.append((current, points))
            for lap, points in groups:
                parts.append(polyline(points, COLORS[lap % len(COLORS)], 1.6 if zoom else 1.35))
        else:
            parts.append(polyline(track, '#13a875' if green else '#2166ac', 1.4))
        for index, point in enumerate(report['reference_checkpoints']):
            normalized = {'lat': point['lat'], 'lon': point.get('lng', point.get('lon'))}
            x, y = screen(normalized)
            if 10 < x < width-10 and 12 < y < height-12:
                parts.append(f'<circle cx="{x}" cy="{y}" r="3.5" fill="#b47a1d"/><text x="{x+6}" y="{y-6}">{index+1}</text>')
        bar = 5 if zoom else 20
        parts += [f'<path d="M25,{height-20}h{bar*scale:.2f}" stroke="#354e64" stroke-width="2"/>',
                  f'<text x="25" y="{height-30}">{bar}米</text>',
                  '<text x="22" y="25">北 ↑</text>', '</svg></div>']
        return ''.join(parts)

    boundary = variant['boundary']
    if boundary['status'] == 'snapshot_only':
        boundary_text = (f'本地围栏快照检查通过 · {boundary["captured_date"]} · {boundary["source"]}。'
                         '这不是实时学校规则验证。')
    else:
        boundary_text = '未提供围栏快照：边界尚未验证，不代表可用于真实提交。'
    summary = model['summary']
    legend = ''.join(f'<span style="color:{COLORS[n % len(COLORS)]}">━ 第{n+1}圈</span>'
                     for n in sorted({p['lap'] for p in changed}))
    panels = (panel('原路线：各圈重合', baseline) + panel('走线微变：按圈着色', changed, colored=True) +
              panel('单色叠加：地图线条效果', changed, green=True) +
              panel('弯道放大：保留真实米制比例', changed, colored=True, zoom=True))
    return f'''<section class="lap-comparison"><style>
.lap-comparison{{margin:22px 0}}.lap-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.lap-card{{border:1px solid #e1e8ed;border-radius:8px;background:#fbfcfd;overflow:hidden}}
.lap-card h3{{font-size:15px;margin:12px 14px 0}}.lap-legend{{display:flex;gap:15px;flex-wrap:wrap;font-size:13px;margin:12px 0}}
.lap-svg text{{font-size:14px}}@media(max-width:760px){{.lap-grid{{grid-template-columns:1fr}}.lap-svg text{{font-size:18px}}}}
</style><h2>多圈走线微变 · 最多2米</h2>
<p>偏移位于基准跑道外侧0–2米；路线之间使用相同的速度、步频、时长和目标距离。没有额外GPS定位噪声。</p>
<p>当前最大生成偏移 {summary['max_lateral_offset_m']:.2f} 米，完成约 {summary['complete_laps']} 个整圈及末尾弧段。
必经点仅强制按序完成一轮，之后自然绕圈。</p><div class="lap-legend">{legend}</div>
<div class="lap-grid">{panels}</div><p>灰色虚线为原路线。所有视图使用米制比例；放大仅改变显示比例。</p>
<p>{escape(boundary_text)}</p></section>'''
