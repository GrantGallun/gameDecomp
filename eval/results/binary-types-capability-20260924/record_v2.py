"""Confirm and record the capability run's harness-exact sources (PROTOCOL.md "Confirmation and recording").

Frozen code root and isolated repo copies, as register-o2-20260924/record.py. Contamination screen before any write:
no `#include "game/`, no type name defined only in the reference's src/ (eval.status.reference_only_types) or only in
include/game/** (type-flywheel-20260924/game_type_leak.game_only). Ratchet-checked.

    python3 record.py [--dry-run]   -> inventory-receipts.json
"""
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402

_spec = importlib.util.spec_from_file_location("frontend_type_repair",
                                               "/mnt/c/Code/gameDecomp/solver/frontend_type_repair.py")
frontend_type_repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frontend_type_repair)

E = Path.home() / "decomp/experiments/binary-types-capability-20260924/v2"
REPO = Path.home() / "decomp/sbk1"
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
PROMPT = ("Fresh recompile of an m2c draft produced with a BINARY-DERIVED type context "
          "(eval/results/binary-types-capability-20260924; struct identity from binary dataflow, prototypes from "
          "binary arity, globals from accesses in ELF symbol extents; placeholder names; public libultra/SDK prelude "
          "only), then, if the frontend gate refused it, the existing frontend_type_repair. No reference source, no "
          "game headers, no model.")


def screens():
    sys.path.insert(0, "/mnt/c/Code/gameDecomp")
    from eval import status as st
    ref_only = st.reference_only_types(REPO)
    spec = importlib.util.spec_from_file_location(
        "glk", "/mnt/c/Code/gameDecomp/eval/results/type-flywheel-20260924/game_type_leak.py")
    # game_type_leak computes game_only at import; it needs the KB read-only only
    glk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(glk)
    return ref_only, glk.game_only


def last_frontend(conn, name):
    row = conn.execute("SELECT a.sampling FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? "
                       "ORDER BY a.id DESC LIMIT 1", (name,)).fetchone()
    try:
        return json.loads(row[0]).get("frontend") if row and row[0] else None
    except ValueError:
        return None


def confirm(conn, repo, name, source, rounds=2):
    frontier = [(source, [])]
    for depth in range(rounds + 1):
        nxt = []
        for src, labs in frontier:
            verdict = compile_logged(repo / "nonmatchings" / name, repo, name, src, conn=conn,
                strategy="binary-types-v2-20260924:source-independent", run_id="binary-types-capability-v2-20260924",
                action="independent-confirmation", model="deterministic-binary-types", run_kind="compiler-model-steering",
                prompt=PROMPT, extra={"training_eligible": False, "assistance_tier": "source-independent",
                                      "repairs": labs, "source_sha256": hashlib.sha256(src.encode()).hexdigest()})
            if verdict["exact"]:
                return verdict, src, labs
            fe = last_frontend(conn, name)
            if depth < rounds and verdict.get("score") == 100.0 and fe and fe.get("passed") is False:
                nxt += [(cand, labs + [lab]) for lab, cand in frontend_type_repair.variants(src, name, fe)]
        frontier = nxt
        if not frontier:
            break
    return None, None, []


def main():
    dry = "--dry-run" in sys.argv
    summary = json.loads((HERE / "summary-v2.json").read_text())
    ref_only, game_only = screens()
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for name in summary["exact_functions"]:
        row = json.loads((E / "rows" / f"{name}.json").read_text())
        source = row["source"]
        text = re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.S)
        idents = set(IDENT.findall(text))
        bad = sorted((idents & ref_only) | (idents & game_only))
        if '#include "game/' in source or bad:
            results.append({"function": name, "status": "screened-out", "names": bad[:5]})
            continue
        if dry:
            results.append({"function": name, "status": "would-confirm"})
            continue
        repo = isolate(REPO, E / "inventory" / name, name)
        verdict, final, labs = confirm(conn, repo, name, source)
        if verdict is None:
            results.append({"function": name, "status": "not-confirmed"})
            continue
        results.append({"function": name, "status": "new-object-exact", "receipt_id": verdict["receipt_id"],
                        "repairs": labs, "source_sha256": hashlib.sha256(final.encode()).hexdigest()})
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    import collections
    out = {"counts": dict(collections.Counter(r["status"] for r in results)), "before": len(before),
           "after": len(after), "results": results}
    (HERE / ("inventory-dry-run-v2.json" if dry else "inventory-receipts-v2.json")).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))


if __name__ == "__main__":
    main()
