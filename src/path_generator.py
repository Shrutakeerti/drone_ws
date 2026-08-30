"""
path_generator.py

Stage 4a: validated mission JSON -> concrete list of (north_m, east_m,
altitude_m) waypoints, local NED, relative to the takeoff point.

Pure, deterministic geometry. No LLM call happens here or anywhere
downstream of validator.py. Same mission JSON -> same waypoint list, every
time -> this is what makes the executor auditable.
"""

import math

Waypoint = tuple[float, float, float]  # (north_m, east_m, altitude_m)


def generate_waypoints(mission: dict) -> list[Waypoint]:
    pattern = mission["pattern"]
    alt = mission["altitude_m"]

    if pattern == "waypoints":
        base = [(float(n), float(e)) for n, e in mission["waypoints"]]
    elif pattern == "square":
        base = _square(mission.get("size_m", 5.0))
    elif pattern == "perimeter":
        base = _square(mission.get("size_m", 5.0))
    elif pattern == "circle":
        base = _circle(mission.get("radius_m", 5.0), n_points=12)
    else:
        raise ValueError(f"Unknown pattern for path generation: {pattern}")

    loops = mission.get("loops", 1)
    flat_path: list[tuple[float, float]] = []
    for _ in range(loops):
        flat_path.extend(base)

    if mission.get("return_to_start", True) and flat_path and flat_path[-1] != (0.0, 0.0):
        flat_path.append((0.0, 0.0))

    return [(n, e, alt) for n, e in flat_path]


def _square(size: float) -> list[tuple[float, float]]:
    s = size
    return [(s, 0.0), (s, s), (0.0, s), (0.0, 0.0)]


def _circle(radius: float, n_points: int) -> list[tuple[float, float]]:
    pts = []
    for i in range(1, n_points + 1):
        theta = 2 * math.pi * i / n_points
        pts.append((radius * math.sin(theta), radius * (1 - math.cos(theta))))
    return pts
