"""Bounded physical path variation, before timeline sampling; no GPS noise or I/O."""
import bisect
from dataclasses import dataclass
import hashlib
import math
import random

from .consistency import distance, interpolate, EARTH_RADIUS_M
from .routes import checkpoint_loop


@dataclass(frozen=True)
class RouteVariationOptions:
    max_offset_m: float = 2.0
    seed: int | None = None

    def validate(self):
        if isinstance(self.max_offset_m, bool) or not math.isfinite(self.max_offset_m) or not 0 <= self.max_offset_m <= 2:
            raise ValueError('路线偏移必须是0至2米的有限数值')
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError('路线种子必须是整数')


@dataclass(frozen=True)
class VariedRoute:
    points: tuple
    stations_m: tuple
    offsets_m: tuple
    connectors: tuple
    cumulative_m: tuple
    required_indices: tuple
    anchor_stations_m: tuple
    controls: tuple
    perimeter_m: float
    seed: int

    def metadata_at(self, meters):
        index = min(len(self.points) - 2, max(0, bisect.bisect_right(self.cumulative_m, meters) - 1))
        ratio = max(0, min(1, (meters - self.cumulative_m[index]) /
                           (self.cumulative_m[index + 1] - self.cumulative_m[index])))
        station = self.stations_m[index] * (1 - ratio) + self.stations_m[index + 1] * ratio
        offset = self.offsets_m[index] * (1 - ratio) + self.offsets_m[index + 1] * ratio
        return {'lap': int((station + 1e-7) // self.perimeter_m), 'station_m': station,
                'offset_m': offset, 'connector': self.connectors[index + 1]}


def _unit(x, y):
    length = math.hypot(x, y)
    if length < 1e-12:
        raise ValueError('跑道局部方向退化')
    return x / length, y / length


def _smooth(value):
    value = max(0, min(1, value))
    return value * value * (3 - 2 * value)


class _Frame:
    def __init__(self, ring):
        self.lat, self.lon = ring[0]
        self.sy = math.pi * EARTH_RADIUS_M / 180
        self.sx = self.sy * math.cos(math.radians(self.lat))
        if abs(self.sx) < 1:
            raise ValueError('跑道过于接近极点')
        self.flat = [self.xy(point) for point in ring]
        edges = list(zip(self.flat, self.flat[1:] + self.flat[:1]))
        sign = 1 if sum(a[0]*b[1] - b[0]*a[1] for a, b in edges) > 0 else -1
        normals = [_unit(sign * (b[1] - a[1]), -sign * (b[0] - a[0])) for a, b in edges]
        self.normals = [_unit(normals[i - 1][0] + normals[i][0], normals[i - 1][1] + normals[i][1])
                        for i in range(len(ring))]
        self.cumulative = [0.0]
        for a, b in edges:
            self.cumulative.append(self.cumulative[-1] + math.dist(a, b))
        self.perimeter = self.cumulative[-1]

    def xy(self, point):
        return (point[1] - self.lon) * self.sx, (point[0] - self.lat) * self.sy

    def geo(self, point):
        return self.lat + point[1] / self.sy, self.lon + point[0] / self.sx

    def project(self, point):
        x, y = self.xy(point)
        best = None
        for index, a in enumerate(self.flat):
            b = self.flat[(index + 1) % len(self.flat)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = max(0, min(1, ((x - a[0])*dx + (y - a[1])*dy) / (dx*dx + dy*dy)))
            gap = math.hypot(x - a[0] - fraction*dx, y - a[1] - fraction*dy)
            if best is None or gap < best[0]:
                left, right = self.normals[index], self.normals[(index + 1) % len(self.normals)]
                normal = _unit(left[0]*(1-fraction) + right[0]*fraction,
                               left[1]*(1-fraction) + right[1]*fraction)
                station = self.cumulative[index] + fraction * math.hypot(dx, dy)
                best = gap, station % self.perimeter, normal
        return best

    def inside(self, point):
        x, y = self.xy(point)
        inside = False
        for a, b in zip(self.flat, self.flat[1:] + self.flat[:1]):
            if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1]) + a[0]:
                inside = not inside
        return inside


