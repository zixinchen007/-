"""Independent offline entry: never creates an account or network client."""
import argparse
import json
from pathlib import Path
import sys

from running.motion import MotionOptions
from running.route_variation import RouteVariationOptions
from running.preview import build_preview
from running.preview_html import write_preview


def main(argv=None):
    parser = argparse.ArgumentParser(description='连续慢跑离线模拟预览，不登录、不上传')
    parser.add_argument('--distance', type=int, default=2537, help='距离，米，默认 2537')
    parser.add_argument('--time-sec', type=int, default=1183, help='时长，秒，默认 1183')
    parser.add_argument('--seed', type=int, help='可复现随机种子，省略时生成并保存')
    parser.add_argument('--motion-model', choices=['jog', 'v3'], default='jog')
    parser.add_argument('--route-file', type=Path, help='本地 BD-09 跑道模板 JSON')
    parser.add_argument('--checkpoints-file', type=Path, help='可选本地参考点位 JSON 数组，不请求官方点位')
    parser.add_argument('--lap-variation', action='store_true', help='离线对比最多2米的多圈走线微变')
    parser.add_argument('--fences-file', type=Path, help='可选BD-09本地围栏快照，须含来源与采集日期')
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / '.local/previews')
    args = parser.parse_args(argv)
    if args.fences_file and not args.lap_variation:
        parser.error('--fences-file 需要与 --lap-variation 一起使用')
    try:
        loop, name, checkpoints = None, '示意跑道（非实测）', None
        if args.route_file:
            route = json.loads(args.route_file.read_text(encoding='utf-8-sig'))
            if not isinstance(route, dict) or route.get('coord_type') != 'bd09' or not isinstance(route.get('loop'), list):
                raise ValueError('跑道文件需要 coord_type=bd09 和 loop 点列')
            loop, name = route['loop'], route.get('name') or args.route_file.name
        if args.checkpoints_file:
            checkpoints = json.loads(args.checkpoints_file.read_text(encoding='utf-8-sig'))
        fences = json.loads(args.fences_file.read_text(encoding='utf-8-sig')) if args.fences_file else None
        preview, options = build_preview, MotionOptions(seed=args.seed)
        if args.motion_model == 'v3':
            from running.preview_v3 import build_preview as preview
            from running.motion_v3 import MotionV3Options
            options = MotionV3Options(seed=args.seed)
        report = preview(args.distance, args.time_sec, options, loop, checkpoints, name,
            route_variation=RouteVariationOptions() if args.lap_variation else None, fence_snapshot=fences)
        html_path, data_path = write_preview(report, args.output_dir)
        print(json.dumps({'html': str(html_path.resolve()), 'data': str(data_path.resolve()),
                          'seed': report['seed'], 'all_checks_passed': True}, ensure_ascii=False))
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f'离线预览失败：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
