"""finishCurrentRdpTask: frame 0x28 vs target 0x20 although only sp1C (-4) is in memory. Does the register local
temp_v0 own the extra slot? Inline it (and, as a control, declare it before sp1C) and compile through the population
probe driver."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import alloc_inverter  # noqa: E402

HERE = Path(__file__).resolve().parent
PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")
NAME = "finishCurrentRdpTask"


def main():
    row = next(r for p in (Path.home() / "decomp/experiments/restart-round3-20260923/rows").glob("*.json")
               if (r := json.loads(p.read_text())).get("function") == NAME)
    world = json.loads(Path(row["world"]).read_text())["world"]
    node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
    src = node["source"]
    swapped = src.replace("    s32 sp1C;\n    SchedulerTask *temp_v0;\n", "    SchedulerTask *temp_v0;\n    s32 sp1C;\n", 1)
    variants = {"inline_temp_v0": alloc_inverter._inline(src, NAME, "temp_v0"), "decl_swap": swapped}
    probes = [{"function": NAME, "label": f"frame:{k}", "source": v, "parent_score": node["verdict"]["score"]}
              for k, v in variants.items() if v and v != src]
    path = HERE / "rdp_probes.json"
    path.write_text(json.dumps(probes))
    subprocess.run(["python3", "probe.py", str(path)], cwd=PT, check=True, capture_output=True, text=True)
    want = {hashlib.sha256(p["source"].encode()).hexdigest(): p["label"] for p in probes}
    for line in (PT / "probes.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["source_sha256"] in want:
            frame = [l for l in (r.get("diff") or "").splitlines() if "sp,sp" in l]
            print(want[r["source_sha256"]], r["compiled"], r["exact"], r["score"], frame[:4])


if __name__ == "__main__":
    main()
