"""Fresh assembly/header-only drafts, with no solver outcome used for selection."""
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
manifest = json.loads((OUT / "freeze-v2.json").read_text())
CODE = Path(manifest["code_root"])
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate
from solver import m2c_input, project_headers

REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/register-storage-20260922/drafts-v2"


def main():
    assert not (OUT / "panel-v2.json").exists()
    (OUT / "inputs-v2").mkdir(exist_ok=True)
    rows = []
    for name in manifest["followup"]:
        ws = REPO / "nonmatchings" / name
        if not (ws / "target.s").exists():
            rows.append({"function": name, "status": "no-existing-target-workspace"})
            continue
        private = isolate(REPO, NATIVE / name, name)
        target = private / "nonmatchings" / name / "target.s"
        assembly = target.read_text()
        headers = tuple(dict.fromkeys(["common.h", *project_headers.context_headers(private, name, assembly)]))
        result, metadata = m2c_input.draft(private, target, context_headers=headers)
        row = {"function": name, "status": "generated" if result.returncode == 0 else "draft-failed",
            "assembly_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "metadata": metadata,
            "returncode": result.returncode, "stderr": result.stderr,
            "assistance_tier": "header-assisted", "training_eligible": False}
        if result.returncode == 0:
            source = ''.join(f'#include "{h}"\n' for h in headers) + '\n' + result.stdout
            path = OUT / "inputs-v2" / f"{name}.c"
            path.write_text(source)
            row.update(source=str(path.relative_to(OUT)), source_sha256=hashlib.sha256(source.encode()).hexdigest())
        rows.append(row)
        print(json.dumps({"function": name, "status": row["status"]}), flush=True)
    assert all(hashlib.sha256((CODE / p).read_bytes()).hexdigest() == sha for p, sha in manifest["files"].items())
    (OUT / "panel-v2.json").write_text(json.dumps({"selection": "eight names frozen before generation; retain all outcomes",
        "reference_body_supplied": False, "training_eligible": False, "rows": rows}, indent=2))


if __name__ == "__main__":
    main()
