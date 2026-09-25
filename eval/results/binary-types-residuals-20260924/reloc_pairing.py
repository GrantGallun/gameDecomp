"""Normalized-assembly-exact binary-types sources whose object certificate fails only on HI16/LO16 relocation
pairing order (bytes identical). The pairing follows the order IDO emits the address expressions in, so the existing
order-changing families of solver.regalloc_mutations.variants are tried, judged by the OFFICIAL certificate
(compile_logged: exact = certificate + frontend gate). Up to 12 variants per function; every compile is logged.

    python3 reloc_pairing.py   -> reloc-pairing-receipts.json
"""
import collections
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

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
spec = importlib.util.spec_from_file_location("rm_main", "/mnt/c/Code/gameDecomp/solver/regalloc_mutations.py")
E = Path.home() / "decomp/experiments/binary-types-residuals-20260924"
REPO = Path.home() / "decomp/sbk1"
ORDER_FAMILIES = {"stmt_order", "stmt_move", "commutative", "decl_order", "field_local", "truth_test"}


def main():
    import subprocess
    # the main-tree variants stream, run in a subprocess with the main tree first on sys.path (the frozen code root
    # predates several families)
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    exact = {n for (n,) in conn.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                        "where a.exact=1")}
    todo = []
    for name, sampling, source in conn.execute(
            "select f.name, a.sampling, a.source_code from attempts a join functions f on f.addr=a.func_addr "
            "where a.run_id like 'binary-types%' and a.exact=0 and a.compiled=1 and a.score=100 order by a.id"):
        v = (json.loads(sampling) or {}).get("verification") or {}
        if name not in exact and v.get("normalized_assembly_exact") and v.get("status") == "object_sections_differ":
            todo.append((name, source))
    todo = list({n: (n, s) for n, s in todo}.values())
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for name, source in todo:
        proc = subprocess.run(
            [sys.executable, "-c",
             "import json,sys; sys.path.insert(0,'/mnt/c/Code/gameDecomp');"
             "from solver import regalloc_mutations as rm;"
             "src=sys.stdin.read(); name=sys.argv[1]; fam=set(sys.argv[2].split(','));"
             "out=[[l,k,c] for l,k,c in rm.variants(src,name,'') if k in fam][:12];"
             "print(json.dumps(out))", name, ",".join(sorted(ORDER_FAMILIES))],
            input=source, capture_output=True, text=True, timeout=300)
        try:
            cands = json.loads(proc.stdout or "[]")
        except ValueError:
            cands = []
        repo = isolate(REPO, E / "inventory" / name, name)
        status, receipt, tried = "not-found", None, 0
        for label, kind, cand in cands:
            tried += 1
            verdict = compile_logged(repo / "nonmatchings" / name, repo, name, cand, conn=conn,
                strategy="binary-types-20260924:source-independent", run_id="binary-types-reloc-20260924",
                action="independent-confirmation", model="deterministic-binary-types", run_kind="compiler-model-steering",
                prompt="Binary-types source that was normalized-assembly exact but failed the object certificate on "
                       "HI16/LO16 pairing order only; an order-changing family of regalloc_mutations.variants. "
                       "No reference source.",
                extra={"training_eligible": False, "assistance_tier": "source-independent", "repairs": [label],
                       "source_sha256": hashlib.sha256(cand.encode()).hexdigest()})
            if verdict["exact"]:
                status, receipt = "new-object-exact", verdict.get("receipt_id")
                break
        results.append({"function": name, "status": status, "tried": tried, "receipt_id": receipt,
                        "candidates": len(cands)})
        print(name, status, tried, flush=True)
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"counts": dict(collections.Counter(r["status"] for r in results)), "results": results}
    (HERE / "reloc-pairing-receipts.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["counts"]))


if __name__ == "__main__":
    main()
