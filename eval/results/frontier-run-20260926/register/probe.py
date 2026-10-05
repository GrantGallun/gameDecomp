"""Run the single predeclared private lifetime probe in PROBE.md."""
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

HOME = Path("/home/grant/decomp")
PRIVATE = HOME / "experiments/frontier-register-20260926"
WS = PRIVATE / "repo/nonmatchings/releaseSoundEffectHandleNode"
SOURCE_DB = HOME / "runs/resume-pipeline-20260908/campaign.sqlite"
PARENT_SHA = "a393535d050115637b0582fceb7ff52c8c1efa34c3850dcafce3bec6d257f43f"
OLD = "            gActiveSoundHandleListTail = NULL;"
NEW = "            gActiveSoundHandleListTail = temp_v0;"


def main():
    db = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    parent = db.execute("select source_code from attempts where id=108368").fetchone()[0]
    assert hashlib.sha256(parent.encode()).hexdigest() == PARENT_SHA
    assert parent.count(OLD) == 1
    source = parent.replace(OLD, NEW)
    candidate_sha = hashlib.sha256(source.encode()).hexdigest()
    (PRIVATE / "probe.c").write_text(source)
    (WS / "probe.c").write_text(source)
    run = subprocess.run(["bash", "build.sh", "probe.c"], cwd=WS, capture_output=True, text=True, timeout=300)
    output = run.stdout + run.stderr
    (PRIVATE / "probe-build.txt").write_text(output)
    match = re.search(r"Score: ([\d.]+)%", output)
    compiled = run.returncode == 0 and match is not None
    score = float(match.group(1)) if match else None
    dump_path = WS / "probe_object_dump_normalized.s"
    diff_path = WS / "probe_diff"
    dump = dump_path.read_text() if compiled and dump_path.exists() else None
    diff = diff_path.read_text() if compiled and diff_path.exists() else None
    if dump is not None:
        (PRIVATE / "probe-dump.s").write_text(dump)
    if diff is not None:
        (PRIVATE / "probe-diff.txt").write_text(diff)
    result = {"parent_attempt": 108368, "parent_sha256": PARENT_SHA, "source_sha256": candidate_sha,
              "compiled": compiled, "score": score, "exact": "Verified exact match: yes" in output,
              "returncode": run.returncode, "diff": diff, "error": output[-1500:] if not compiled else None}
    (PRIVATE / "probe-result.json").write_text(json.dumps(result, indent=2))
    log = sqlite3.connect(PRIVATE / "diagnosis.sqlite")
    log.execute("create table if not exists probe_attempts (parent_attempt integer, parent_sha256 text, source_sha256 text, source_code text, compiled integer, score real, exact integer, returncode integer, compiler_output text, diff text)")
    log.execute("insert into probe_attempts values (?,?,?,?,?,?,?,?,?,?)", (108368, PARENT_SHA, candidate_sha, source, compiled, score, result["exact"], run.returncode, output, diff))
    log.commit()
    print(json.dumps({key: value for key, value in result.items() if key != "diff"}, indent=2))
    print("DIFF:\n", diff)


if __name__ == "__main__":
    main()
