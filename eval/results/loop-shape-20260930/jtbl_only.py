"""Unsolved functions whose best candidate differs from the target ONLY in jump-table relocation spelling."""
import json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import draft_census
from solver import diffrepair

JT = re.compile(r"%(hi|lo)\((jtbl_[0-9A-Fa-f_]+|\.rodata|D_[0-9A-F]+)\)")
solved = draft_census.solved_by_pipeline()
hits = {}
for path in draft_census.LEDGERS:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    for name, aid, diff in db.execute("select f.name, a.id, a.diff_summary from attempts a join functions f on "
                                      "f.addr=a.func_addr where a.compiled=1 and coalesce(a.exact,0)=0 and a.diff_summary is not null"):
        if name in solved or name in hits:
            continue
        t, c = diffrepair._streams(diff)
        if len(t) != len(c) or t == c:
            continue
        diffs = [(a, b) for a, b in zip(t, c) if a != b]
        if diffs and all(JT.sub(r"%\1(X)", a) == JT.sub(r"%\1(X)", b) and ("jtbl" in a or "jtbl" in b) for a, b in diffs):
            hits[name] = {"ledger": str(path), "attempt_id": aid, "lines": len(diffs)}
sealed = {x["name"] for x in json.load(open(HERE.parent / "heldout50-20260929/frame.json"))}
print(len(hits), "functions; sealed among them:", sorted(set(hits) & sealed))
(HERE / "jtbl_only.json").write_text(json.dumps(hits, indent=1))