def _check_crossings(route, frame):
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    groups = {}
    for index, (a, b) in enumerate(zip(route.points, route.points[1:])):
        if route.connectors[index + 1]:
            continue
        lap = int((route.stations_m[index] + 1e-7) // route.perimeter_m)
        groups.setdefault(lap, []).append((frame.xy(a), frame.xy(b)))
    for segments in groups.values():
        for index, (a, b) in enumerate(segments):
            for c, d in segments[index + 2:]:
                if (max(min(a[0], b[0]), min(c[0], d[0])) > min(max(a[0], b[0]), max(c[0], d[0])) or
                        max(min(a[1], b[1]), min(c[1], d[1])) > min(max(a[1], b[1]), max(c[1], d[1]))):
                    continue
                if cross(a, b, c)*cross(a, b, d) < -1e-10 and cross(c, d, a)*cross(c, d, b) < -1e-10:
                    raise ValueError('偏移路线在单圈内出现异常自交')


def build_varied_route(loop, fixed_points, meters, options):
    options.validate()
    if isinstance(meters, bool) or not isinstance(meters, (int, float)) or not math.isfinite(meters) or meters <= 0:
        raise ValueError('目标距离必须为正的有限数值')
    ring = checkpoint_loop(loop, [], meters)
    frame = _Frame(ring)
    coverage = max(meters + frame.perimeter, (len(fixed_points) + 2)*frame.perimeter + 300)
    if coverage / frame.perimeter * len(ring) > 100000 or coverage / 5 > 100000:
        raise ValueError('路线采样点过多，请缩短目标距离')
    if fixed_points:
        reference = checkpoint_loop(ring, fixed_points, coverage)
    else:
        count = math.ceil(coverage / frame.perimeter)
        reference = ring * count + [ring[0]]
    # Only these ordered occurrences are required; later natural passes are free.
    required = []
    cursor = 0
    for fixed in fixed_points:
        while cursor < len(reference) and distance(reference[cursor], (fixed.lat, fixed.lon)) > .011:
            cursor += 1
        if cursor == len(reference):
            raise ValueError('无法按顺序保留必经点')
        reference[cursor] = (fixed.lat, fixed.lon)
        required.append(cursor)
        cursor += 1
    projections = [frame.project(point) for point in reference]
    deltas = [((b[1] - a[1] + frame.perimeter/2) % frame.perimeter - frame.perimeter/2)
              for a, b in zip(projections, projections[1:])]
    direction = 1 if sum(deltas) > 0 else -1
    stations, connectors = [0.0], [False]
    for index, delta in enumerate(deltas):
        connector = projections[index][0] > .02 or projections[index + 1][0] > .02
        advance = 0 if connector else direction * delta
        if advance < -.02:
            raise ValueError('基准跑道存在异常折返')
        stations.append(stations[-1] + max(0, advance))
        connectors.append(connector)
    anchors = tuple(stations[index] for index in required)
    root_seed = options.seed if options.seed is not None else random.SystemRandom().randrange(2**63)
    seed = int.from_bytes(hashlib.sha256(f'lap-variation-v1:{root_seed}'.encode()).digest()[:8], 'big')
    rng = random.Random(seed)
    controls = [rng.uniform(0, options.max_offset_m)]
    for _ in range(math.ceil(stations[-1] / 50) + 1):
        value = controls[-1] + rng.uniform(-.5, .5)
        # Reflection keeps both the bounds and the adjacent-control limit.
        if options.max_offset_m:
            value %= 2 * options.max_offset_m
            value = min(value, 2 * options.max_offset_m - value)
        else:
            value = 0.0
        controls.append(value)

    def displaced(point, station, connector):
        if connector:
            return point, 0.0
        index = min(len(controls) - 2, int(station / 50))
        weight = _smooth(station / 50 - index)
        offset = controls[index]*(1-weight) + controls[index + 1]*weight
        offset *= min((_smooth(abs(station - event) / 25) for event in anchors), default=1.0)
        if offset < 1e-12:
            return point, 0.0
        _, _, normal = frame.project(point)
        x, y = frame.xy(point)
        shifted = frame.geo((x + normal[0]*offset, y + normal[1]*offset))
        if frame.project(shifted)[0] > options.max_offset_m + 1e-5 or frame.inside(shifted):
            raise ValueError('偏移路线离开外侧允许范围')
        return shifted, offset

    first, initial_offset = displaced(reference[0], 0, projections[0][0] > .02)
    points, progress, offsets, connection, cumulative = [first], [0.0], [initial_offset], [projections[0][0] > .02], [0.0]
    required_output = [0] if required and required[0] == 0 else []
    required_set = set(required)

    def append(point, station, offset, connector):
        gap = distance(points[-1], point)
        if gap < 1e-7:
            return
        pieces = max(1, math.ceil(gap / 5))
        previous, q0, d0 = points[-1], progress[-1], offsets[-1]
        for step in range(1, pieces + 1):
            fraction = step / pieces
            new = interpolate(previous, point, fraction)
            cumulative.append(cumulative[-1] + distance(points[-1], new))
            points.append(new)
            progress.append(q0*(1-fraction) + station*fraction)
            offsets.append(d0*(1-fraction) + offset*fraction)
            connection.append(connector)
            if len(points) > 100000:
                raise ValueError('路线采样点过多')

    finished = False
    for edge, (a, b) in enumerate(zip(reference, reference[1:])):
        pieces = max(1, math.ceil(distance(a, b) / 5))
        for step in range(1, pieces + 1):
            fraction = step / pieces
            point = interpolate(a, b, fraction)
            station = stations[edge]*(1-fraction) + stations[edge + 1]*fraction
            shifted, offset = displaced(point, station, connectors[edge + 1])
            append(shifted, station, offset, connectors[edge + 1])
            if step == pieces and edge + 1 in required_set:
                required_output.append(len(points) - 1)
            if cumulative[-1] >= meters:
                if required and (len(required_output) != len(required) or cumulative[required_output[-1]] > meters + .001):
                    raise ValueError('目标距离不足以按顺序经过全部必经点')
                finished = True
                break
        if finished:
            break
    if not finished:
        raise ValueError('偏移路线长度不足')
    route = VariedRoute(tuple(points), tuple(progress), tuple(offsets), tuple(connection), tuple(cumulative),
                        tuple(required_output), anchors, tuple(controls), frame.perimeter, seed)
    _check_crossings(route, frame)
    return route
