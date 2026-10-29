"""Android map serialization and post-settlement OBS record association."""
import base64
import datetime
import gzip
import json
import math

from .consistency import coordinate, distance


def bd09_to_gcj02(latitude, longitude):
    latitude, longitude = coordinate(latitude, longitude)
    x, y = longitude - 0.0065, latitude - 0.006
    factor = math.pi * 3000 / 180
    radius = math.hypot(x, y) - 0.00002 * math.sin(y * factor)
    angle = math.atan2(y, x) - 0.000003 * math.cos(x * factor)
    return radius * math.sin(angle), radius * math.cos(angle)


def map_points(points, start_ms, seconds, meters, steps, timeline=None):
    """Keep the generated route; serialize MyLocation's GCJ map coordinates."""
    traveled = [0.0]
    for previous, point in zip(points, points[1:]):
        traveled.append(traveled[-1] + distance((previous.lat, previous.lon), (point.lat, point.lon)))
    if not points or traveled[-1] <= 0:
        raise ValueError('地图轨迹没有有效长度')
    if timeline is not None:
        timeline.validate_target(meters, seconds, steps)
    zone = datetime.timezone(datetime.timedelta(hours=8))
    result = []
    for index, (point, length) in enumerate(zip(points, traveled)):
        elapsed = (point.time - start_ms) / 1000
        latitude, longitude = bd09_to_gcj02(point.lat, point.lon)
        cumulative = meters * length / traveled[-1]
        step_count = round(steps * elapsed / seconds)
        if timeline is not None:
            cumulative, raw_steps = timeline.counters_at(point.time - start_ms)
            step_count = getattr(timeline, 'round_steps', round)(raw_steps)
        # The detail chart reads this field as min/km; GpsPoint.speed stays m/s.
        # Keep the existing zero sentinel when no positive speed is available.
        pace = 1000.0 / (60.0 * point.speed) if point.speed > 0 else 0.0
        result.append({
            'gLat': round(latitude, 7), 'gLng': round(longitude, 7),
            'coorType': 'gcj02', 'lat': -1.0, 'lng': -1.0,
            'flag': start_ms, 'queueNum': index + 1, 'type': 0 if point.isValid else -1,
            'totalTime': int(elapsed), 'validTime': int(elapsed),
            'totalDis': round(cumulative, 4), 'validDis': round(cumulative, 4),
            'steps': step_count, 'speed': pace,
            'avgSpeed': cumulative / elapsed if elapsed else 0.0,
            'radius': point.accuracy,
            'gainTime': datetime.datetime.fromtimestamp(point.time / 1000, zone).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return result


def bind_record(payload, rrid, uuid, start_ms):
    if isinstance(rrid, bool) or not str(rrid).isdigit() or int(rrid) <= 0:
        raise ValueError('没有有效记录 ID，不能关联轨迹文件')
    if not isinstance(uuid, str) or not uuid or any(char in uuid for char in '/\\\r\n'):
        raise ValueError('轨迹 UUID 无效')
    rrid = int(rrid)
    bound = dict(payload)
    for key, value in (('rrid', str(rrid)), ('uuid', uuid)):
        bound[key] = base64.b64encode(gzip.compress(value.encode('utf-8'))).decode('ascii')
    zone = datetime.timezone(datetime.timedelta(hours=8))
    hour = datetime.datetime.fromtimestamp(start_ms / 1000, zone).strftime('%Y%m%d%H')
    keys = [f'run_data/{hour}/{uuid}.json', f'run_data/{rrid // 1000000}/{rrid}.json']
    return bound, keys
