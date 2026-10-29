"""V3 offline preview over the same prepared path consumed by the model."""
from dataclasses import asdict, replace
import random

from .builder import RunningTrackBuilder
from .motion import MotionOptions
from .motion_v3 import MotionV3Options, build_timeline, VERSION
from .preview import build_preview as baseline_preview, default_loop, _model
from .region_geometry import verify_region


def build_preview(meters, seconds, options=None, route_loop=None, checkpoints=None,
                  route_name='示意跑道', route_variation=None, fence_snapshot=None):
    options = options or MotionV3Options()
    options.validate()
    if options.seed is None:
        options = replace(options, seed=random.SystemRandom().randrange(2**64))
    # The original preview remains a reproducible baseline (no client imports).
    report = baseline_preview(meters, seconds, MotionOptions(seed=options.seed),
                              route_loop, checkpoints, route_name,
                              route_variation, fence_snapshot)
    loop = default_loop() if route_loop is None else route_loop
    checkpoints = report['reference_checkpoints'] or None
    builder = RunningTrackBuilder(0, 0, 1 if checkpoints else 6, uuid_str='offline-v3')
    prepared = builder.prepare_route(*loop[0], checkpoints, meters, loop,
                                     route_variation, options.seed)
    motion_route = prepared.motion_route
    timeline = build_timeline(motion_route, seconds, options)
    motion_route.validate_bends(timeline, options.lateral_accel_m_s2)
    model = _model(meters, seconds, loop, checkpoints, timeline, route_variation, prepared)
    model['boundary'] = verify_region(model['track'], fence_snapshot)
    report['models']['v3'] = model
    report['v3_options'] = asdict(options)
    report['selected_model'] = 'v3'
    report['model_version'] = VERSION
    report['all_checks_passed'] &= all(model['checks'].values())
    return report
