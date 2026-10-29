"""Self-contained SVG charts for local simulation review; no external assets."""
from html import escape
import json
import math
from pathlib import Path
from .lap_preview_html import render_lap_comparison


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f'{minutes}:{seconds:02d}'


def pace(minutes):
    whole, seconds = divmod(int(minutes * 60), 60)
    return f'{whole}′{seconds:02d}″'


MODEL_STYLES = (('constant', '#8794a5', True, '匀速基准'),
                ('jog', '#1768b3', False, '连续慢跑'),
                ('v3', '#b65a20', False, 'motion-v3'))


def _chart(models, key, title, unit, inverted=False):
    series = [(models[name]['samples'], color, dashed)
              for name, color, dashed, _ in MODEL_STYLES if name in models]
    values = [p[key] for points, _, _ in series for p in points if p[key] is not None]
    low, high = min(values), max(values)
    padding = max((high-low)*.12, .01)
    low, high = max(0,low-padding), high+padding
    left, top, width, height = 100,22,766,155
    duration = series[0][0][-1]['time_s']
    pieces = [f'<section class="chart"><h2>{escape(title)} <small>{escape(unit)}</small></h2>',
              f'<svg viewBox="0 0 900 220" role="img" aria-label="{escape(title)}">']
    for tick in range(5):
        f = tick/4
        y = top+f*height
        value = low+f*(high-low) if inverted else high-f*(high-low)
        label = pace(value) if key=='pace_min_km' else f'{value:.2f}' if key=='stride_m' else f'{value:.0f}'
        pieces += [f'<line x1="{left}" y1="{y}" x2="{left+width}" y2="{y}" class="grid"/>',
                   f'<text x="{left-12}" y="{y+4}" text-anchor="end">{label}</text>',
                   f'<text x="{left+f*width}" y="{top+height+27}" text-anchor="middle">{clock(duration*f)}</text>']
    for points,color,dashed in series:
        groups, coordinates = [], []
        for p in points:
            if p[key] is None:
                if coordinates:
                    groups.append(coordinates)
                coordinates=[]
                continue
            ratio=(p[key]-low)/(high-low)
            y=top+(ratio if inverted else 1-ratio)*height
            coordinates.append(f'{left+p["time_s"]/duration*width:.2f},{y:.2f}')
        if coordinates:
            groups.append(coordinates)
        dash = 'stroke-dasharray="6 5"' if dashed else ''
        for group in groups:
            coords=' '.join(group)
            pieces.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.2" {dash}/>')
    return ''.join(pieces)+'</svg></section>'


def _route(report):
    track = report['models'][report.get('selected_model', 'jog')]['track']
    lat0 = sum(point['lat'] for point in track) / len(track)
    lon0 = sum(point['lon'] for point in track) / len(track)
    cosine = math.cos(math.radians(lat0))
    flat = [((point['lon'] - lon0) * cosine * 111195, (point['lat'] - lat0) * 111195) for point in track]
    xs, ys = zip(*flat)
    scale = min(330 / max(max(xs) - min(xs), 1), 300 / max(max(ys) - min(ys), 1))
    center_x, center_y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    def screen(x, y):
        return 220 + (x - center_x) * scale, 175 - (y - center_y) * scale

    line = ' '.join(f'{x:.2f},{y:.2f}' for x, y in (screen(x, y) for x, y in flat))
    parts = ['<section class="route"><h2>路线 <small>BD-09 本地坐标示意</small></h2>',
             '<svg viewBox="0 0 440 365" role="img" aria-label="本地跑道路线">',
             f'<polyline points="{line}" fill="none" stroke="#1768b3" stroke-width="3"/>',
             '<text x="24" y="30">北 ↑</text>']
    for index, point in enumerate(report['reference_checkpoints']):
        x, y = screen((point.get('lng', point.get('lon')) - lon0) * cosine * 111195,
                      (point['lat'] - lat0) * 111195)
        parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="#c98715"/><text x="{x + 10}" y="{y + 4}">{index + 1}</text>')
    return ''.join(parts) + '</svg><p>两种模型沿同一路线运动；蓝线保留弯角和本地参考点。</p></section>'


