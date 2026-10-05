"""Restart (32 + 32 from best + 32 from best) vs one search of 96, same 224 functions, same code (PROTOCOL.md)."""
import json
from pathlib import Path

H = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments"


def rows(d):
    return {json.loads(p.read_text())["function"]: json.loads(p.read_text()) for p in (E / d / "rows").glob("*.json")}


long_ = rows("restart-long-20260923")
r1, r2, r3 = rows("locality-population-20260923"), rows("restart-round2-20260923"), rows("restart-round3-20260923")
solved = lambda r: bool(r and r.get("exact")) and not r.get("baseline_exact")
funcs = sorted(set(long_) | set(r1))
restart_exact = {f for f in funcs if solved(r1.get(f)) or r2.get(f, {}).get("exact") or r3.get(f, {}).get("exact")}
long_exact = {f for f in funcs if solved(long_.get(f))}
best = lambda f: max([x.get(f, {}).get("best_score", 0) or 0 for x in (r1, r2, r3)])
open_ = [f for f in funcs if f not in restart_exact and f not in long_exact]
out = {"functions": len(funcs), "long_exact": len(long_exact), "restart_exact": len(restart_exact),
       "restart_only": sorted(restart_exact - long_exact), "long_only": sorted(long_exact - restart_exact),
       "restart_round_gains": {"round2": sorted(f for f in r2 if r2[f].get("exact")),
                               "round3": sorted(f for f in r3 if r3[f].get("exact"))},
       "open_restart_better": sum(best(f) > (long_.get(f, {}).get("best_score") or 0) for f in open_),
       "open_long_better": sum(best(f) < (long_.get(f, {}).get("best_score") or 0) for f in open_),
       "compiles": {"long": sum(r.get("compiles", 0) for r in long_.values()),
                    "restart": sum(x.get(f, {}).get("compiles", 0) for x in (r1, r2, r3) for f in funcs)}}
out["verdict"] = ("restart adopted" if out["restart_exact"] > out["long_exact"] and not out["long_only"]
                  else "no better than budget")
(H / "compare.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
