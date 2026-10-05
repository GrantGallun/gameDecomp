"""One feedback-driven policy revision and a final separate development panel.

Replay the expanded calibration worlds against three explicit depth penalties,
save that decision, then verify the proposed recipe with fresh compiler runs.
No source repair generator or compiler changes; no held-out reference bodies.

python eval/results/dream-search-20260922/adapt.py --run-id adapted-v1 --budget 32
"""
from pathlib import Path
import pilot
from eval.search_replay import Policy, load_world, select_policy


if __name__ == "__main__":
    worlds = [load_world(pilot.OUT / "evolution-v1" / f"calibration--{name}.world.json")
              for name in ("Fdistort", "__MusIntProcessWobble")]
    incumbent = Policy("breadth", "breadth")
    policies = [Policy(f"depth-{q}", "depth", q) for q in (1, 4, 16)]
    selection = select_policy(worlds, policies, incumbent, budget=32)
    destination = pilot.OUT / "adaptation-selection.json"
    if destination.exists():
        raise SystemExit("adaptation selection already frozen; retain the original record")
    pilot.write(destination, selection)
    pilot.POLICIES = [incumbent, Policy(**selection["policy"])]
    pilot.CALIBRATION = ["Fdistort", "__MusIntProcessWobble"]
    pilot.FRESH = ["updateRacePlayerLeanAngle", "__ll_rem"]
    pilot.main()
