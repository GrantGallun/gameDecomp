"""A revalidate job (zero-model agentrepair + accept) replaces a stale semantic failure with the current panel verdict.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/pointer-contracts-20260914/validate_revalidate.py
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / "staged-revalidate2"))
from eval import agentrepair, campaign_state, campaign_workers, completion_campaign as campaign  # noqa: E402
from solver import repair_queue  # noqa: E402

RUN = OUT.parents[0] / "resume-pipeline-20260908"
state = campaign_state.read(RUN / "campaign.json")
rows = []
for function in ("func_80064C68", "func_800647E0"):
    node = copy.deepcopy(state["nodes"][function])
    before = (node.get("semantic_validation") or {}).get("status")
    profile = repair_queue.next_profile(node, 3, campaign.PROFILES)
    native = Path(tempfile.mkdtemp(prefix="revalidate-"))
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        source = Path(node["source"]).read_text()
        receipt = agentrepair.run(repo=repo, db=db, function=function, source=source, source_parent_attempt_id=None,
                                  out=native / "receipt.json", best_source_out=native / "best.c", model="none",
                                  endpoint="http://127.0.0.1:9", draws=1, depth=4, beam=3, max_calls=0, timeout=10,
                                  think="low", num_thread=4, temperature=0.35, num_predict=100, seed=1, cache_dir=None,
                                  verbose=False, deterministic_budget=0, structured_output=True, retry_invalid=True,
                                  include_header_context=True, resilient=True)["result"]
        result = {**receipt, "attempt_id": receipt["best_attempt_id"], "source": receipt["best_source_path"],
                  "source_sha256": receipt["best_source_sha256"], "residual": receipt["best_residual"],
                  "score": receipt["best_residual"]["weighted_progress_score"]}
        campaign.accept(node, profile, result, native / "receipt.json")
        after = (node.get("semantic_validation") or {}).get("status")
        row = {"function": function, "profile": profile["name"], "before": before, "after": after,
               "lane_after": repair_queue.lane(node).value,
               "ok": profile["name"].startswith("revalidate@") and before == "observed_failure"
                     and after in ("observed_pass", "observed_pass_with_execution_debt")}
        rows.append(row)
    finally:
        shutil.rmtree(native, ignore_errors=True)
    print(json.dumps(rows[-1]), flush=True)
ok = all(r["ok"] for r in rows)
(OUT / "validate-revalidate.json").write_text(json.dumps({"passed": ok, "rows": rows}, indent=1))
print("PASSED" if ok else "FAILED")
