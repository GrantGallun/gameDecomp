"""ugen's own assembly (cc -S, before as1) for a workspace candidate, with temporaries named."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import uopt_diagnosis  # noqa: E402

NAMES = {2: "v0", 3: "v1", 4: "a0", 5: "a1", 6: "a2", 7: "a3", **{8 + i: f"t{i}" for i in range(8)},
         **{16 + i: f"s{i}" for i in range(8)}, 24: "t8", 25: "t9", 1: "at", 29: "sp", 31: "ra", 0: "zero"}


def ugen_assembly(name: str, source: str, run: Path) -> str:
    repo = run / "ws" / name / "gated"
    ws = repo / "nonmatchings" / name
    command = uopt_diagnosis._recipe_command(ws, repo)
    scratch = repo / "build" / "ugen_view"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "c.c").write_text(source)
    args = []
    skip = False
    for part in command:
        if skip:
            skip = False
            continue
        if part == "-o":
            skip = True
            continue
        if part in ("-c",) or part.endswith(".c"):
            continue
        args.append(part)
    # keep only the cc invocation and its flags (drop an asm-processor wrapper up to its second `--`)
    if "--" in args:
        cc = next(a for a in args if a.endswith("ido-recomp/linux/cc"))
        args = [cc] + args[[i for i, a in enumerate(args) if a == "--"][1] + 1:]
    # relative compiler and include paths resolve from the repo; -S writes <stem>.s to the working directory
    out = repo / "c.s"
    out.unlink(missing_ok=True)
    subprocess.run(args + ["-S", str(scratch / "c.c")], cwd=repo, capture_output=True, text=True, check=True)
    text = out.read_text()
    out.unlink(missing_ok=True)
    return re.sub(r"\$(\d+)\b", lambda m: NAMES.get(int(m.group(1)), m.group(0)), text)


if __name__ == "__main__":
    import json
    here = Path(__file__).resolve().parent
    name, label = sys.argv[1], sys.argv[2]
    run = Path.home() / "decomp/experiments/gated-population-20260923"
    probes = json.loads((here / "probes-steer2.json").read_text()) + [
        {"function": c["function"], "label": "best", "source": c["best_source"]}
        for c in json.loads((here / "census.json").read_text()) if c.get("best_source")]
    src = next(p["source"] for p in probes if p["function"] == name and p["label"] == label)
    body = ugen_assembly(name, src, run)
    start = body.index(f"{name}:")
    print("\n".join(l for l in body[start:].splitlines() if l.strip() and not l.strip().startswith((".loc", ".mask",
                                                                                                    ".frame", "#"))))
