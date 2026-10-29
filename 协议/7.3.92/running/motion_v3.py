"""Python reference port of Neko motion-v3 (CC BY-NC 4.0).

Derived from LightWhite520/NekoSportsWorldTool commit
bb52858b1c9273b7917819df58a029253e496422, route-planner/src/motion.rs
and biomech.rs. Attribution: repository-root THIRD_PARTY_MOTION_V3.md.
Python RNG output differs from Rust StdRng. No network or protocol imports.
"""
import bisect
from dataclasses import dataclass
import math
import random


VERSION = 'motion-v3-python-2'


def round_steps(value):
    return math.floor(value + .5)


@dataclass(frozen=True)
class MotionV3Options:
    seed: int | None = None
    max_speed_m_s: float = 6.3
    accel_m_s2: float = .65
    brake_m_s2: float = 1.
    lateral_accel_m_s2: float = 2.5
    pace_correlation_s: float = 24.
    pace_response_s: float = 4.
    pace_stddev: float = .02
    cadence_correlation_s: float = 10.
    cadence_response_s: float = 3.
    cadence_stddev: float = 1.

    def validate(self):
        if self.seed is not None and (type(self.seed) is not int or not 0 <= self.seed < 2**64):
            raise ValueError('V3 随机种子必须是 0 至 2**64-1 的整数')
        for name, value in vars(self).items():
            if name == 'seed':
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('V3 运动参数必须是有限数值')
            if value < 0 or (not name.endswith('stddev') and value == 0):
                raise ValueError('V3 约束和时间常数必须为正，标准差不能为负')
        if self.pace_stddev > .2:
            raise ValueError('V3 速度扰动标准差不能超过 0.2')


@dataclass(frozen=True)
class MotionRoute:
    points_xy: tuple
    stations_m: tuple
    curvatures: tuple

    @property
    def length_m(self):
        return self.stations_m[-1]

    @classmethod
    def from_xy(cls, points, stations=None):
        points = tuple(tuple(p) for p in points)
        if len(points) < 2 or len(points) > 500000 or any(
                len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                  or not math.isfinite(v) for v in p) for p in points):
            raise ValueError('运动路线需要至少两个有限坐标')
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        if any(d < 1e-6 for d in lengths):
            raise ValueError('运动路线含重复或过近节点')
        if stations is None:
            values = [0.]
            for d in lengths:
                values.append(values[-1] + d)
            stations = tuple(values)
        else:
            stations = tuple(stations)
        if (len(stations) != len(points) or stations[0] != 0
                or not all(isinstance(s, (int, float)) and not isinstance(s, bool)
                           and math.isfinite(s) for s in stations)
                or any(b <= a for a, b in zip(stations, stations[1:]))
                or not 0 < stations[-1] <= 100000):
            raise ValueError('运动路线累计距离必须严格递增且不超过 100 km')
        curvature = []
        closed = points[0] == points[-1]
        for i, p in enumerate(points):
            if 0 < i < len(points)-1:
                a, b = points[i-1], points[i+1]
            elif closed and len(points) > 3:
                a, b = points[-2], points[1]
            else:
                curvature.append(0.)
                continue
            u, v = (p[0]-a[0], p[1]-a[1]), (b[0]-p[0], b[1]-p[1])
            angle = abs(math.atan2(u[0]*v[1]-u[1]*v[0], u[0]*v[0]+u[1]*v[1]))
            curvature.append(angle / max(1., (math.dist(a,p)+math.dist(p,b))/2))
        return cls(points, stations, tuple(curvature))

    def position_at(self, meters):
        meters = max(0., min(self.length_m, meters))
        i = min(len(self.stations_m)-1, max(1, bisect.bisect_left(self.stations_m, meters)))
        f = (meters-self.stations_m[i-1])/(self.stations_m[i]-self.stations_m[i-1])
        a, b = self.points_xy[i-1:i+1]
        return a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1])

    @classmethod
    def loop(cls, points, meters):
        if isinstance(meters, bool) or not isinstance(meters, (int,float)) or not math.isfinite(meters) or not 0 < meters <= 100000:
            raise ValueError('目标距离必须在 0 至 100 km 内')
        base = cls.from_xy(points)
        if base.points_xy[0] != base.points_xy[-1]:
            raise ValueError('环线必须闭合')
        expanded, stations = [], []
        offset = 0.
        while offset < meters:
            for p, s in zip(base.points_xy, base.stations_m):
                s += offset
                if s >= meters:
                    break
                if not stations or s > stations[-1]:
                    expanded.append(p)
                    stations.append(s)
            offset += base.length_m
            if len(expanded) > 500000:
                raise ValueError('路线采样规模过大')
        expanded.append(base.position_at(meters % base.length_m))
        stations.append(float(meters))
        return cls.from_xy(expanded, stations)

    def max_lateral_acceleration(self, timeline):
        """Check vertex crossings inside each constant-acceleration time step."""
        peak = 0.
        for s,k in zip(self.stations_m,self.curvatures):
            i = min(timeline.duration_s,max(1,bisect.bisect_left(
                timeline.cumulative_distances_m,s)))
            a,b = timeline.speeds_m_s[i-1:i+1]
            v2 = a*a+2*(b-a)*(s-timeline.cumulative_distances_m[i-1])
            peak = max(peak,max(0.,v2)*abs(k))
        return peak

    def validate_bends(self, timeline, limit, tolerance=.1):
        # The reference look-ahead scheme can miss a sharp corner within a
        # second. Retain its output for diagnostics but reject it at integration.
        peak = self.max_lateral_acceleration(timeline)
        if peak > limit+tolerance:
            raise ValueError(f'弯道求解后校验失败：{peak:.3f} m/s² 超过 {limit:g}+{tolerance:g}')


