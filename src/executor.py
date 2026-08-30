"""
executor.py

Stage 4b: deterministic executor -> PX4 SITL (via MAVSDK).

This is the ONLY code in the repo that ever talks to the drone. It reads a
validated mission_manifest.json (written by mission_cli.py after passing
validator.py), expands it to concrete waypoints via
path_generator.generate_waypoints(), then arms / takes off / flies each
waypoint in order / lands - using MAVSDK's high-level offboard position
API. No LLM call happens in this process; given the same manifest, this
script issues the same sequence of goto commands every time.

Usage:
    python3 executor.py /tmp/mission_manifest.json
    python3 executor.py /tmp/mission_manifest.json --connection udp://:14540
"""

import argparse
import asyncio
import json
import sys

from mavsdk import System
from mavsdk.offboard import OffboardError, PositionNedYaw

from path_generator import generate_waypoints

WAYPOINT_TOLERANCE_M = 0.5
WAYPOINT_TIMEOUT_S = 25.0


async def run(manifest_path: str, connection: str):
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
    mission = manifest["mission"]
    waypoints = generate_waypoints(mission)
    print(f"[executor] Validated mission: {mission}")
    print(f"[executor] Deterministic waypoint queue ({len(waypoints)} pts): {waypoints}")

    drone = System()
    print(f"[executor] Creating MAVSDK connection to {connection} ...")
    try:
        await asyncio.wait_for(drone.connect(system_address=connection), timeout=15)
    except asyncio.TimeoutError:
        print(
            "[executor] TIMED OUT (15s) inside drone.connect() itself.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("[executor] connect() call returned. Waiting for heartbeat/connection_state...")

    try:
        async for state in _timeout_iter(drone.core.connection_state(), timeout_s=20):
            if state.is_connected:
                print("[executor] Connected to PX4 SITL.")
                break
    except asyncio.TimeoutError:
        print("[executor] TIMED OUT waiting for a heartbeat from PX4 after 20s.", file=sys.stderr)
        sys.exit(1)

    print("[executor] Waiting for global position + home lock...")
    try:
        async for health in _timeout_iter(drone.telemetry.health(), timeout_s=30):
            if health.is_global_position_ok and health.is_home_position_ok:
                print("[executor] Position lock OK.")
                break
    except asyncio.TimeoutError:
        print("[executor] TIMED OUT waiting for GPS/home position lock after 30s.", file=sys.stderr)
        sys.exit(1)

    print("[executor] Arming...")
    await drone.action.arm()

    print(f"[executor] Taking off to {mission['altitude_m']} m...")
    await drone.action.set_takeoff_altitude(mission["altitude_m"])
    await drone.action.takeoff()
    await asyncio.sleep(8)  # let it reach altitude before switching to offboard

    print("[executor] Starting offboard mode...")
    await drone.offboard.set_position_ned(PositionNedYaw(0.0, 0.0, -mission["altitude_m"], 0.0))
    try:
        await drone.offboard.start()
    except OffboardError as e:
        print(f"[executor] Offboard start failed: {e}", file=sys.stderr)
        await drone.action.land()
        return

    for i, (north, east, alt) in enumerate(waypoints):
        print(f"[executor] Waypoint {i}: N={north:.2f} E={east:.2f} Alt={alt:.2f}")
        down = -alt
        reached = await _wait_until_reached(drone, north, east, down)
        if not reached:
            print(f"[executor] WARNING: waypoint {i} timed out, continuing to next.")

    print("[executor] Mission complete. Stopping offboard, landing.")
    try:
        await drone.offboard.stop()
    except OffboardError:
        pass
    await drone.action.land()


async def _timeout_iter(async_gen, timeout_s: float):
    async def _runner():
        async for item in async_gen:
            yield item

    it = _runner()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        item = await asyncio.wait_for(it.__anext__(), timeout=remaining)
        yield item


async def _wait_until_reached(drone: System, north: float, east: float, down: float) -> bool:
    """Continuously resend the offboard position setpoint while waiting to
    arrive. PX4's offboard mode requires a fresh setpoint at least every
    ~0.5s or it auto-exits offboard and holds position - sending the target
    once and only polling telemetry (the earlier bug) meant the vehicle
    dropped out of offboard almost immediately and never actually moved."""
    elapsed = 0.0
    step = 0.2
    target = PositionNedYaw(north, east, down, 0.0)
    last_print = 0.0
    while elapsed <= WAYPOINT_TIMEOUT_S:
        await drone.offboard.set_position_ned(target)
        try:
            pos = await drone.telemetry.position_velocity_ned().__anext__()
        except StopAsyncIteration:
            pos = None
        if pos is not None:
            dn = pos.position.north_m - north
            de = pos.position.east_m - east
            dd = pos.position.down_m - down
            dist = (dn ** 2 + de ** 2 + dd ** 2) ** 0.5
            if elapsed - last_print >= 2.0:
                print(f"[executor]   ... distance to target: {dist:.2f} m "
                      f"(current N={pos.position.north_m:.2f} E={pos.position.east_m:.2f})")
                last_print = elapsed
            if dist < WAYPOINT_TOLERANCE_M:
                return True
        await asyncio.sleep(step)
        elapsed += step
    return False


def main():
    parser = argparse.ArgumentParser(description="Deterministic MAVSDK executor for a validated mission")
    parser.add_argument("manifest", type=str, help="Path to a validated mission_manifest.json")
    parser.add_argument("--connection", type=str, default="udp://:14540")
    args = parser.parse_args()
    asyncio.run(run(args.manifest, args.connection))


if __name__ == "__main__":
    main()
