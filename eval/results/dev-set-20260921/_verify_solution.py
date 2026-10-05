"""Verify the one exact result independently, from the bytes in the receipt.

The registry search reported `__MusIntProcessWobble` exact at 100.0 by applying `regalloc-search` to the
fixed policy's 97.045 candidate. This re-derives that verdict from the recorded source -- not from the
search's own claim -- and prints what the certificate says, plus whether the function was already solved
before the search ran.

Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.dev-set-20260921._verify_solution
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval.tool_agent_run import build_context                              # noqa: E402

SEARCH = ROOT / "eval/results/dev-set-20260921/search-registry-policy2.json"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"

payload = json.loads(SEARCH.read_text(encoding="utf-8"))
entry = next(r for r in payload["results"] if r["search"].get("exact"))
name = entry["function"]
source = entry["solution_source"]
print("=" * 78)
print(f"{name}: the search's claim is exact={entry['search']['exact']} "
      f"score={entry['search']['final_score']}")
print(f"  solution sha256 (recorded)  {entry['solution_sha256']}")
print(f"  solution sha256 (recomputed) {hashlib.sha256(source.encode()).hexdigest()}")

problems = []
if hashlib.sha256(source.encode()).hexdigest() != entry["solution_sha256"]:
    problems.append("the recorded source does not hash to the recorded digest")

context, why = build_context(REPO, name)
if context is None:
    problems.append(f"no context: {why}")
    verdict = {}
else:
    verdict = context.compile_fn(source)
    print(f"  fresh verdict               compiled={verdict.get('compiled')} "
          f"exact={verdict.get('exact')} score={verdict.get('score')}")
    certificate = verdict.get("verification") or {}
    if isinstance(certificate, dict):
        interesting = {k: certificate.get(k) for k in
                       ("exact", "candidate_source_sha256", "requires_isolated_integration",
                        "kind", "schema_version")
                       if k in certificate}
        print(f"  certificate                 {json.dumps(interesting)}")
    if not verdict.get("exact"):
        problems.append("the recompiled source is NOT exact")

# WAS IT ALREADY SOLVED? A "new" match that was already in the KB would be a counting error, not a result.
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    before = conn.execute("select count(*) from attempts where func_addr = (select func_addr from attempts "
                          "where source_sha256 = ? limit 1) and coalesce(exact,0)=1",
                          (entry["sha256"],)).fetchone()[0]
    any_exact = conn.execute("select count(*) from attempts where coalesce(exact,0)=1 and source_sha256=?",
                             (entry["solution_sha256"],)).fetchone()[0]
finally:
    conn.close()
print(f"  exact attempts for this function existing BEFORE this candidate: {before}")
print(f"  attempt rows already carrying THIS solution's hash: {any_exact}")

# THE ASSISTANCE TIER, which decides what the result may be called.
print(f"  assistance tier             {entry['assistance']['tier']}")
print(f"  steps that produced it      {entry['assistance'].get('reference_source_steps')} "
      f"{entry['assistance'].get('header_steps')}")
print()
if problems:
    print("VERIFICATION FAILED:")
    for problem in problems:
        print(f"  - {problem}")
    sys.exit(1)
print("VERIFIED: the recorded bytes recompile to an object-exact match under the project's own oracle.")
print("NOT VERIFIED BY THIS SCRIPT: promotion into the real build. That is the ratchet's business, needs")
print("`requires_isolated_integration`, and is a separate step with its own acceptance test.")
