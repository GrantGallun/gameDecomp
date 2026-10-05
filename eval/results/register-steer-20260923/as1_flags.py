"""Which flags make assembling ugen's .s reproduce the direct compile?"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from as1_roundtrip import RUN, NAME, cc_args, repo  # noqa: E402
from solver import byte_certificate  # noqa: E402

work = repo / "build" / "as1_probe"
direct = byte_certificate.object_image((work / "direct.o").read_bytes())
for extra in ([], ["-Wb,-O2"], ["-Wb,-O3"], ["-O3"], ["-Wab,-O2"], ["-Wa,-O2"]):
    out = work / "try.o"
    out.unlink(missing_ok=True)
    r = subprocess.run(cc_args() + extra + ["-c", "-o", str(out), str(work / "c.s")], cwd=repo, capture_output=True,
                       text=True)
    same = r.returncode == 0 and byte_certificate.object_image(out.read_bytes()) == direct
    size = out.stat().st_size if out.exists() else None
    print(f"{str(extra):14} rc={r.returncode} identical={same} size={size} {r.stderr.strip()[-120:]}")
