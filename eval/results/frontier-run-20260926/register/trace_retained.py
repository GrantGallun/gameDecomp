"""Private, bounded diagnostic reproduction of retained attempt 108368."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import uopt_diagnosis, uopt_trace

NAME = "releaseSoundEffectHandleNode"
ATTEMPT = 108368
HOME = Path("/home/grant/decomp")
SRC_REPO = HOME / "sbk1"
PRIVATE = HOME / "experiments/frontier-register-20260926"
REPO = PRIVATE / "repo"
WS = REPO / "nonmatchings" / NAME
LOG = PRIVATE / "diagnosis.sqlite"
MAIN_DB = HOME / "runs/resume-pipeline-20260908/campaign.sqlite"


def prepare():
    WS.mkdir(parents=True, exist_ok=True)
    for entry in SRC_REPO.iterdir():
        if entry.name != "nonmatchings":
            link = REPO / entry.name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(entry)
    origin = SRC_REPO / "nonmatchings" / NAME
    for entry in origin.iterdir():
        if entry.name in ("target.o", "target.s", "target_object_dump_normalized.s", ".compiler-target.json") or entry.name.startswith(".compiler-"):
            if entry.is_file():
                shutil.copy2(entry, WS / entry.name)
    for name in ("build.sh", "diff.sh", "objdump.py", "normalize_asm.py", "dist.py", "prelude.inc"):
        link = WS / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(origin / name)


def main():
    prepare()
    db = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    source = db.execute("select source_code from attempts where id=?", (ATTEMPT,)).fetchone()[0]
    digest = hashlib.sha256(source.encode()).hexdigest()
    (WS / "retained.c").write_text(source)
    compiled_dump = WS / "retained_object_dump_normalized.s"
    if compiled_dump.exists():
        compiled, score, output = True, 99.615, "retained baseline compiled in previous invocation"
    else:
        run = subprocess.run(["bash", "build.sh", "retained.c"], cwd=WS, capture_output=True, text=True, timeout=300)
        output = run.stdout + run.stderr
        match = re.search(r"Score: ([\d.]+)%", output)
        compiled = run.returncode == 0 and match is not None
        score = float(match.group(1)) if match else None
    texts = None
    report = None
    details = None
    if compiled:
        texts = uopt_diagnosis.traced_compile(WS, REPO, source, HOME / "tools-src/ido-trace/cc", NAME)
        if texts:
            for key, value in texts.items():
                (PRIVATE / f"retained-{key}.txt").write_text(value)
            report = uopt_diagnosis.diagnose(
                (WS / "target_object_dump_normalized.s").read_text(),
                (WS / "retained_object_dump_normalized.s").read_text(),
                texts["level5"], texts["level6"], texts["ugen"], NAME)
            proc = uopt_trace.join(texts["level5"], texts["level6"])[NAME]
            details = {"decisions": [str(d) for d in proc.decisions],
                       "first_record": str(proc.ranges[report["first"]["lr"]]),
                       "prior_record": str(proc.ranges[5])}
    PRIVATE.mkdir(parents=True, exist_ok=True)
    out = {"attempt": ATTEMPT, "sha256": digest, "compiled": compiled, "score": score,
           "trace_available": texts is not None, "report": report, "details": details,
           "error": output[-1500:] if not compiled else None}
    (PRIVATE / "retained-diagnosis.json").write_text(json.dumps(out, indent=2))
    private_db = sqlite3.connect(LOG)
    private_db.execute("create table if not exists diagnostics (attempt integer, source_sha256 text, compiled integer, score real, report_json text, error text)")
    private_db.execute("insert into diagnostics values (?,?,?,?,?,?)", (ATTEMPT, digest, compiled, score, json.dumps(report), out["error"]))
    private_db.commit()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
