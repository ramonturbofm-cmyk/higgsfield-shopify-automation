"""Time-series reduction for charts (audit P2-09).

Never "every n-th point": samples are grouped into equal *time* buckets and each bucket keeps
average, minimum and maximum (so short peaks stay visible), with the number of samples. LTTB
(largest-triangle-three-buckets) is available for shape-preserving line reduction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def time_buckets(rows: Sequence[dict], ts: Callable[[dict], float], metrics: Sequence[str],
                 get: Callable[[dict, str], float | None], start: float, end: float, buckets: int) -> list[dict]:
    """Group ``rows`` into ``buckets`` equal time windows between ``start`` and ``end``."""
    if buckets < 1 or end <= start:
        return []
    width = (end - start) / buckets
    acc: dict[int, dict[str, list[float]]] = {}
    for r in rows:
        t = ts(r)
        if not start <= t < end:
            continue
        b = min(buckets - 1, int((t - start) / width))
        slot = acc.setdefault(b, {})
        for m in metrics:
            v = get(r, m)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            slot.setdefault(m, []).append(float(v))
    out = []
    for b in sorted(acc):
        vals = acc[b]
        out.append({"ts": start + (b + 0.5) * width, "bucket_s": width,
                    "values": {m: sum(v) / len(v) for m, v in vals.items()},
                    "min": {m: min(v) for m, v in vals.items()}, "max": {m: max(v) for m, v in vals.items()},
                    "n": max((len(v) for v in vals.values()), default=0)})
    return out


def lttb(points: Sequence[tuple[float, float]], threshold: int) -> list[tuple[float, float]]:
    """Largest-Triangle-Three-Buckets downsampling (keeps first/last point and visual extremes)."""
    n = len(points)
    if threshold >= n or threshold < 3:
        return list(points)
    out = [points[0]]
    every = (n - 2) / (threshold - 2)
    a = 0
    for i in range(threshold - 2):
        s, e = int((i + 1) * every) + 1, min(int((i + 2) * every) + 1, n)
        avg_x = sum(p[0] for p in points[s:e]) / max(1, e - s)
        avg_y = sum(p[1] for p in points[s:e]) / max(1, e - s)
        rs, re = int(i * every) + 1, int((i + 1) * every) + 1
        ax, ay = points[a]
        best, best_area = rs, -1.0
        for j in range(rs, min(re, n - 1)):
            area = abs((ax - avg_x) * (points[j][1] - ay) - (ax - points[j][0]) * (avg_y - ay))
            if area > best_area:
                best, best_area = j, area
        out.append(points[best])
        a = best
    out.append(points[-1])
    return out
