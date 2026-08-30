# Challenges attempted & scaling story (drone variant)

## Not yet wired up in this variant — approach only

This drone pipeline currently implements the **core task only** (patrol/route/
go-to-point with a fixed altitude, offboard NED control). None of the three
senior challenges are wired into `executor.py` yet. Approach for each,
building on the existing prompt→JSON→validator→executor split:

### #1 — Multi-agent formations
Run N instances of PX4 SITL (`px4_sitl_multi` target supports this
natively, each vehicle on its own MAVSDK port `udp://:14540+i`). Extend
`mission_schema.json` with an `agents: [...]` list carrying per-agent
role/offset. Add a formation-expansion function next to
`path_generator.py`: given one reference waypoint list, compute each
agent's offset waypoints (e.g. wedge formation = fixed lateral/longitudinal
offsets per agent index). Spin up one `executor.py`-style process per
vehicle, each only ever reading its own agent's validated waypoints.

### #2 — SLAM / autonomous navigation
PX4 + a depth-camera-equipped SITL model (e.g. `iris_depth_camera`) plus a
ROS 2 bridge (`px4_ros_com` / `micro-ROS-Agent`) would let a SLAM stack
(RTAB-Map, given it's the only ROS 2-native visual-SLAM option in 3D) build
a map while flying. The executor would then send goals through a local
planner that consults the SLAM occupancy map instead of flying straight-line
NED waypoints blind — same validated-JSON input, different (obstacle-aware)
executor implementation.

### #3 — Vision AI target detection + follow
Attach a simulated camera (Gazebo `iris_fpv_cam` model or jMAVSim's camera
plugin), run Ultralytics YOLO on the video stream with `target_class` read
from the (schema-extended) mission JSON. On detection: publish the frame to
an operator-facing endpoint, and temporarily override the waypoint queue
with a simple proportional visual-servo controller (center + close distance
on the bounding box) via the same `offboard.set_position_ned` primitive
already in `executor.py`, resuming the patrol once the target is lost.

## Scaling to harder, real-world problems

- **Sim-to-real**: `executor.py` only depends on the MAVSDK API surface
  (`action`, `offboard`, `telemetry`) — pointing it at a real PX4-powered
  vehicle instead of SITL requires changing the connection string only.
  `validator.py`'s altitude/speed/geofence ceilings become even more
  important here and should be backed by PX4's own onboard geofence
  parameters as a second, independent layer of protection.
- **Reliability**: add a telemetry watchdog that triggers RTL (return-to-launch)
  if GPS/position health degrades mid-mission, and re-check the geofence
  continuously against live telemetry, not just once at validation time.
- **LLM robustness**: add a validator-error-feedback retry loop (one retry
  with the specific rejection reason fed back to the LLM) instead of a hard
  fail; log every prompt→raw→validated triple (the manifest already does
  this) for offline auditing.
- **Multi-vehicle fleets**: the formation-layer sketch above generalizes into
  a fleet manager that holds a shared no-fly geofence, assigns
  non-overlapping regions per vehicle, and aggregates fleet health back to
  the operator.
- **Safety certification**: `validator.py` has no I/O beyond reading a
  static schema file, making it the natural target for exhaustive
  property-based testing and independent review, separate from the LLM
  component — the same separation of concerns that makes it auditable today
  makes it easier to certify for real flight later.
