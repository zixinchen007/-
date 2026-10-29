"""Follow an explicit closed loop instead of connecting checkpoints by chords."""
import math

from .consistency import coordinate, distance, interpolate, EARTH_RADIUS_M


def checkpoint_loop(waypoints, fixed_points, meters):
    """Keep one direction around a simple loop, with access paths of at most 25m."""
    ring = []
    for point in waypoints:
        point = coordinate(*point)
        if not ring or distance(ring[-1], point) > .01:
            ring.append(point)
    if len(ring) > 1 and distance(ring[0], ring[-1]) <= .01:
        ring.pop()
    if not 3 <= len(ring) <= 512:
        raise ValueError('闭合跑道需要 3 至 512 个不同坐标点')

    lat0, lon0 = ring[0]
    scale = math.pi * EARTH_RADIUS_M / 180
    scale_x = scale * math.cos(math.radians(lat0))

    def xy(point):
        return (point[1] - lon0) * scale_x, (point[0] - lat0) * scale

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    flat = [xy(point) for point in ring]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(flat, flat[1:] + flat[:1]))
    if abs(area) < 1:
        raise ValueError('跑道不能退化为一条线')
    for i, a in enumerate(flat):
        b = flat[(i + 1) % len(flat)]
        for j in range(i + 2, len(flat)):
            if i == 0 and j == len(flat) - 1:
                continue
            c, d = flat[j], flat[(j + 1) % len(flat)]
            if (max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
                    and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
                    and cross(a, b, c) * cross(a, b, d) <= 0
                    and cross(c, d, a) * cross(c, d, b) <= 0):
                raise ValueError('跑道边界不能交叉')
    if not fixed_points:
        return ring

    # Project each checkpoint onto its closest edge. These are short access
    # paths; no coordinate or order in the official checkpoint list is changed.
    events = [(float(i), point, None) for i, point in enumerate(ring)]
    for index, fixed in enumerate(fixed_points):
        target = coordinate(fixed.lat, fixed.lon)
        x, y = xy(target)
        choices = []
        for i, a in enumerate(flat):
            b = flat[(i + 1) % len(flat)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
            projected = interpolate(ring[i], ring[(i + 1) % len(ring)], fraction)
            choices.append((distance(target, projected), (i + fraction) % len(ring), projected))
        gap, position, projected = min(choices)
        if gap > 25:
            raise ValueError(f'官方点位 {index + 1} 距跑道 {gap:.1f} 米，超过 25 米；请重新校准模板')
        events.append((position, projected, index))

    attachments = [None] * len(fixed_points)
    ring = []
    for _, point, index in sorted(events, key=lambda event: event[0]):
        if not ring or distance(ring[-1], point) > .01:
            ring.append(point)
        if index is not None:
            attachments[index] = len(ring) - 1
    if distance(ring[-1], ring[0]) <= .01:
        attachments = [0 if index == len(ring) - 1 else index for index in attachments]
        ring.pop()

    cumulative = [0.0]
    for a, b in zip(ring, ring[1:] + ring[:1]):
        cumulative.append(cumulative[-1] + distance(a, b))
    perimeter = cumulative[-1]

    def required_arc(direction):
        return sum(((cumulative[b] - cumulative[a]) * direction) % perimeter
                   for a, b in zip(attachments, attachments[1:]))

    direction = min((1, -1), key=required_arc)
    path = [(fixed_points[0].lat, fixed_points[0].lon)]
    length = 0.0

    def append(point):
        nonlocal length
        gap = distance(path[-1], point)
        if gap > .01:
            path.append(point)
            length += gap
        if len(path) > 100000:
            raise ValueError('跑道圈数过多')

    current = attachments[0]
    append(ring[current])
    for target, fixed in zip(attachments[1:], fixed_points[1:]):
        while current != target:
            current = (current + direction) % len(ring)
            append(ring[current])
        append((fixed.lat, fixed.lon))
        if length > meters + .001:
            raise ValueError('目标距离不足以沿跑道按顺序经过所有官方点位')
        append(ring[current])
    # Finish any remaining distance on the same loop. Access paths are only
    # visited when the next official checkpoint needs them, not on every lap.
    while length < meters:
        current = (current + direction) % len(ring)
        append(ring[current])
    return path