def render_preview(report, data_name):
    models = report['models']
    summary = models[report.get('selected_model', 'jog')]['summary']
    model_names = [name for name, _, _, _ in MODEL_STYLES if name in models]
    headers = ''.join(f'<th>{label}</th>' for name, _, _, label in MODEL_STYLES if name in models)
    legend = ''.join(f'<span style="color:{color}">━ {label}</span>' for name, color, _, label in MODEL_STYLES if name in models)
    rows = []
    for label, key, format_value in (
            ('距离', 'distance_m', lambda value: f'{value / 1000:.3f} 公里'),
            ('用时', 'duration_s', clock), ('步数', 'total_steps', str),
            ('平均配速', 'average_pace_min_km', pace),
            ('最快逐点配速', 'fastest_pace_min_km', pace),
            ('平均步频', 'average_cadence_spm', lambda value: f'{value:.1f} 步/分'),
            ('平均步幅', 'average_stride_m', lambda value: f'{value * 100:.1f} 厘米')):
        cells = ''.join(f'<td>{escape(format_value(models[name]["summary"][key]))}</td>' for name in model_names)
        rows.append(f'<tr><th>{label}</th>{cells}</tr>')
    charts = ''.join(_chart(models, key, title, unit, inverted) for key, title, unit, inverted in (
        ('pace_min_km', '配速', '分/公里；数值越小越快', True),
        ('cadence_spm', '步频', '步/分钟', False), ('stride_m', '推算步幅', '米/步', False),
        ('distance_m', '累计距离', '米', False), ('steps', '累计步数', '步', False)))
    passed = '一致性检查全部通过' if report['all_checks_passed'] else '一致性检查未通过'
    max_error = max(model['summary']['position_error_m'] for model in models.values())
    options = report['options']
    description = (f"主体速度约 ±{options['variation'] * 100:g}% 平滑起伏，相关时长约 {options['correlation_seconds']:g} 秒；步频中心 {options['cadence_center']:g} 步/分钟。")
    if report.get('selected_model') == 'v3':
        description = 'motion-v3：最终路线曲率限速、独立平滑噪声、零速起停；步频由速度耦合并积分。橙线为 V3，参数参考固定上游提交。'
    model_title = 'motion-v3' if report.get('selected_model') == 'v3' else '连续慢跑'
    lap_comparison = render_lap_comparison(report)
    if lap_comparison and report.get('selected_model') == 'v3':
        lap_comparison += '<p>上方展示原连续慢跑模型的走线对比；V3 按最终路线曲率重新求解，运动数据见下方橙线。</p>'
    route_card = '' if lap_comparison else _route(report)
    top_style = ' style="grid-template-columns:1fr"' if lap_comparison else ''
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{model_title} · 离线模拟预览</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f8;color:#172b40;font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:30px 24px 48px}}h1{{font-size:28px;margin:10px 0}}h2{{font-size:19px;margin:0 0 12px}}
small,p,.muted{{color:#586c7f}}small{{font-size:12px;font-weight:400}}p{{margin:8px 0}}.tag{{color:#1768b3;font-weight:600}}
.top{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:22px 0}}section{{background:white;padding:20px;border:1px solid #e2e8ee;border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:9px 7px;border-bottom:1px solid #edf0f3}}th{{font-weight:500}}
.chart{{margin:18px 0}}svg{{display:block;width:100%;height:auto}}svg text{{font-size:12px;fill:#5b6f82;font-family:"Microsoft YaHei",sans-serif}}
.grid{{stroke:#e8edf1;stroke-width:1}}.legend{{display:flex;gap:24px;flex-wrap:wrap}}.baseline{{color:#758395}}.jog{{color:#1768b3}}
.check{{border-left:4px solid #2a7b55}}a{{color:#1768b3}}code{{overflow-wrap:anywhere}}@media(max-width:760px){{main{{padding:18px 12px}}.top{{grid-template-columns:1fr}}h1{{font-size:23px}}.chart svg text{{font-size:27px}}}}
</style><main><span class="tag">离线模拟 · 实验版本</span>
<h1>{summary['distance_m'] / 1000:.3f} 公里 / {clock(summary['duration_s'])} {model_title}</h1>
<p>路线：{escape(report['route_name'])}　种子：<code>{report['seed']}</code></p>
<p>{description}</p>
<p class="muted">这是本地模拟数据，不是实测运动。此页面不登录账号、不发送数据；本地参考点位未经实时服务端校验。</p>
{lap_comparison}<div class="top"{top_style}>{route_card}<section><h2>总览对比</h2><table><thead><tr><th>项目</th>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>匀速基准使用原来的均匀分配，步数按 0.72 米/步估算；新模型步数由步频积分得到。</p></section></div>
<div class="legend">{legend}</div>
{charts}<section class="check"><h2>{passed}</h2><p>检查距离总量、位置时间关系、步数总量、10 秒窗口、时间戳和配速单位。</p>
<p>位置与时间序列的最大误差：{max_error * 1000:.2f} 毫米。距离与时长保持输入值。</p>
<p><a href="{escape(data_name, quote=True)}">查看完整 JSON 数据、模型参数及逐项检查结果</a></p></section></main></html>'''


def write_preview(report, output_dir):
    if not report['all_checks_passed']:
        raise ValueError('离线一致性检查未通过，不输出预览')
    summary = report['models'][report.get('selected_model', 'jog')]['summary']
    stem = f'motion-{summary["distance_m"]}-{summary["duration_s"]}-{report["seed"]}'
    if report.get('selected_model') == 'v3':
        stem += '-v3'
    if report.get('route_variation'):
        stem += '-lap2m'
    directory = Path(output_dir)
    data_path, html_path = directory / f'{stem}.json', directory / f'{stem}.html'
    data = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
    html = render_preview(report, data_path.name)
    directory.mkdir(parents=True, exist_ok=True)
    data_path.write_text(data, encoding='utf-8')
    html_path.write_text(html, encoding='utf-8')
    return html_path, data_path
