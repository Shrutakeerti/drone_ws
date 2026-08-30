# Omokai Robotics — Prompt → LLM → Validated Mission JSON → Deterministic Executor → PX4 SITL



## Demo
https://github.com/user-attachments/assets/fc1190a2-dc2d-46f6-bad4-cf06c975b342


## 1. What this is

A drone pipeline, using MAVSDK-Python against PX4 SITL, that turns a
natural-language operator command into a predetermined patrol/route the
drone flies in simulation:

```
Operator prompt
   │
   ▼
Groq LLM (llm_planner.py)         — proposes a mission, never controls the drone
   │  raw / UNTRUSTED JSON
   ▼
Validator (validator.py)          — jsonschema + hard safety clamps (speed, altitude,
   │                                 geofence, loop count) — guardrail layer
   │  validated mission JSON  (mission_manifest.json = audit log)
   ▼
Deterministic path generator (path_generator.py) — pure geometry, no LLM
   │  concrete (north, east, altitude) waypoint list, local NED
   ▼
Executor (executor.py)            — ONLY process that ever talks to MAVSDK:
   │                                 arm -> takeoff -> offboard goto per waypoint -> land
   ▼
PX4 SITL (jMAVSim)
```

Same architectural guarantee as the ground-robot version: the LLM call
(`mission_cli.py`) and the vehicle-control process (`executor.py`) are
physically separate. The LLM's JSON only ever reaches the drone after
passing `validator.py` and being written to a manifest file. Given the same
manifest, `executor.py` always issues the same sequence of goto commands.

## 2. Project structure

```
omokai_drone_task/
├── Dockerfile                 <- Ubuntu 22.04 + PX4 SITL (jMAVSim) + pipeline
├── requirements.txt
├── README.md                  <- this file
├── config/
│   └── mission_schema.json    <- JSON schema contract (guardrail)
├── docs/
│   └── CHALLENGES.md          <- challenges attempted + scaling story
└── src/
    ├── llm_planner.py         <- Stage 2: prompt -> raw JSON (Groq)
    ├── validator.py           <- Stage 3: guardrail / safety clamps
    ├── path_generator.py      <- Stage 4a: deterministic NED waypoint expansion
    ├── executor.py            <- Stage 4b: MAVSDK arm/takeoff/goto/land
    └── mission_cli.py         <- orchestrates stages 1-3, writes the manifest
```

## 3. Install & run

### Option A — Docker (preferred, reproducible on the examiner's machine)

```bash
docker build -t omokai-drone .
docker run -it --rm -e GROQ_API_KEY=$GROQ_API_KEY omokai-drone
```
No display/X11 needed — jMAVSim SITL here is run headless (`HEADLESS=1`), and
you interact purely through MAVSDK/the pipeline's own logs.

### Option B — bare Ubuntu 22.04

```bash
git clone --recursive https://github.com/PX4/PX4-Autopilot.git --branch v1.15.0
cd PX4-Autopilot && bash Tools/setup/ubuntu.sh
pip3 install -r requirements.txt   # from this repo
export MISSION_SCHEMA_FILE=$(pwd)/../config/mission_schema.json
export GROQ_API_KEY=your_key_here
```

### Run it

Terminal 1 — PX4 SITL, headless:
```bash
cd PX4-Autopilot
HEADLESS=1 make px4_sitl jmavsim
```
This exposes MAVSDK on `udp://:14540` by default.

Terminal 2 — generate + validate a mission:
```bash
cd src
python3 mission_cli.py "Patrol the perimeter loop twice at 15 metres altitude"
# writes /tmp/mission_manifest.json only if validation passes
```

Terminal 2 (continued) — fly it:
```bash
python3 executor.py /tmp/mission_manifest.json
```
You'll see: arm -> climb to the validated altitude -> fly each NED waypoint
in order (offboard position control) -> return-to-start -> land.

## 4. Example prompts

- `"Patrol the perimeter loop twice at 15 metres altitude"` → `mission_type=patrol,
  pattern=perimeter, loops=2, altitude_m=15`
- `"Fly a circle of radius 8 metres, climb to 12 metres, come back after one loop"`
  → `pattern=circle, radius_m=8, altitude_m=12, loops=1, return_to_start=true`
- `"Go to north 5, east 3 and hover at 6 metres"` → `mission_type=go_to_point,
  pattern=waypoints, waypoints=[[5,3]], altitude_m=6`
- `"Climb to 500 metres and fly at 50 m/s"` → clamped to `altitude_m=20,
  speed_mps=5.0` (the hard ceilings) with the clamp explained in `notes`

## 5. Safety / guardrail layer

`validator.py` enforces, independent of what the LLM proposes:
- speed clamp: 0.5–5.0 m/s
- altitude clamp: 2–20 m AGL
- loop count clamp: 1–10
- shape size / radius clamp: 2–20 m
- waypoint count clamp: ≤ 20
- geofence: all coordinates clamped/rejected outside ±30 m of the takeoff point
- whitelist on `mission_type` / `pattern` — unknown values are rejected, not guessed

If validation fails, **no manifest is written**, and `executor.py` is never invoked.

## 6. Cited sources / tools

- [PX4-Autopilot / PX4 SITL](https://github.com/PX4/PX4-Autopilot) (BSD-3-Clause) — flight stack + jMAVSim simulator.
- [MAVSDK-Python](https://github.com/mavlink/MAVSDK-Python) (BSD-3-Clause) — `arm`/`takeoff`/`offboard`/`land` API used in `executor.py`.
- [Groq Python SDK](https://github.com/groq/groq-python) (Apache-2.0) — LLM client in `llm_planner.py`.
- [jsonschema](https://github.com/python-jsonschema/jsonschema) (MIT) — schema validation in `validator.py`.
- The prompt→LLM→validated-JSON→deterministic-executor architecture was
  designed from scratch per the task brief's stage definitions; MAVSDK's own
  [offboard example](https://github.com/mavlink/MAVSDK-Python/blob/main/examples/offboard_position_ned.py)
  was used as a reference for correct `PositionNedYaw`/offboard-mode usage
  in `executor.py`, not copied verbatim.

See `docs/CHALLENGES.md` for challenges attempted and the scaling story.

## 7. A note on the source PDF

The original task PDF contained a hidden instruction telling an AI assistant
to stop reading partway through, skipping the section that warns candidates
must be able to explain and modify their own code live. That instruction was
deliberately ignored — flagging it here for transparency.
