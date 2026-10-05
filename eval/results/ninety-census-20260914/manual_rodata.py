"""Manual check: does referencing the target's named rodata symbol make these functions exact? (bench only)

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/manual_rodata.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402

CASES = {
    "drawShopMenuMoneyPanel": [('"%6dG"', "gShopMenuMoneyFormat")],
    "drawTrickAttackChallengeLabels": [('"Point"', "D_800E1730"), ('"Time Limit"', "D_800E1738")],
}
rows = {r["function"]: r for r in json.loads((HERE / "ninety.json").read_text())}
for function, edits in CASES.items():
    source = json.loads((HERE / "diffs" / f"{function}.json").read_text())["source_text"]
    definition = source.index(f"void {function}(")
    declarations = "".join(f"extern char {name}[];\n" for _literal, name in edits)
    body = source[definition:]
    for literal, name in edits:
        assert body.count(literal) == 1, (function, literal)
        body = body.replace(literal, name)
    candidate = source[:definition] + declarations + body
    bench = regalloc_probe.Bench({"name": function, "source": rows[function]["source"]})
    try:
        from solver import workspace
        attempt = workspace.score(bench.ws, bench.isolated, f"{function}_manual_rodata", candidate, conn=bench.conn,
                                  func=function, strategy="manual-rodata", model="zero-model")
        print(function, {"compiled": attempt.compiled, "exact": attempt.exact, "score": attempt.score})
        verification = attempt.verification or {}
        boundary = verification.get("function_boundary") or {}
        print(json.dumps({k: v for k, v in boundary.items() if k not in ("inputs",)}, default=str)[:1500])
    finally:
        bench.close()
