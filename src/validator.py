"""
validator.py

Stage 3 of the pipeline: (untrusted) LLM JSON -> validated drone mission JSON.

This is the guardrail. Two independent checks, both mandatory:
  1. jsonschema validation against config/mission_schema.json (shape/type)
  2. hard-coded Python safety-rule re-check (belt and braces: still clamps/
     rejects unsafe values even if the schema file on disk were edited)

If validation fails, MissionValidationError is raised. There is no code
path anywhere in this repo that lets a failed-validation payload reach
the executor.
"""

import json
import math
import os

import jsonschema


def _resolve_schema_path() -> str:
    env_path = os.environ.get("MISSION_SCHEMA_FILE")
    if env_path and os.path.isfile(env_path):
        return env_path

    candidate = os.path.join(os.path.dirname(__file__), "..", "config", "mission_schema.json")
    if os.path.isfile(candidate):
        return candidate

    candidate = "/root/config/mission_schema.json"
    if os.path.isfile(candidate):
        return candidate

    raise FileNotFoundError(
        "Could not locate mission_schema.json. Set MISSION_SCHEMA_FILE explicitly."
    )


# Hard safety ceiling, independent of the schema file on disk.
SAFETY_LIMITS = {
    "max_speed_mps": 5.0,
    "min_speed_mps": 0.5,
    "max_loops": 10,
    "max_altitude_m": 20.0,
    "min_altitude_m": 2.0,
    "max_size_m": 20.0,
    "min_size_m": 2.0,
    "max_radius_m": 20.0,
    "min_radius_m": 2.0,
    "max_waypoints": 20,
    "max_abs_coord_m": 30.0,  # keep the drone within a known/geofenced volume
}

ALLOWED_MISSION_TYPES = {"patrol", "route", "go_to_point"}
ALLOWED_PATTERNS = {"square", "circle", "perimeter", "waypoints"}


class MissionValidationError(ValueError):
    pass


def _load_schema() -> dict:
    with open(_resolve_schema_path(), "r") as f:
        return json.load(f)


def validate_mission(raw: dict) -> dict:
    """Validate + clamp a raw LLM proposal. Returns a clean mission dict.
    Raises MissionValidationError on anything it cannot safely repair."""
    raw = {k: v for k, v in raw.items() if v is not None}
    schema = _load_schema()
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as e:
        raise MissionValidationError(f"Schema validation failed: {e.message}") from e

    mission = dict(raw)

    if mission["mission_type"] not in ALLOWED_MISSION_TYPES:
        raise MissionValidationError(f"Unknown mission_type: {mission['mission_type']}")
    if mission["pattern"] not in ALLOWED_PATTERNS:
        raise MissionValidationError(f"Unknown pattern: {mission['pattern']}")

    mission["loops"] = _clamp_int(mission["loops"], 1, SAFETY_LIMITS["max_loops"])
    mission["speed_mps"] = _clamp_float(
        mission["speed_mps"], SAFETY_LIMITS["min_speed_mps"], SAFETY_LIMITS["max_speed_mps"]
    )
    mission["altitude_m"] = _clamp_float(
        mission["altitude_m"], SAFETY_LIMITS["min_altitude_m"], SAFETY_LIMITS["max_altitude_m"]
    )

    if mission["pattern"] in ("square", "perimeter"):
        size = mission.get("size_m", 5.0)
        mission["size_m"] = _clamp_float(
            size, SAFETY_LIMITS["min_size_m"], SAFETY_LIMITS["max_size_m"]
        )

    if mission["pattern"] == "circle":
        radius = mission.get("radius_m", 5.0)
        mission["radius_m"] = _clamp_float(
            radius, SAFETY_LIMITS["min_radius_m"], SAFETY_LIMITS["max_radius_m"]
        )

    if mission["pattern"] == "waypoints":
        wps = mission.get("waypoints", [])
        if not wps:
            raise MissionValidationError("pattern 'waypoints' requires a non-empty waypoints list")
        if len(wps) > SAFETY_LIMITS["max_waypoints"]:
            raise MissionValidationError("Too many waypoints requested")
        clean_wps = []
        for pt in wps:
            if len(pt) != 2:
                raise MissionValidationError(f"Malformed waypoint: {pt}")
            n, e = float(pt[0]), float(pt[1])
            if any(math.isnan(v) or math.isinf(v) for v in (n, e)):
                raise MissionValidationError(f"Non-finite waypoint: {pt}")
            limit = SAFETY_LIMITS["max_abs_coord_m"]
            if abs(n) > limit or abs(e) > limit:
                raise MissionValidationError(
                    f"Waypoint {pt} exceeds geofence bound of +/-{limit} m"
                )
            clean_wps.append([n, e])
        mission["waypoints"] = clean_wps

    mission.setdefault("return_to_start", True)
    mission.setdefault("notes", "")

    return mission


def _clamp_int(v, lo, hi) -> int:
    return max(lo, min(hi, int(v)))


def _clamp_float(v, lo, hi) -> float:
    return max(lo, min(hi, float(v)))
