"""Independently recompile, then record, the o1_register_saved exacts from fire.json. Ratchet-checked.

Same path as frame-size-20260923/record.py: frozen code root, isolated repo copy, compile_logged into the KB."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402
import importlib.util  # noqa: E402

# frontend_type_repair postdates the frozen code root; load the main tree's module by path (it needs only
# solver.repair_context, which the frozen root has).
_spec = importlib.util.spec_from_file_location(
    "frontend_type_repair", "/mnt/c/Code/gameDecomp/solver/frontend_type_repair.py")
frontend_type_repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frontend_type_repair)

NATIVE = Path.home() / "decomp/experiments/register-o2-20260924"
REPO = Path.home() / "decomp/sbk1"


def last_frontend(conn, name) -> dict | None:
    """The frontend gate verdict of the function's most recent attempt (stored in its sampling JSON)."""
    row = conn.execute("SELECT a.sampling FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? "
                       "ORDER BY a.id DESC LIMIT 1", (name,)).fetchone()
    try:
        return json.loads(row[0]).get("frontend") if row and row[0] else None
    except ValueError:
        return None


def confirm(conn, repo, name, source, tier, labels, rounds=2):
    """compile_logged; if the object is exact but the frontend gate refuses the C, apply the existing
    frontend_type_repair to the gate's own diagnostics (up to two rounds, as for finishCurrentRdpTask)."""
    frontier = [(source, labels)]
    for depth in range(rounds + 1):
        nxt = []
        for src, labs in frontier:
            verdict = compile_logged(repo / "nonmatchings" / name, repo, name, src, conn=conn,
                strategy=f"register-o2-20260924:{tier}", run_id="register-o1-inventory-20260924",
                action="independent-confirmation", model="deterministic-repair", run_kind="compiler-model-steering",
                prompt=PROMPT, extra={"training_eligible": False, "assistance_tier": tier, "mechanism": labs,
                                      "source_sha256": hashlib.sha256(src.encode()).hexdigest()})
            if verdict["exact"]:
                return verdict, src, labs
            fe = last_frontend(conn, name)
            if depth < rounds and verdict.get("score") == 100.0 and fe and fe.get("passed") is False:
                nxt += [(cand, labs + [lab]) for lab, cand in frontend_type_repair.variants(src, name, fe)]
        frontier = nxt
        if not frontier:
            break
    return None, None, labels


PROMPT = ("Fresh recompile of a restart-round-3 best node repaired by solver.branch_shape.o1_register_saved "
          "(IDO 5.3 -O1: a `register` local takes a callee-saved register; H6/H7, register-o2-20260924), then the "
          "existing frontend_type_repair on the gate's own diagnostics. The rule was suggested by draft-to-reference "
          "mining over NON-population functions and confirmed on synthetic compiles; no reference body of this "
          "function was read.")


def main():
    fired = [r for r in json.loads((HERE / "fire.json").read_text()) if r["end"] >= 100.0]
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for r in fired:
        name, source = r["function"], r["source"]
        # An object-exact attempt whose source the frontend gate refused (attempt 95895, the first run of this
        # script) is not a confirmation; skip only when a gate-passing exact exists.
        rows = conn.execute("SELECT a.sampling FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                            "WHERE f.name=? AND a.exact=1", (name,)).fetchall()
        if any('"passed": true' in (row[0] or "") for row in rows):
            results.append({"function": name, "status": "already-object-exact"})
            continue
        tier = "project-header-assisted" if '#include "game/' in source else "source-independent"
        repo = isolate(REPO, NATIVE / "inventory" / name, name)
        verdict, final, labels = confirm(conn, repo, name, source, tier, [lab for rnd in r["rounds"] for lab, _s in rnd])
        if verdict is None:
            results.append({"function": name, "status": "not-confirmed"})
            continue
        results.append({"function": name, "receipt_id": verdict["receipt_id"], "assistance_tier": tier,
                        "status": "new-object-exact",
                        "mechanism": labels, "source_sha256": hashlib.sha256(final.encode()).hexdigest()})
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"results": results, "before": len(before), "after": len(after)}
    (HERE / "inventory-receipts.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
