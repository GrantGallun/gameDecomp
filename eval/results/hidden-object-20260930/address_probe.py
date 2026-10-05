"""Fire test on real objects: rodata_symbol.address_variants on the .text-identical functions blocked by rodata.

Scores the census candidate, runs the generator on its fresh objects, scores the rewrite, and reports object and
function-boundary verdicts plus the generator receipt. Writes the facts and sources to address_cases.json so the
unit test replays the motivating residual without a toolchain. Trial DB only.

    python3 eval/results/hidden-object-20260930/address_probe.py [function ...]
"""
import json, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from solver import rodata_symbol, workspace                                   # noqa: E402

REPO = Path("/home/grant/decomp/sbk1")
TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"
DEFAULT = ["func_8005A884", "func_8005CF60", "updateEndingObjectSpriteDebugViewer", "func_8005C14C",
           "func_8005D558", "func_8005AC44", "func_8005905C"]


def verdict(a):
    v = a.verification or {}
    fb = v.get("function_boundary") or {}
    return {"compiled": a.compiled, "score": a.score, "object_exact": bool(a.exact),
            "complete": workspace.repair_complete(a), "status": v.get("status"),
            "function_exact": fb.get("function_exact"), "fb_schema": fb.get("schema_version"),
            "fb_error": fb.get("schema_3_error") or fb.get("error")}


def main(names):
    rows = {json.loads(l)["name"]: json.loads(l) for l in (HERE / "census.jsonl").read_text().splitlines()}
    cases = []
    for name in names:
        r = rows[name]
        src = sqlite3.connect(f"file:{r['ledger']}?mode=ro", uri=True).execute(
            "select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
        ws = workspace.bootstrap(REPO, name)
        conn = sqlite3.connect(TRIAL, timeout=600)
        tag = f"{name}_addr_{time.time_ns()}"
        before = workspace.score(ws, REPO, tag, src, conn=conn, func=name, strategy="lead3:address-baseline",
                                 run_kind="lead3")
        conn.commit()
        facts = rodata_symbol.address_facts((ws / "target.o").read_bytes(), (ws / f"{tag}.o").read_bytes())
        new, receipt = rodata_symbol.address_rewrite(src, name, facts)
        after = None
        if new:
            a = workspace.score(ws, REPO, f"{tag}_rw", new, conn=conn, func=name,
                                strategy="lead3:rodata_address", run_kind="lead3")
            conn.commit()
            after = verdict(a)
        row = {"function": name, "attempt_id": r["attempt_id"], "ledger": r["ledger"], "before": verdict(before),
               "after": after, "receipt": receipt}
        print(json.dumps(row), flush=True)
        cases.append({"function": name, "source": src, "facts": facts, "rewritten": new, "receipt": receipt,
                      "before": row["before"], "after": after})
        for p in ws.glob(f"{tag}*"):
            p.unlink()
    (HERE / "address_cases.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT)
