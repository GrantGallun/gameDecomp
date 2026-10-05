"""Save real target/candidate objects for certificate v3 development (no campaign writes).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/v3_fixtures.py

Writes ~/decomp/v3-fixtures/<function>/{target.o,candidate.o,target.s,meta.json}.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402
from solver import workspace  # noqa: E402

PENDING = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}
RELOC = {r["name"]: r for r in json.loads((ROOT / "eval/results/frontend-reloc-20260914/relocation-targets.json").read_text())["functions"]}
CASES = {
    "drawRaceSplitscreenSelectEntryFee": Path(RELOC["drawRaceSplitscreenSelectEntryFee"]["source"]),   # rodata string, literal
    "waitForTitleDemoRaceIntroStart": Path(RELOC["waitForTitleDemoRaceIntroStart"]["source"]),         # .late_rodata float
    "initMainMenuSettings": HERE / "address-probe/initMainMenuSettings.c",                             # rodata float after address fix
    "__osSiRawStartDma": Path(PENDING["__osSiRawStartDma"]["source"]),                                 # hardware register literals
    "drawTitleScreenStartPrompt": Path(PENDING["drawTitleScreenStartPrompt"]["source"]),               # extern rodata symbol
    "waitEndingJamPhase17": Path(PENDING["waitEndingJamPhase17"]["source"]),                           # v2 passes (replay control)
}
base = Path.home() / "decomp/v3-fixtures"
for function, source_path in CASES.items():
    row = {"name": function, "source": str(source_path)}
    bench = regalloc_probe.Bench(row)
    try:
        attempt = workspace.score(bench.ws, bench.isolated, "cand", source_path.read_text(), conn=bench.conn, func=function)
        out = base / function
        out.mkdir(parents=True, exist_ok=True)
        for name, target in (("target.o", "target.o"), ("candidate.o", "cand.o"), ("target.s", "target.s")):
            shutil.copy2(bench.ws / target, out / name)
        meta = bench.conn.execute("SELECT addr,size FROM functions WHERE name=?", (function,)).fetchone()
        (out / "meta.json").write_text(json.dumps({"function": function, "address": meta[0], "size": meta[1],
                                                   "score": attempt.score, "exact": attempt.exact,
                                                   "diff": attempt.diff}, indent=1))
        print(function, attempt.score, attempt.exact, flush=True)
    finally:
        bench.close()
