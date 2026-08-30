"""
mission_cli.py

Stage 1-3 orchestrator: Prompt -> LLM -> validated mission JSON -> manifest file.

Usage:
    python3 mission_cli.py "Patrol the perimeter loop twice at 15 metres altitude"
    python3 mission_cli.py "Fly a circle radius 8m" --out /tmp/mission_manifest.json

This process never touches MAVSDK / the drone. It only talks to the LLM and
writes a validated, timestamped manifest to disk. executor.py is a separate
process that only ever reads that manifest - the LLM has no code path to
the vehicle.
"""

import argparse
import datetime
import json
import sys

from llm_planner import GroqMissionPlanner, LLMPlannerError
from validator import MissionValidationError, validate_mission


def main():
    parser = argparse.ArgumentParser(description="Prompt -> LLM -> validated drone mission JSON")
    parser.add_argument("prompt", type=str, help="Natural language operator command")
    parser.add_argument("--out", type=str, default="/tmp/mission_manifest.json")
    parser.add_argument("--model", type=str, default=None, help="Groq model override")
    args = parser.parse_args()

    planner = GroqMissionPlanner(model=args.model)

    print(f"[1/3] Prompt: {args.prompt}")
    try:
        raw_proposal = planner.plan(args.prompt)
    except LLMPlannerError as e:
        print(f"LLM planning failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[2/3] Raw LLM proposal (UNTRUSTED): {json.dumps(raw_proposal, indent=2)}")

    try:
        validated = validate_mission(raw_proposal)
    except MissionValidationError as e:
        print(f"REJECTED by validator: {e}", file=sys.stderr)
        print("No manifest written. The executor will never see this proposal.", file=sys.stderr)
        sys.exit(2)

    manifest = {
        "operator_prompt": args.prompt,
        "raw_llm_proposal": raw_proposal,
        "mission": validated,
        "generated_at_utc": datetime.datetime.utcnow().isoformat() + "Z",
    }
    with open(args.out, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"[3/3] VALIDATED mission written to {args.out}")
    print(json.dumps(validated, indent=2))
    print("\nNext, with PX4 SITL running:")
    print(f"  python3 executor.py {args.out}")


if __name__ == "__main__":
    main()
