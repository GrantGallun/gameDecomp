"""Is `cc -S` then assembling the .s byte-identical to the normal compile? If so, as1 can be probed with edited .s."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import byte_certificate, uopt_diagnosis  # noqa: E402

RUN = Path.home() / "decomp/experiments/gated-population-20260923"
NAME = "waitCourseSelectRecordsClose"
repo = RUN / "ws" / NAME / "gated"
ws = repo / "nonmatchings" / NAME
src = next(p["source"] for p in json.loads((HERE / "probes-callback.json").read_text())
           if p["label"] == "callback:via_camera_global")


def cc_args():
    command = uopt_diagnosis._recipe_command(ws, repo)
    args, skip = [], False
    for part in command:
        if skip:
            skip = False
        elif part == "-o":
            skip = True
        elif part != "-c" and not part.endswith(".c"):
            args.append(part)
    if "--" in args:
        cc = next(a for a in args if a.endswith("ido-recomp/linux/cc"))
        args = [cc] + args[[i for i, a in enumerate(args) if a == "--"][1] + 1:]
    return args


def text_image(obj: Path):
    return byte_certificate.object_image(obj.read_bytes())[".text"] if False else \
        {k: v for k, v in byte_certificate.object_image(obj.read_bytes()).items()}


def main():
    work = repo / "build" / "as1_probe"
    work.mkdir(parents=True, exist_ok=True)
    (work / "c.c").write_text(src)
    base = cc_args()
    subprocess.run(base + ["-c", "-o", str(work / "direct.o"), str(work / "c.c")], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(base + ["-S", str(work / "c.c")], cwd=repo, check=True, capture_output=True)
    (repo / "c.s").replace(work / "c.s")
    r = subprocess.run(base + ["-c", "-o", str(work / "viaS.o"), str(work / "c.s")], cwd=repo, capture_output=True,
                       text=True)
    print("assemble rc", r.returncode, r.stderr[-300:])
    a = byte_certificate.object_image((work / "direct.o").read_bytes())
    b = byte_certificate.object_image((work / "viaS.o").read_bytes()) if r.returncode == 0 else None
    print("identical sections+relocations:", a == b)
    if b and a != b:
        for k in a:
            if a.get(k) != (b or {}).get(k):
                print("  differs:", k)


if __name__ == "__main__":
    main()
