"""
llm_planner.py

Stage 2 of the pipeline: Prompt -> LLM -> (untrusted) drone mission JSON proposal.

Only job of this module: translate operator intent into a JSON object that
*claims* to match config/mission_schema.json. Its output is NEVER trusted
directly - it always passes through validator.py before anything reaches
MAVSDK / the drone. The LLM proposes; it never controls the vehicle.
"""

import json
import os

from groq import Groq

SCHEMA_SUMMARY = """
You convert a drone operator's natural language command into ONE JSON object.
Output ONLY the JSON object. No markdown fences, no prose, no explanation.

Schema (all required fields must be present):
{
  "mission_type": "patrol" | "route" | "go_to_point",
  "pattern": "square" | "circle" | "perimeter" | "waypoints",
  "loops": integer 1-10,
  "speed_mps": number 0.5-5.0,
  "altitude_m": number 2.0-20.0,
  "size_m": number 2.0-20.0        (only for pattern square/perimeter),
  "radius_m": number 2.0-20.0      (only for pattern circle),
  "waypoints": [[north_m, east_m], ...] up to 20 points, relative to takeoff
               point (only for pattern waypoints),
  "return_to_start": true|false,
  "notes": "short rationale, not executed"
}

Rules:
- Never invent fields outside this schema.
- If the operator requests an unsafe or out-of-range value (e.g. "fly at 50 m/s"
  or "climb to 200 metres"), clamp your proposal to the nearest allowed value
  and explain why in "notes".
- Default altitude_m to 10 if the operator doesn't specify one.
- "Patrol the perimeter loop twice at 15 metres [altitude]" -> mission_type:
  patrol, pattern: perimeter, loops: 2, altitude_m: 15.
- "Fly a circle of radius 8 metres once and return" -> mission_type: patrol,
  pattern: circle, radius_m: 8, loops: 1, return_to_start: true.
- "Go to north 5, east 3 and hover" -> mission_type: go_to_point, pattern:
  waypoints, waypoints: [[5, 3]], loops: 1.
"""


class LLMPlannerError(RuntimeError):
    pass


class GroqMissionPlanner:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise LLMPlannerError(
                "GROQ_API_KEY not set. export GROQ_API_KEY=... before running."
            )
        self.model = model or os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
        self.client = Groq(api_key=api_key)

    def plan(self, operator_prompt: str) -> dict:
        """Return a raw (UNVALIDATED) dict proposal from the LLM."""
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": SCHEMA_SUMMARY},
                {"role": "user", "content": operator_prompt},
            ],
        )
        raw_text = response.choices[0].message.content.strip()
        raw_text = _strip_code_fences(raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise LLMPlannerError(
                f"LLM did not return valid JSON: {e}\nRaw output:\n{raw_text}"
            ) from e

        if not isinstance(data, dict):
            raise LLMPlannerError("LLM output was valid JSON but not an object.")

        return data


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
