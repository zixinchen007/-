"""Offline comparison data. Does not construct network or login clients."""
from dataclasses import asdict, replace
import math

from .builder import RunningTrackBuilder
from .consistency import distance, EARTH_RADIUS_M
from .motion import MotionOptions, build_timeline
from .region_geometry import verify_region


def default_loop():
    scale = math.pi * EARTH_RADIUS_M / 180
    radius = 36.5
    half = (400 - 2 * math.pi * radius) / 4
    return [(30 + (offset + radius * math.sin(i * math.pi / 32)) / scale,
             120 + radius * math.cos(i * math.pi / 32) / (scale * math.cos(math.radians(30))))
            for offset, indices in ((half, range(33)), (-half, range(32, 65))) for i in indices]


def _model(meters, seconds, loop, checkpoints, timeline, route_variation=None, prepared_route=None):
    steps = timeline.total_steps if timeline is not None else round(meters / .72)
    builder = RunningTrackBuilder(0, 0, 1 if checkpoints else 6, uuid_str='offline-preview')
    points, fixed = builder.generate_track_around_points(*loop[0], checkpoints,
        meters, seconds, 0, route_loop=loop, timeline=timeline, route_variation=route_variation,
        prepared_route=prepared_route)
    body, _, _ = builder.build_submission_payload(meters, seconds, steps, 0,
        seconds * 1000, points, fixed, timeline=timeline)
    import json
    native = json.loads(body['allLocJson'])
    samples = []
    cadences = timeline.cadences_spm if timeline is not None else None
    for second in range(seconds + 1):
        if timeline is None:
            cumulative, step_count = meters * second / seconds, steps * second / seconds
            speed, cadence = meters / seconds, steps * 60 / seconds
        else:
            cumulative, step_count = timeline.counters_at(second * 1000)
            speed, cadence = timeline.speeds_m_s[second], cadences[second]
        samples.append({'time_s': second, 'distance_m': cumulative, 'steps': step_count,
                        'speed_m_s': speed, 'pace_min_km': 1000 / speed / 60 if speed > 0 else None,
                        'cadence_spm': cadence, 'stride_m': speed * 60 / cadence if cadence > 0 else None})
    actual = max_position_error = 0.0
    for index, point in enumerate(points):
        if index:
            previous = points[index - 1]
            actual += distance((previous.lat, previous.lon), (point.lat, point.lon))
        expected = timeline.counters_at(point.time)[0] if timeline is not None else meters * point.time / (seconds * 1000)
        max_position_error = max(max_position_error, abs(actual - expected))
    checks = {
        'distance_total': abs(actual - meters) <= max(.1, meters * .0001),
        'position_timeline': max_position_error < .01,
        'steps_total': native[-1]['steps'] == steps,
        'distance_windows': abs(sum(row['distance'] for row in body['speedPerTenSec']) - meters) < .01,
        'step_windows': sum(row['stepsNum'] for row in body['stepsPerTenSec']) == steps,
        'timestamps': points[0].time == 0 and points[-1].time == seconds * 1000 and
                      all(a.time < b.time for a, b in zip(points, points[1:])),
        'pace_units': all((abs(wire['speed'] * point.speed - 1000 / 60) < 1e-8
                          if point.speed > 0 else wire['speed'] == 0)
                          for wire, point in zip(native, points)),
    }
    result = {
        'summary': {'distance_m': meters, 'duration_s': seconds, 'total_steps': steps,
                    'average_cadence_spm': steps * 60 / seconds,
                    'average_pace_min_km': seconds / 60 / (meters / 1000),
                    'fastest_pace_min_km': min(row['speed'] for row in native if row['speed'] > 0),
                    'slowest_pace_min_km': max(row['speed'] for row in native if row['speed'] > 0),
                    'average_stride_m': meters / steps, 'gps_point_count': len(points),
                    'position_error_m': max_position_error},
        'checks': checks, 'samples': samples,
        'track': [{'lat': p.lat, 'lon': p.lon, 'time_ms': p.time, 'speed_m_s': p.speed} for p in points],
        'map_points': native, 'distance_windows': body['speedPerTenSec'],
        'step_windows': body['stepsPerTenSec'],
    }
    geometry = builder.route_geometry
    if geometry is not None:
        for point in result['track']:
            point.update(geometry.metadata_at(timeline.counters_at(point['time_ms'])[0]))
        result['summary'].update(max_lateral_offset_m=max(p['offset_m'] for p in result['track']),
            complete_laps=result['track'][-1]['lap'], reference_perimeter_m=geometry.perimeter_m)
        result['geometry'] = {'derived_seed': geometry.seed, 'controls_m': geometry.controls,
            'control_spacing_m': 50, 'control_step_limit_m': .5, 'anchor_taper_m': 25,
            'anchor_stations_m': geometry.anchor_stations_m, 'required_visits': len(geometry.required_indices)}
        result['checks'].update(lateral_bounds=all(0 <= p['offset_m'] <= 2 for p in result['track']),
            ordered_required_visits=len(geometry.required_indices) == len(fixed))
    return result


def build_preview(meters, seconds, options=None, route_loop=None, checkpoints=None, route_name='示意跑道',
                  route_variation=None, fence_snapshot=None):
    options = options or MotionOptions()
    timeline = build_timeline(meters, seconds, options)
    loop = default_loop() if route_loop is None else route_loop
    if checkpoints is not None:
        if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 5:
            raise ValueError('本地参考点位必须是 1 至 5 个坐标对象')
        from .consistency import coordinate
        for point in checkpoints:
            if not isinstance(point, dict) or 'lat' not in point:
                raise ValueError('本地参考点位缺少坐标')
            coordinate(point['lat'], point.get('lng', point.get('lon')))
        checkpoints = [dict(point, lng=point.get('lng', point.get('lon'))) for point in checkpoints]
    models = {name: _model(meters, seconds, loop, checkpoints, state)
              for name, state in (('constant', None), ('jog', timeline))}
    report = {'schema_version': 1, 'simulated': True, 'seed': timeline.seed,
            'options': asdict(replace(options, seed=timeline.seed)),
            'route_name': str(route_name), 'route_loop': loop, 'reference_checkpoints': checkpoints or [],
            'all_checks_passed': all(all(model['checks'].values()) for model in models.values()),
            'models': models}
    if route_variation is not None:
        route_variation.validate()
        if route_variation.max_offset_m > 0:
            configured = route_variation if route_variation.seed is not None else replace(route_variation, seed=timeline.seed)
            varied = _model(meters, seconds, loop, checkpoints, timeline, configured)
            varied['checks'].update(same_motion=varied['samples'] == models['jog']['samples'],
                same_distance_windows=varied['distance_windows'] == models['jog']['distance_windows'],
                same_step_windows=varied['step_windows'] == models['jog']['step_windows'])
            boundary = verify_region(varied['track'], fence_snapshot)
            report['route_variation'] = {'options': asdict(configured), 'model': varied, 'boundary': boundary}
            report['all_checks_passed'] &= all(varied['checks'].values())
    return report