class SmoothNoise:
    """AR(1) target followed by a low-pass response, with variance compensation."""
    def __init__(self, correlation_s, response_s, stddev):
        self.memory = math.exp(-1/correlation_s)
        self.response = math.exp(-1/response_s)
        m, r = self.memory, self.response
        variance_ratio = (1-r)*(1+m*r)/((1+r)*(1-m*r))
        self.innovation = stddev*math.sqrt((1-m*m)/variance_ratio)
        self.target = self.value = 0.

    def step(self, rng):
        self.target = self.memory*self.target + self.innovation*rng.gauss(0,1)
        self.value = self.response*self.value + (1-self.response)*self.target
        return self.value


def gait_cadence(v):
    cadence = max(110., min(220., 150.+12.*v))
    stride = v*60./cadence
    if stride > 1.7:
        cadence = v*60./1.7
    elif stride < .5:
        cadence = v*60./.5
    return cadence


@dataclass(frozen=True)
class MotionV3Timeline:
    seed: int
    duration_s: int
    speeds_m_s: tuple
    cumulative_distances_m: tuple
    cumulative_steps: tuple
    interval_cadences_spm: tuple

    @property
    def total_distance_m(self):
        return self.cumulative_distances_m[-1]

    @property
    def total_steps(self):
        return round_steps(self.cumulative_steps[-1])

    @property
    def cadences_spm(self):
        # Display only: t>0 shows the cadence for the preceding interval.
        return (0.,) + self.interval_cadences_spm

    @staticmethod
    def round_steps(value):
        return round_steps(value)

    def validate_target(self, meters, seconds, steps=None):
        if seconds != self.duration_s or not math.isclose(meters, self.total_distance_m, rel_tol=0., abs_tol=1e-7):
            raise ValueError('V3 时间序列与目标距离或时长不一致')
        if steps is not None and steps != self.total_steps:
            raise ValueError('总步数必须来自同一 V3 时间序列')

    def _position(self, elapsed_ms):
        if not math.isfinite(elapsed_ms):
            raise ValueError('时间必须是有限数值')
        t = max(0., min(self.duration_s, elapsed_ms/1000))
        i = min(self.duration_s-1, int(t))
        return i, t-i

    def counters_at(self, elapsed_ms):
        i, f = self._position(elapsed_ms)
        if i+f == self.duration_s:
            return self.total_distance_m, self.cumulative_steps[-1]
        a, b = self.speeds_m_s[i:i+2]
        return (self.cumulative_distances_m[i]+a*f+(b-a)*f*f/2,
                self.cumulative_steps[i]+self.interval_cadences_spm[i]*f/60)

    def speed_at(self, elapsed_ms):
        i, f = self._position(elapsed_ms)
        return self.speeds_m_s[i]*(1-f)+self.speeds_m_s[i+1]*f

    def time_at_distance(self, meters):
        if not math.isfinite(meters) or meters < 0 or meters > self.total_distance_m+1e-7:
            raise ValueError('累计距离超出 V3 时间序列')
        if meters == 0:
            return 0.
        if abs(meters-self.total_distance_m) <= 1e-7:
            return self.duration_s*1000.
        i = min(self.duration_s-1, max(0, bisect.bisect_right(self.cumulative_distances_m, meters)-1))
        low, high = i*1000., (i+1)*1000.
        for _ in range(40):
            mid = (low+high)/2
            if self.counters_at(mid)[0] < meters:
                low = mid
            else:
                high = mid
        return (low+high)/2


