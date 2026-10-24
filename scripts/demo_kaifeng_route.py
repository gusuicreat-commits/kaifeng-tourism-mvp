"""Standalone synthetic demo for the Kaifeng route; no DB, credentials, HTTP or LLM.

Runs the same engine as demo_city_engine.py but with the field-reported Kaifeng
stops (开封府 -> 第一楼 -> 鼓楼夜市 -> 返程) and a fixed visiting order. Everything
is synthetic and clearly marked; it is never imported into the runtime database.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.city.facts import FactBundle  # noqa: E402
from app.city.engine.context import PlanningContext  # noqa: E402
from app.city.engine.models import Requirements, OperationalBundle  # noqa: E402
from app.city.engine.prepare import prepare  # noqa: E402
from app.city.engine.solver import solve  # noqa: E402

FIXTURE = ROOT / "data/route-acceptance/kaifeng-route-demo-20261025.json"
FIXED_ORDER = ("kaifengfu_visit", "firstfloor_dinner", "gulou_visit")


def run() -> dict:
    data = json.loads(FIXTURE.read_text())
    records = FactBundle.model_validate(data["facts"]).records
    catalog = {(r.kind, r.fact_id): r for r in records}
    names = {r.fact_id: getattr(r, "name", None) for r in records}
    ctx = PlanningContext(
        catalog,
        OperationalBundle.model_validate(data["operations"]),
        datetime.fromisoformat(data["assessed_at"]),
        synthetic=True,
    )
    req = Requirements.model_validate(data["requirements"])
    problem = prepare(req, ctx, fixed_order=FIXED_ORDER)
    result = solve(problem)
    out = {
        "route_id": "kaifeng-route-demo-20261025",
        "mode": "synthetic_demo",
        "status": result.status,
        "itineraries": [],
    }
    for it in result.itineraries:
        steps = []
        for a in it.activities:
            steps.append({
                "type": "activity",
                "place_name": names.get(a.place_id, a.place_id),
                "kind": a.kind,
                "starts_at": a.starts_at.isoformat(),
                "ends_at": a.departs_at.isoformat(),
                "duration_minutes": a.duration_seconds // 60,
            })
        for leg in it.legs:
            travel = leg.journey.travel
            steps.append({
                "type": "leg",
                "from": names.get(travel.origin_place_id, travel.origin_place_id),
                "to": names.get(travel.destination_place_id, travel.destination_place_id),
                "mode": travel.mode,
                "departs_at": leg.departs_at.isoformat(),
                "arrives_at": leg.arrives_at.isoformat(),
                "distance_m": leg.journey.distance_m,
                "walk_minutes": round((travel.upper_seconds or 0) / 60),
            })
        out["itineraries"].append({
            "returns_at": it.returns_at.isoformat(),
            "total_minutes": it.total_seconds // 60,
            "steps": steps,
        })
    return out


def main() -> int:
    result = run()
    out = ROOT / "data/route-acceptance/kaifeng-route-demo-20261025-result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "feasible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
