"""finishCurrentRdpTask: the inlined candidate's object is exact but the frontend gate rejects inherited implicit
declarations. Apply solver.frontend_type_repair (the existing mechanism) and compile its candidates."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import frontend_type_repair  # noqa: E402

HERE = Path(__file__).resolve().parent
PT = Path("/mnt/c/Code/gameDecomp/eval/results/population-transfer-20260922")
NAME = "finishCurrentRdpTask"
SHA = sys.argv[1] if len(sys.argv) > 1 else "d3439f80ddb6"


def main():
    ws = Path.home() / "decomp/experiments/population-transfer-20260922/probe-ws" / NAME / SHA / "nonmatchings" / NAME
    probes = [p for f in ("rdp_probes.json", "rdp_frontend_probes.json") if (HERE / f).exists()
              for p in json.loads((HERE / f).read_text())
              if hashlib.sha256(p["source"].encode()).hexdigest().startswith(SHA)]
    source = probes[0]["source"]
    frontend = json.loads((ws / f"{NAME}.frontend.json").read_text())
    out = [{"function": NAME, "label": f"frame:inline_temp_v0+{label}", "source": cand, "parent_score": 100.0}
           for label, cand in frontend_type_repair.variants(source, NAME, frontend)]
    print(len(out), "candidates")
    for p in out:
        head = p["source"][p["source"].find("void finishCurrentRdpTask") - 200:p["source"].find("void finishCurrentRdpTask")]
        print(p["label"], "|", head.strip().replace("\n", " | ")[-200:])
    path = HERE / "rdp_frontend_probes.json"
    path.write_text(json.dumps(out))
    subprocess.run(["python3", "probe.py", str(path)], cwd=PT, check=True, capture_output=True, text=True)
    want = {hashlib.sha256(p["source"].encode()).hexdigest(): p["label"] for p in out}
    for line in (PT / "probes.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["source_sha256"] in want:
            print(want[r["source_sha256"]], r["compiled"], r["exact"], r["score"], r["source_sha256"][:12])


if __name__ == "__main__":
    main()
