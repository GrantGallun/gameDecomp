"""For the `member-on-typed-pointer` states: is the access even expressible by the deterministic tooling?

`opaque_variant` builds a padded struct from PARAMETER accesses at NON-NEGATIVE offsets, and
`structgen.field_type` names fields `unk<hex>`. So it can reach `arg0->unk10`. It cannot reach
`arg0->matrixDirty`, because nothing in the assembly says the field is called that.

This asks the question that decides whether item 2 is a tooling job at all, per state:

  1. what does the draft actually say, and where does clang's `member reference base type` land
  2. does the parameter appear as an address base in the assembly at that offset, so `opaque_variant`'s
     first condition can see it
  3. does `opaque_variant` fire, and if not, which condition declined
  4. is the offending member ONE m2c named (`unk10`, `unk0`) or a real field name (`matrixDirty`) --
     because only the first is reachable without inventing semantics

Read-only.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import compile_obligations, dataflow, workspace                # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

residual = json.loads((BASE / "post-sequence-residual.json").read_text(encoding="utf-8"))
targets = [row["function"] for row in residual["single_class"]
           if row["class"] == "member-on-typed-pointer"]
print(f"states where member-on-typed-pointer is the WHOLE distance: {len(targets)}\n")

MEMBER = re.compile(r"([A-Za-z_]\w*)\s*->\s*(?P<member>[A-Za-z_]\w*)|"
                    r"([A-Za-z_]\w*)\s*->\s*(?P<member2>unk-\w+)")
m2c_named = Counter()
real_named = Counter()

conn = sqlite3.connect(str(KB))
try:
    for name in targets:
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            print(f"{name}: {why}")
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""

        # The members clang complained about, read from the candidate.
        members = []
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*|unk-\w+)", current):
            base, member = match.group(1), match.group(2)
            if member not in ("unk0", "unk4", "unk8") and not re.fullmatch(r"unk[0-9A-Fa-f]+", member):
                members.append((base, member))
        for _base, member in members:
            if re.fullmatch(r"unk[0-9A-Fa-f]+", member):
                m2c_named[member] += 1
            else:
                real_named[member] += 1

        asm = workspace.target_asm(workspace.bootstrap(REPO, name), name)
        analysis = dataflow.analyse(asm)
        param_slots = [a for a in analysis.accesses.values()
                       if a.address and a.address.kind == "address"
                       and a.address.name.startswith("param") and a.address.offset >= 0]
        report = compile_obligations.opaque_variant(REPO, name, current, asm)
        fired = report[0] != current
        changed, info = report
        print(f"{name}")
        print(f"   offending members : {sorted(set(m for _, m in members))[:5]}")
        print(f"   param accesses    : {len(param_slots)} at non-negative offsets "
              f"(opaque_variant's first condition)")
        print(f"   opaque_variant    : {'FIRED' if fired else 'declined'}")
        print(f"   final             : compiled={bool(best.get('compiled'))} score={best.get('score')}")
        print()
finally:
    conn.close()

print(f"members clang complains about, across these states:")
print(f"   m2c-named (`unkNN`, mechanically typeable) : {sum(m2c_named.values())} "
      f"{dict(m2c_named.most_common(8))}")
print(f"   real field names (need semantics)          : {sum(real_named.values())} "
      f"{dict(real_named.most_common(12))}")
