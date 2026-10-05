"""Why pending functions fail integration: per-function outcome from today's sweep receipts and build logs."""
import glob, json, os, re, collections
from pathlib import Path
ART = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign-artifacts")
cut = 1790736000  # 2026-09-30, after the amendment
rows = {}
for d in sorted(glob.glob(str(ART / "*-integration-sweep"))):
    if int(Path(d).name.split("-")[0]) // 10**9 < cut:
        continue
    if not (Path(d) / "sweep.json").exists():
        continue                                       # a sweep still running has no receipt yet
    sweep = json.load(open(Path(d) / "sweep.json"))
    for rec in sweep.get("records", []):
        fs = rec.get("functions") or []
        if len(fs) != 1:
            continue                                   # bisection steps; single-function records are the verdicts
        f = fs[0]
        reason = rec.get("error") or rec.get("status")
        if rec.get("status") == "build_failed":
            log = rec.get("build_log") or next((str(p) for p in Path(d).glob("*.build.log")), None)
            try:
                receipt = json.load(open(rec["receipt"])) if rec.get("receipt") else {}
                log = receipt.get("build_log") or log
            except Exception:
                pass
            if log and os.path.exists(log):
                errs = [re.sub(r"\x1b\[[0-9;]*m", "", l).strip() for l in open(log, errors="replace") if "error:" in l]
                reason = errs[0][:220] if errs else "build failed, no error line"
        rows[f] = {"status": rec.get("status"), "reason": reason}
kinds = collections.Counter()
for f, r in sorted(rows.items()):
    k = ("redeclaration/type conflict" if "redeclaration" in r["reason"] or "conflicting types" in r["reason"]
         else "shared declaration integration" if "shared declaration" in r["reason"]
         else r["status"])
    kinds[k] += 1
    print(f"{f:44s} {r['status']:20s} {r['reason'][:150]}")
print(dict(kinds))
json.dump(rows, open("/mnt/c/Code/gameDecomp/eval/results/perfect-score-20260929/integration_failures.json", "w"), indent=1)
