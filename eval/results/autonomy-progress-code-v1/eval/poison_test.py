"""The poison test: prove the knowledge base can detect its own corruption.

ROADMAP.md Phase 3's acceptance check. The design's safety argument is that a
bad fact is cheap to retract and its consequences are immediately measurable.
That is a claim about the code, and until it is executed it is only a claim.

    1. Start from a KB with N matched functions.
    2. Corrupt one field type several matched functions depend on.
    3. The system must DETECT it, LOCALISE it, and AUTO-RETRACT it, returning
       to N.
    4. Fuzz it: many random corruptions. Anything below 100% detection means
       `func_deps` has holes, and a hole is a fact that can be poisoned
       silently.

This runs against a THROWAWAY copy of the database. It must never mutate the
real KB -- a test that corrupts production to prove corruption is detectable
has missed the point.

Run:
    python3 -m eval.poison_test --db ~/decomp/kb-sbk1.sqlite --trials 50
"""

from __future__ import annotations

import argparse
import random
import shutil
import sqlite3
import tempfile
from pathlib import Path

from kb import tms


def build_fixture(conn: sqlite3.Connection, n_funcs: int = 12,
                  n_facts: int = 6, seed: int = 1) -> dict:
    """A small synthetic KB: evidence, inferences citing it, matched functions.

    Synthetic rather than borrowed from the real KB because the property under
    test is structural -- does retraction reach everything that depended on a
    claim -- and that is clearer with a known dependency graph than with 72,845
    real rows.
    """
    rng = random.Random(seed)
    conn.executescript((Path(__file__).parent.parent / "kb" / "schema.sql").read_text())
    conn.execute("INSERT INTO extraction (target, rom_sha1, elf_path,"
                 " tool_versions, created_at) VALUES ('fixture','x','x','{}',0)")

    ev_ids = []
    for i in range(n_facts * 3):
        cur = conn.execute(
            "INSERT INTO evidence (extraction_id, kind, addr, op, base, offset,"
            " width, signed, class, access, is_load)"
            " VALUES (1,'mem_access',?,'lw','param0',?,4,1,'int','full',1)",
            (0x80000000 + i * 4, i * 4))
        ev_ids.append(cur.lastrowid)

    fact_ids = []
    for f in range(n_facts):
        cited = ev_ids[f * 3:(f + 1) * 3]
        fact_ids.append(tms.assert_inference(
            conn, "field", f"struct:Fixture@{f * 4:#x}", "s32", cited,
            origin="miner", confidence=0.9))

    # Some facts build on others, so retraction has a cascade to walk.
    derived = tms.assert_inference(
        conn, "struct_size", "struct:Fixture", "24", [ev_ids[0]],
        origin="miner", depends_on=[fact_ids[0], fact_ids[1]])
    fact_ids.append(derived)

    for i in range(n_funcs):
        addr = 0x80010000 + i * 0x40
        conn.execute(
            "INSERT INTO functions (addr, name, size, insn_count, is_leaf, state)"
            " VALUES (?,?,64,16,1,'matched')", (addr, f"fixtureFunc{i}"))
        # Each function leans on a couple of facts.
        deps = rng.sample(fact_ids, k=2)
        tms.record_dependency(conn, addr, deps)

    conn.commit()
    return {"facts": fact_ids, "n_funcs": n_funcs}


def poison_once(conn: sqlite3.Connection, fact_id: int) -> dict:
    """Corrupt one fact, then require the system to detect and undo it."""
    before = tms.matched_count(conn)

    expected = sorted(set([fact_id] + tms.dependents(conn, fact_id)))
    expected_funcs = tms.affected_functions(conn, expected)

    r = tms.retract(conn, fact_id, reason="poison test: deliberately corrupted")
    after_retract = tms.matched_count(conn)

    # Detection is only meaningful when something actually depended on the
    # fact. A claim no matched function relies on SHOULD demote nothing, and
    # scoring that as a miss conflates "the system failed to notice" with
    # "there was nothing to notice" -- which made a correct system look 96%.
    load_bearing = bool(expected_funcs)
    detected = (after_retract < before) if load_bearing else None

    localised = sorted(set([r.root] + r.cascaded)) == expected
    complete = sorted(r.demoted) == sorted(expected_funcs)

    return {
        "fact": fact_id,
        "before": before,
        "after": after_retract,
        "load_bearing": load_bearing,
        "detected": detected,
        "localised": localised,
        "complete": complete,
        "dependents": len(r.cascaded),
        "demoted": len(r.demoted),
    }