def build_timeline(route, seconds, options=None):
    options = options or MotionV3Options()
    if not isinstance(options, MotionV3Options):
        raise ValueError('V3 需要 MotionV3Options')
    options.validate()
    if type(seconds) is not int or not 2 <= seconds <= 86400:
        raise ValueError('V3 时长需要 2 至 86400 的整数秒')
    # Revalidate publicly constructible dataclasses, including forged stations.
    route = MotionRoute.from_xy(route.points_xy, route.stations_m)
    seed = options.seed if options.seed is not None else random.SystemRandom().randrange(2**64)
    rng = random.Random(seed ^ 0x9e3779b97f4a7c15)
    pace = SmoothNoise(options.pace_correlation_s, options.pace_response_s, options.pace_stddev)
    noise = tuple(pace.step(rng) for _ in range(seconds))
    vmax, accel, brake = options.max_speed_m_s, options.accel_m_s2, options.brake_m_s2
    caps = [vmax*vmax if abs(k)<1e-9 else min(vmax*vmax, options.lateral_accel_m_s2/abs(k))
            for k in route.curvatures]
    # With nonnegative, linearly interpolated one-second samples, braking by
    # more than the lowest corner speed can require stopping and accelerating
    # inside one interval. Use a conservative envelope representable by this
    # timeline, while remaining within the configured physical braking limit.
    brake = min(brake, math.sqrt(min(caps)))
    stations = route.stations_m
    for i in range(len(caps)-2,-1,-1):
        caps[i] = min(caps[i], caps[i+1]+2*brake*(stations[i+1]-stations[i]))
    for i in range(1,len(caps)):
        caps[i] = min(caps[i], caps[i-1]+2*accel*(stations[i]-stations[i-1]))

    def next_speed_cap(s, v):
        # A one-second linear velocity interval can cross a corner between its
        # endpoints. Bound its end velocity by every reachable vertex, using
        # v_cross² = v² + 2*(v_next-v)*distance_to_vertex.
        upper = min(vmax, v+accel)
        lower = max(0., v-brake)
        first = bisect.bisect_right(stations, s)
        if first == len(stations):
            return upper
        end = s+(v+upper)/2
        last = min(len(stations), bisect.bisect_right(stations, end)+1)
        for index in range(first, last):
            d = stations[index]-s
            at_vertex = 2*d-v
            if at_vertex <= 0 or caps[index] >= at_vertex*at_vertex:
                limit = v+(caps[index]-v*v)/(2*d)
                violation = v*v+2*(lower-v)*d-caps[index]
            else:
                # End before the vertex and retain enough braking distance:
                # v_next² <= cap² + 2*brake*(d-(v+v_next)/2).
                discriminant = brake*brake + 4*(caps[index]+2*brake*d-brake*v)
                if discriminant < -1e-7:
                    raise ValueError('V3 弯道制动约束不可行')
                limit = (-brake+math.sqrt(max(0., discriminant)))/2
                violation = lower*lower-caps[index]-2*brake*(d-(v+lower)/2)
            if limit < lower:
                # Near-coincident sample/vertex positions amplify roundoff in
                # the velocity quotient; test the original squared constraint.
                if violation > 1e-8:
                    raise ValueError('V3 弯道所需制动超过上限')
                limit = lower
            upper = min(upper, limit)
        return max(0., upper)

    def simulate(base):
        speeds, distances = [0.], [0.]
        s = v = 0.
        for t, z in enumerate(noise, 1):
            lower = max(0., v-brake)
            upper = min(next_speed_cap(s,v), (seconds-t)*brake)
            target = max(0., base*(1+z)-lower)
            room = max(0., upper-lower)
            # A smooth minimum keeps a little room below active constraints.
            # Hard saturation can hit a corner exactly at an integer second;
            # changing the cruise target then discontinuously flips the next
            # interval from braking to accelerating, defeating distance search.
            small, large = sorted((target, room))
            increment = small/math.sqrt(1+(small/large)**2) if large else 0.
            next_v = lower+increment
            s += (v+next_v)/2
            v = next_v
            speeds.append(v)
            distances.append(s)
        return speeds, distances

    low, high = 0., vmax*4
    if simulate(high)[1][-1] < route.length_m-1e-7:
        raise ValueError('给定距离和时长无法满足加减速或弯道约束，请延长时长')
    for _ in range(64):
        mid = (low+high)/2
        speeds, distances = simulate(mid)
        residual = distances[-1]-route.length_m
        if abs(residual) <= 1e-8:
            break
        if residual < 0:
            low = mid
        else:
            high = mid
    if abs(residual) > 1e-7:
        raise ValueError('V3 运动约束求解未收敛，请调整时长')
    cadence_rng = random.Random(seed ^ 0xc6a4a7935bd1e995)
    cadence_noise = SmoothNoise(options.cadence_correlation_s, options.cadence_response_s, options.cadence_stddev)
    steps, cadences = [0.], []
    for a,b in zip(speeds,speeds[1:]):
        v = (a+b)/2
        cadence = max(80.,min(220.,gait_cadence(v)+cadence_noise.step(cadence_rng)))*min(v/1.5,1.)
        cadences.append(cadence)
        steps.append(steps[-1]+cadence/60)
    return MotionV3Timeline(seed,seconds,tuple(speeds),tuple(distances),tuple(steps),tuple(cadences))
