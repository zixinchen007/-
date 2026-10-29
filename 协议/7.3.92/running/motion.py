"""Pure local jogging simulation: one source for distance, speed and steps."""
import bisect
from dataclasses import dataclass
import math
import random

from .consistency import validate_totals


@dataclass(frozen=True)
class MotionOptions:
    seed: int | None = None
    variation: float = .05
    correlation_seconds: float = 30.0
    cadence_center: float = 135.0
    cadence_min: float = 125.0
    cadence_max: float = 145.0
    min_speed_m_s: float | None = None
    max_speed_m_s: float | None = None


@dataclass(frozen=True)
class MotionTimeline:
    seed: int
    duration_s: int
    speeds_m_s: tuple[float, ...]
    cadences_spm: tuple[float, ...]
    cumulative_distances_m: tuple[float, ...]
    cumulative_steps: tuple[float, ...]

    @property
    def total_distance_m(self):
        return self.cumulative_distances_m[-1]

    @property
    def total_steps(self):
        return round(self.cumulative_steps[-1])

    def validate_target(self, meters, seconds, steps=None):
        if seconds != self.duration_s or not math.isclose(
                meters, self.total_distance_m, rel_tol=1e-12, abs_tol=1e-7):
            raise ValueError('运动时间序列与目标距离或时长不一致')
        if steps is not None and steps != self.total_steps:
            raise ValueError('总步数必须来自同一运动时间序列')

    def _position(self, elapsed_ms):
        if not math.isfinite(elapsed_ms):
            raise ValueError('时间必须是有限数值')
        seconds = max(0.0, min(self.duration_s, elapsed_ms / 1000))
        index = min(self.duration_s - 1, int(seconds))
        return index, seconds - index

    def counters_at(self, elapsed_ms):
        index, fraction = self._position(elapsed_ms)
        if index + fraction == self.duration_s:
            return self.total_distance_m, self.cumulative_steps[-1]
        v0, v1 = self.speeds_m_s[index:index + 2]
        c0, c1 = self.cadences_spm[index:index + 2]
        return (self.cumulative_distances_m[index] + v0 * fraction + (v1 - v0) * fraction**2 / 2,
                self.cumulative_steps[index] + (c0 * fraction + (c1 - c0) * fraction**2 / 2) / 60)

    def speed_at(self, elapsed_ms):
        index, fraction = self._position(elapsed_ms)
        return self.speeds_m_s[index] * (1 - fraction) + self.speeds_m_s[index + 1] * fraction

    def time_at_distance(self, meters):
        if not math.isfinite(meters) or not 0 <= meters <= self.total_distance_m:
            raise ValueError('累计距离超出运动时间序列')
        if meters == self.total_distance_m:
            return self.duration_s * 1000.0
        index = min(self.duration_s - 1,
                    max(0, bisect.bisect_right(self.cumulative_distances_m, meters) - 1))
        lower, upper = index * 1000.0, (index + 1) * 1000.0
        for _ in range(40):
            middle = (lower + upper) / 2
            if self.counters_at(middle)[0] < meters:
                lower = middle
            else:
                upper = middle
        return (lower + upper) / 2


def _correlated(rng, count, correlation_seconds):
    rho = math.exp(-1 / correlation_seconds)
    sigma = math.sqrt(1 - rho * rho)
    value = 0.0
    result = []
    for _ in range(count):
        value = rho * value + sigma * rng.gauss(0, 1)
        result.append(math.tanh(value))
    return result


def _integral(values):
    return math.fsum((a + b) / 2 for a, b in zip(values, values[1:]))


def build_timeline(meters, seconds, options=None):
    validate_totals(seconds, 0, 0, seconds * 1000, meters)
    options = options or MotionOptions()
    if seconds < 60:
        raise ValueError('连续慢跑模型至少需要 60 秒')
    if options.seed is not None and (isinstance(options.seed, bool) or not isinstance(options.seed, int)):
        raise ValueError('随机种子必须是整数')
    parameters = (options.variation, options.correlation_seconds, options.cadence_center,
                  options.cadence_min, options.cadence_max)
    if not all(math.isfinite(value) for value in parameters):
        raise ValueError('运动参数必须是有限数值')
    if not (0 < options.variation <= .2 and options.correlation_seconds > 0 and
            0 < options.cadence_min <= options.cadence_center <= options.cadence_max):
        raise ValueError('运动波动或步频参数无效')
    for bound in (options.min_speed_m_s, options.max_speed_m_s):
        if bound is not None and (not math.isfinite(bound) or bound <= 0):
            raise ValueError('速度边界必须是正的有限数值')
    seed = options.seed if options.seed is not None else random.SystemRandom().randrange(2**63)
    rng = random.Random(seed)
    mean = meters / seconds
    noise = _correlated(rng, seconds + 1, options.correlation_seconds)
    envelope = [min(1, .85 + .15 * t / 10) * min(1, .9 + .1 * (seconds - t) / 10)
                for t in range(seconds + 1)]
    lower = [max(mean * e * (1 - options.variation), options.min_speed_m_s or 0) for e in envelope]
    upper = [min(mean * e * (1 + options.variation), options.max_speed_m_s or math.inf) for e in envelope]
    if any(a > b for a, b in zip(lower, upper)) or not _integral(lower) <= meters <= _integral(upper):
        raise ValueError('指定距离、时长与速度约束无法同时满足')
    raw = [mean * e * (1 + options.variation * z) for e, z in zip(envelope, noise)]

    def scaled(factor):
        return [max(lo, min(hi, value * factor)) for value, lo, hi in zip(raw, lower, upper)]

    low, high = 0.0, 1.0
    while _integral(scaled(high)) < meters:
        high *= 2
    for _ in range(70):
        middle = (low + high) / 2
        if _integral(scaled(middle)) < meters:
            low = middle
        else:
            high = middle
    speeds = scaled((low + high) / 2)
    cadence_noise = _correlated(rng, seconds + 1, 15.0)
    cadences = [max(options.cadence_min, min(options.cadence_max,
                options.cadence_center * (speed / mean)**.35 + 2 * z))
                for speed, z in zip(speeds, cadence_noise)]
    distances, steps = [0.0], [0.0]
    for v0, v1, c0, c1 in zip(speeds, speeds[1:], cadences, cadences[1:]):
        distances.append(distances[-1] + (v0 + v1) / 2)
        steps.append(steps[-1] + (c0 + c1) / 120)
    distances[-1] = float(meters)
    return MotionTimeline(seed, seconds, tuple(speeds), tuple(cadences), tuple(distances), tuple(steps))
