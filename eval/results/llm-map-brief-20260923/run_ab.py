"""Paired A/B (PROTOCOL.md): agentrepair with no brief (A) vs the map brief (B), 16 functions, 2 calls per arm."""
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from brief import build  # noqa: E402
from eval import agentrepair  # noqa: E402
from solver import llm  # noqa: E402

RUN = Path.home() / "decomp/experiments/locality-population-20260923"
OUT = HERE / "arms"
N, CALLS = 16, 2


def candidates():
    index = json.loads((HERE / "example_index.json").read_text())
    rows = []
    for path in sorted((RUN / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        text = build(row["function"], best["verdict"], best["source"], index)
        if not text or best["verdict"].get("receipt_id") is None:
            continue
        try:
            agentrepair._refuse_frozen_heldout(Path("/mnt/c/Code/gameDecomp/eval/sets"), row["function"])
        except Exception:                                   # noqa: BLE001  frozen held-out: excluded
            continue
        rows.append((best["verdict"]["score"], row["function"], best, text))
    rows.sort(key=lambda r: -r[0])
    return rows[:N]


def score_of(db: Path, attempt_id):
    with sqlite3.connect(db) as conn:
        r = conn.execute("SELECT score, exact FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return (r[0], bool(r[1])) if r else (None, False)


def main():
    OUT.mkdir(exist_ok=True)
    db = RUN / "attempts.sqlite"
    picked = candidates()
    (HERE / "selection.json").write_text(json.dumps([{"function": f, "start": s, "brief": t} for s, f, _b, t in picked], indent=1))
    endpoint = llm.host()
    for i, (start, name, best, text) in enumerate(picked):
        arms = [("A", ""), ("B", text)]
        if i % 2:
            arms.reverse()                                  # alternate order: no warm-cache advantage for one arm
        for arm, brief in arms:
            out = OUT / f"{name}--{arm}.json"
            if out.exists():
                continue
            started = time.time()
            receipt = agentrepair.run(
                repo=RUN / "ws" / name / "locality", db=db, function=name, source=best["source"],
                source_parent_attempt_id=best["verdict"]["receipt_id"], out=out,
                best_source_out=out.with_suffix(".best.c"), model="gpt-oss:20b", endpoint=endpoint,
                draws=1, depth=CALLS, beam=1, max_calls=CALLS, timeout=420, think="low", num_thread=12,
                temperature=0.35, num_predict=10000, seed=20260923 + i, cache_dir=None, verbose=False,
                strategy_brief=brief, structured_output=True, retry_invalid=True, include_header_context=True,
                compile_only=False, type_transaction=True)
            res = receipt["result"]
            best_score, exact = score_of(db, res["best_attempt_id"])
            line = {"function": name, "arm": arm, "start": start, "best": best_score, "exact": exact or res["exact"],
                    "calls": res["calls_attempted"], "invalid": res["invalid_proposals"],
                    "compiling_children": res["compiling_children"], "seconds": round(time.time() - started)}
            (OUT / f"{name}--{arm}.summary.json").write_text(json.dumps(line))
            print(json.dumps(line), flush=True)


if __name__ == "__main__":
    main()
