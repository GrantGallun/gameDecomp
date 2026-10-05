"""Check whether retained filenames still refer to the historical sampled attempt."""
import hashlib
import importlib
import json
import sqlite3
from pathlib import Path

audit = importlib.import_module("eval.results.claude-review-20260928.diff_reconstruction_audit")
root = Path("eval/results/claude-review-20260928")
data = json.loads((root / "diff-reconstruction-audit.json").read_text())
db = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
function_of = {aid: row["function"] for row in data["edges"] for aid in (row["parent"], row["child"])}
checks = []
for row in data["attempts"]:
    if "retained_dump_equal" not in row:
        continue
    aid = row["attempt"]
    diff, = db.execute("select diff_summary from attempts where id=?", (aid,)).fetchone()
    name = function_of[aid]
    ws = Path("/home/grant/decomp/sbk1/nonmatchings") / name
    header = next(line[4:].split("\t")[0] for line in diff.splitlines() if line.startswith("+++ "))
    tag = Path(header).name.removesuffix("_object_dump_normalized.s")
    check = {"attempt": aid, "function": name, "tag": tag,
             "retained_dump_equal": row["retained_dump_equal"]}
    current_diff = ws / (tag + "_diff")
    if current_diff.is_file():
        body = lambda value: "\n".join(line for line in value.splitlines() if not line.startswith(("--- ", "+++ ")))
        check["retained_diff_matches"] = body(current_diff.read_text()) == body(diff)
    # GNU patch is an independent implementation of the reconstruction, using only
    # the stored target and diff. It never compiles or reads any C source.
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="reconstruct-check-") as temp:
        target = Path(temp) / "listing.s"
        target.write_bytes((ws / "target_object_dump_normalized.s").read_bytes())
        patched = subprocess.run(["patch", "--batch", "--fuzz=0", str(target)], input=diff,
                                 text=True, capture_output=True, timeout=10)
        check["gnu_patch_succeeded"] = patched.returncode == 0
        if patched.returncode == 0:
            check["gnu_patch_matches"] = hashlib.sha256("\n".join(target.read_text().splitlines()).encode()).hexdigest() == row["candidate_sha256"]
    checks.append(check)
db.close()
summary = {
    "retained_files_found": len(checks),
    "same_retained_listing": sum(c["retained_dump_equal"] for c in checks),
    "different_retained_listing": sum(not c["retained_dump_equal"] for c in checks),
    "different_listing_with_different_diff": sum(not c["retained_dump_equal"] and c.get("retained_diff_matches") is False for c in checks),
    "different_listing_despite_same_diff": sum(not c["retained_dump_equal"] and c.get("retained_diff_matches") is True for c in checks),
    "gnu_patch_succeeded": sum(c["gnu_patch_succeeded"] for c in checks),
    "gnu_patch_matches": sum(c.get("gnu_patch_matches", False) for c in checks),
}
(root / "reconstructed-artifact-validation.json").write_text(json.dumps({"summary": summary, "checks": checks}, indent=2))
print(json.dumps(summary, indent=2))
