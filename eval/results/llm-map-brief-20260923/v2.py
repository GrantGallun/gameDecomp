"""v2 A/B (PROTOCOL-v2.md): short prompt, stated-fault cohort, 6 calls per arm. Writes probes for compilation."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from brief import build, hint, per_line  # noqa: E402
from eval import agentrepair  # noqa: E402
from solver import llm  # noqa: E402

RUN = Path.home() / "decomp/experiments/locality-population-20260923"
N, CALLS = 16, 6
RULES = ("Rules: keep every declaration and #include the file needs; C89 only (no // comments after code, no "
         "declarations after statements, no inline); do not rename the function or change its signature unless the "
         "evidence says so; change as little as possible. Return the COMPLETE corrected source file in one ```c block.")


def cohort():
    index = json.loads((HERE / "example_index.json").read_text())
    rows = []
    for path in sorted((RUN / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact") or not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        best = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        lines = per_line(best["verdict"], best["source"])
        stated = sum(1 for rows_ in lines.values() if any(hint(c, t, x) for c, t, x in rows_))
        if not stated:
            continue
        try:
            agentrepair._refuse_frozen_heldout(Path("/mnt/c/Code/gameDecomp/eval/sets"), row["function"])
        except Exception:                                   # noqa: BLE001
            continue
        rows.append((stated, best["verdict"]["score"], row["function"], best,
                     build(row["function"], best["verdict"], best["source"], index)))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    return rows[:N]


def prompt(source, evidence_title, evidence):
    return ("You are fixing a C function so that IDO 5.3 (-O2, MIPS) compiles it to exactly the target object code.\n\n"
            f"CURRENT SOURCE FILE:\n```c\n{source}\n```\n\n{evidence_title}\n{evidence}\n\n{RULES}\n")


def extract(text):
    blocks = re.findall(r"```(?:c|C)?\n(.*?)```", text, re.S)
    return max(blocks, key=len) if blocks else None


def main():
    picked = cohort()
    endpoint = llm.host()
    probes, log = [], []
    for i, (stated, start, name, best, brief) in enumerate(picked):
        raw = best["verdict"].get("diff") or ""
        arms = {"A": prompt(best["source"], "INSTRUCTION DIFF (- target, + current):", raw[:6000]),
                "B": prompt(best["source"], "VERIFIED COMPILER EVIDENCE (the compiler's own line records):", brief)}
        for k in range(CALLS):
            for arm in (("A", "B") if k % 2 == 0 else ("B", "A")):
                text, meta = llm.generate(endpoint, "gpt-oss:20b", arms[arm], timeout=600, think="low",
                                          temperature=0.35, num_predict=8000, seed=20260923 + 100 * i + k)
                src = extract(text)
                log.append({"function": name, "arm": arm, "call": k, "parsed": src is not None,
                            "stated_lines": stated, "start": start})
                if src and src.strip() != best["source"].strip():
                    probes.append({"function": name, "label": f"v2:{arm}:{k}", "source": src, "parent_score": start})
                print(json.dumps(log[-1]), flush=True)
    (HERE / "probes-v2.json").write_text(json.dumps(probes, indent=1))
    (HERE / "v2-log.json").write_text(json.dumps(log, indent=1))
    (HERE / "v2-cohort.json").write_text(json.dumps([{"function": f, "stated_lines": s, "start": sc, "brief": b}
                                                     for s, sc, f, _best, b in picked], indent=1))
    print("candidates:", len(probes))


if __name__ == "__main__":
    main()
