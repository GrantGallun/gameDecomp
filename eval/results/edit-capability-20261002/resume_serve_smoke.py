"""Finish serving smoke using the already published two-step adapter; no retraining."""
from pathlib import Path
from logic_pilot import HERE, Pilot

out = Path.home() / "decomp/experiments/edit-capability-20261002/logic-pilot-v3-smoke-completion-logits"
original = out.with_name("logic-pilot-v3-smoke")
# Three aliases of one smoke adapter test the same server capacity as the full pilot.
# They are NOT three trained arms, and this directory never feeds the held-out comparison.
for name in ("repair", "logic"):
    alias = out / f"adapter_{name}"
    if not alias.exists():
        alias.symlink_to(out / "adapter_mixed", target_is_directory=True)
pilot = Pilot(out)
with pilot.server(["repair", "logic", "mixed"]):
    for arm in ("base", "mixed"):
        pilot.run(f"exam_{arm}", ["-m", "eval.logic_exam", "run", out / "exam.json", "--arm", arm,
                                  "--out", out / f"answers_{arm}.jsonl", "--jobs", "4"])
for arm in ("base", "mixed"):
    pilot.run(f"grade_{arm}", [HERE / "logic_grade.py", original / "arms/mixed.jsonl",
              "--answers", out / f"answers_{arm}.jsonl", "--exam", out / "exam.json",
              "--context", out.parent / "public/ctx_smoke.jsonl", "--out", out / f"grades_{arm}.jsonl", "--jobs", "4"])
pilot.status("complete", note="8 training-overlap smoke items; one adapter with three serving aliases; no capability claim")
