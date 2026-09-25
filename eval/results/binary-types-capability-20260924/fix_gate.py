"""Object-exact binary-types sources that the frontend gate refused: apply the existing solver.frontend_fixits
(casts at the diagnosed expression ranges, with the gate's own clang command), then recompile through the official
logged path. Recorded only if the recompiled object is still exact AND the gate passes. Ratchet-checked.

    python3 fix_gate.py   -> fix-gate-receipts.json
"""
import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, f"/mnt/c/Code/gameDecomp/solver/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    return mod


frontend_fixits = _load("frontend_fixits")
E = Path.home() / "decomp/experiments/binary-types-capability-20260924"
REPO = Path.home() / "decomp/sbk1"


def main():
    failed = json.loads((HERE / "gate_failures.json").read_text())["failed"]
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for name in failed:
        row = conn.execute("SELECT a.source_code, a.sampling FROM attempts a JOIN functions f ON f.addr=a.func_addr "
                           "WHERE f.name=? AND a.run_id LIKE 'binary-types%' AND a.run_id != 'binary-types-gatefix-20260924' AND a.exact=1 "
                           "ORDER BY a.id DESC LIMIT 1", (name,)).fetchone()
        if not row:
            results.append({"function": name, "status": "no-object-exact-source"})
            continue
        source, sampling = row
        fe = (json.loads(sampling) or {}).get("frontend") or {}
        command = (fe.get("recipe") or {}).get("command")
        if not command:
            results.append({"function": name, "status": "no-frontend-command"})
            continue
        repo = isolate(REPO, E / "gatefix" / name, name)
        ws = repo / "nonmatchings" / name
        fixed, trace = frontend_fixits.propose(repo, source, command, ws)
        if fixed is None:
            results.append({"function": name, "status": "fixits-declined", "steps": len(trace)})
            continue
        verdict = compile_logged(ws, repo, name, fixed, conn=conn,
            strategy="binary-types-20260924:source-independent", run_id="binary-types-gatefix-20260924",
            action="independent-confirmation", model="deterministic-binary-types", run_kind="compiler-model-steering",
            prompt="Object-exact binary-types source; the frontend gate refused argument pointer types; the existing "
                   "solver.frontend_fixits added casts at the gate's own diagnosed ranges. No reference source.",
            extra={"training_eligible": False, "assistance_tier": "source-independent", "repairs": ["frontend_fixits"],
                   "source_sha256": hashlib.sha256(fixed.encode()).hexdigest()})
        results.append({"function": name, "status": "new-object-exact" if verdict["exact"] else "not-confirmed",
                        "receipt_id": verdict.get("receipt_id")})
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    import collections
    out = {"counts": dict(collections.Counter(r["status"] for r in results)), "results": results}
    (HERE / "fix-gate-receipts.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["counts"], indent=1))


if __name__ == "__main__":
    main()