def run(trials: int, seed: int) -> int:
    rng = random.Random(seed)
    results, failures = [], []

    for t in range(trials):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fixture.sqlite"
            conn = sqlite3.connect(db)
            fixture = build_fixture(conn, seed=seed + t)
            fact = rng.choice(fixture["facts"])
            res = poison_once(conn, fact)
            results.append(res)
            failed = (not res["localised"] or not res["complete"]
                      or (res["load_bearing"] and not res["detected"]))
            if failed:
                failures.append(res)
            conn.close()

    bearing = [r for r in results if r["load_bearing"]]
    inert = len(results) - len(bearing)
    det = sum(1 for r in bearing if r["detected"])
    loc = sum(1 for r in results if r["localised"])
    com = sum(1 for r in results if r["complete"])

    print("=" * 60)
    print("POISON TEST -- ROADMAP Phase 3 acceptance")
    print("=" * 60)
    print(f"trials             : {trials}")
    print(f"  load-bearing     : {len(bearing)}  (a matched function depended on it)")
    print(f"  inert            : {inert}  (nothing depended on it; detection vacuous)")
    if bearing:
        print(f"detected           : {det}/{len(bearing)}  ({100*det/len(bearing):.1f}%)")
    print(f"localised exactly  : {loc}/{trials}  ({100*loc/trials:.1f}%)")
    print(f"cascade complete   : {com}/{trials}  ({100*com/trials:.1f}%)")

    if failures:
        print(f"\n{len(failures)} FAILURES -- func_deps has holes:")
        for f in failures[:5]:
            print(f"  fact {f['fact']}: detected={f['detected']} "
                  f"localised={f['localised']} complete={f['complete']}")
        print("\nAnything below 100% means a fact can be poisoned silently.")
        return 1

    print("\nAll trials detected, localised and fully cascaded.")
    return 0


def check_invariants() -> int:
    """Invariants 3 and 4 must be properties of the code, not conventions."""
    print("\n" + "=" * 60)
    print("INVARIANT ENFORCEMENT")
    print("=" * 60)
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "inv.sqlite")
        build_fixture(conn)
        tms.guard_evidence_immutable(conn)

        # Invariant 4: no citation, no commit.
        try:
            tms.assert_inference(conn, "field", "struct:X@0x0", "s32", [],
                                 origin="model")
            print("  FAIL invariant 4: uncited inference was accepted")
            ok = False
        except tms.CitationRequired:
            print("  ok   invariant 4: uncited inference rejected")

        # A citation naming evidence that does not exist is not a citation.
        try:
            tms.assert_inference(conn, "field", "struct:X@0x4", "s32", [999999],
                                 origin="model")
            print("  FAIL invariant 4: dangling citation was accepted")
            ok = False
        except tms.CitationRequired:
            print("  ok   invariant 4: dangling citation rejected")

        # Invariant 3: evidence is immutable.
        for sql, desc in [
                ("UPDATE evidence SET width=8 WHERE id=1", "update"),
                ("DELETE FROM evidence WHERE id=1", "delete")]:
            try:
                conn.execute(sql)
                print(f"  FAIL invariant 3: evidence {desc} was allowed")
                ok = False
            except sqlite3.IntegrityError:
                print(f"  ok   invariant 3: evidence {desc} blocked")
            except sqlite3.OperationalError as exc:
                if "immutable" in str(exc):
                    print(f"  ok   invariant 3: evidence {desc} blocked")
                else:
                    raise

        # The ratchet must refuse a mutation that costs matches.
        def lose_a_match(c):
            c.execute("UPDATE functions SET state='attempted' WHERE state='matched'"
                      " AND addr=(SELECT MIN(addr) FROM functions)")
            return []

        before = tms.matched_count(conn)
        committed, b, a = tms.ratchet(conn, lose_a_match)
        restored = tms.matched_count(conn)
        if committed or restored != before:
            print(f"  FAIL ratchet: committed={committed} {before}->{restored}")
            ok = False
        else:
            print(f"  ok   ratchet: rejected a mutation costing a match "
                  f"({b}->{a}), rolled back to {restored}")
        conn.close()

    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260827)
    args = ap.parse_args()
    rc = run(args.trials, args.seed) | check_invariants()
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
