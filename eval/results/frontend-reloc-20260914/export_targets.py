"""Freeze the frontend-rejected and relocation-name target sets from the live checkpoint (read-only, via WSL)."""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code")
from eval import campaign_state  # noqa: E402

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
OUT = Path("/mnt/c/Code/gameDecomp/eval/results/frontend-reloc-20260914")
pointer = json.loads((RUN / "campaign.json").read_text())
nodes = campaign_state.read(RUN / "campaign.json")["nodes"]
RELOC = re.compile(r"%(?:hi|lo)\(([^)]+)\)")


def row(name, node, **extra):
    source = Path(str(node["source"]).replace("C:\\", "/mnt/c/").replace("\\", "/"))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != node["source_sha256"]:
        return None
    residual = node.get("residual") or {}
    return {"name": name, "source": str(source), "source_sha256": node["source_sha256"], "attempt_id": node["attempt_id"],
            "address": node["address"], "size": node["size"], "score": node.get("score"),
            "instruction_count": node.get("instruction_count"), "faults": residual.get("faults"),
            "first_difference": residual.get("first_difference"), **extra}


frontend, relocation = [], []
for name, node in sorted(nodes.items()):
    if node["status"] != "pending":
        continue
    residual = node.get("residual") or {}
    fe = residual.get("frontend") or {}
    verification = node.get("verification") or {}
    if residual.get("compiled") and fe.get("passed") is False:
        r = row(name, node, diagnostics=str(fe.get("diagnostics", ""))[:4000],
                object_exact=bool(verification.get("exact")), frontend_status=fe.get("status"))
        if r:
            frontend.append(r)
    diff = residual.get("first_difference") or []
    removed = {m for line in diff if line.startswith("-") for m in RELOC.findall(line)}
    added = {m for line in diff if line.startswith("+") for m in RELOC.findall(line)}
    faults = residual.get("faults") or {}
    if faults.get("relocation") and removed != added and (removed or added):
        r = row(name, node, target_relocations=sorted(removed), candidate_relocations=sorted(added))
        if r:
            relocation.append(r)

(OUT / "frontend-targets.json").write_text(json.dumps({"checkpoint": pointer["commit"], "functions": frontend}, indent=1))
(OUT / "relocation-targets.json").write_text(json.dumps({"checkpoint": pointer["commit"], "functions": relocation}, indent=1))
print(json.dumps({"checkpoint": pointer["commit"], "frontend_rejected": len(frontend),
                  "frontend_object_exact": sum(r["object_exact"] for r in frontend),
                  "frontend_status": dict(Counter(r["frontend_status"] for r in frontend)),
                  "relocation_name_mismatch": len(relocation),
                  "relocation_only": sum(1 for r in relocation if not any(v for k, v in (r["faults"] or {}).items() if k != "relocation")),
                  "rodata_literal": sum(1 for r in relocation if any(".rodata" in c for c in r["candidate_relocations"]))}, indent=1))
