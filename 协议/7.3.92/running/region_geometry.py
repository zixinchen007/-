"""Offline polygon-snapshot checks, including segment interiors and unions."""
import datetime
import math

from .consistency import coordinate, EARTH_RADIUS_M
from .routes import checkpoint_loop


def verify_region(track, snapshot):
    if snapshot is None:
        return {'status': 'unverified', 'reason': '未提供本地围栏快照；不代表实时可提交'}
    if not isinstance(snapshot, dict) or snapshot.get('coord_type') != 'bd09':
        raise ValueError('本地围栏快照需要BD-09坐标')
    captured = datetime.date.fromisoformat(snapshot.get('captured_date', '')).isoformat()
    source = snapshot.get('source')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('围栏快照必须注明来源')
    polygons = snapshot.get('polygons')
    if not isinstance(polygons, list) or not 1 <= len(polygons) <= 32:
        raise ValueError('围栏快照需要1至32个多边形')
    polygons = [checkpoint_loop(polygon, [], 1) for polygon in polygons]
    lat0, lon0 = polygons[0][0]
    sy = math.pi * EARTH_RADIUS_M / 180
    sx = sy * math.cos(math.radians(lat0))

    def flat(point):
        lat, lon = coordinate(*point)
        return (lon-lon0)*sx, (lat-lat0)*sy

    shapes = [[flat(point) for point in polygon] for polygon in polygons]

    def cross(a, b):
        return a[0]*b[1] - a[1]*b[0]

    def inside(point, shape):
        x, y = point
        result = False
        for a, b in zip(shape, shape[1:] + shape[:1]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            fraction = max(0, min(1, ((x-a[0])*dx + (y-a[1])*dy)/(dx*dx + dy*dy)))
            if math.hypot(x-a[0]-fraction*dx, y-a[1]-fraction*dy) <= 1e-5:
                return True
            if (a[1] > y) != (b[1] > y) and x < dx*(y-a[1])/dy + a[0]:
                result = not result
        return result

    def allowed(point):
        return any(inside(point, shape) for shape in shapes)

    points = [flat((p['lat'], p['lon'])) for p in track]
    if not points or not all(allowed(point) for point in points):
        raise ValueError('轨迹存在本地围栏快照以外的点')
    for a, b in zip(points, points[1:]):
        vector = b[0]-a[0], b[1]-a[1]
        cuts = [0.0, 1.0]
        for shape in shapes:
            for c, d in zip(shape, shape[1:] + shape[:1]):
                edge = d[0]-c[0], d[1]-c[1]
                denominator = cross(vector, edge)
                if abs(denominator) < 1e-10:
                    continue
                delta = c[0]-a[0], c[1]-a[1]
                t, u = cross(delta, edge)/denominator, cross(delta, vector)/denominator
                if 0 < t < 1 and 0 <= u <= 1:
                    cuts.append(t)
        cuts.sort()
        for left, right in zip(cuts, cuts[1:]):
            fraction = (left+right)/2
            if not allowed((a[0]+vector[0]*fraction, a[1]+vector[1]*fraction)):
                raise ValueError('轨迹线段穿出本地围栏快照')
    return {'status': 'snapshot_only', 'captured_date': captured, 'source': source,
            'polygon_count': len(shapes), 'note': '仅本地快照检查，不是实时学校规则验证'}
