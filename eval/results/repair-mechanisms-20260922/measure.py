"""Receipt-preserving helper for bounded follow-up development experiments."""
import json
import sqlite3
from probe import OUT, NATIVE, REPO, isolate, score_logged, _attempt_to_verdict


def run(phase, candidates):
    if (OUT / f"{phase}.json").exists():
        raise SystemExit("preserve prior experiment " + phase)
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    rows = []
    for name, label, source, parent in candidates:
        repo = isolate(REPO, NATIVE / phase / name, name)
        attempt = score_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy=phase + ":" + label, run_id=phase + ":" + name,
            parent_attempt_id=parent, action=label, model="deterministic-development-probe",
            prompt="Target residual-guided development candidate; no reference body.",
            extra={"training_eligible": False, "assistance_tier": "project-header-assisted"})
        stem = OUT / f"{name}--{attempt.receipt_id}"
        stem.with_suffix(".c").write_text(source)
        verdict = _attempt_to_verdict(attempt)
        stem.with_suffix(".json").write_text(json.dumps(verdict, indent=2))
        row = {"function": name, "label": label, "receipt_id": attempt.receipt_id,
               "parent_receipt_id": parent, "score": attempt.score, "exact": attempt.exact,
               "compiled": attempt.compiled, "frontend": (attempt.frontend or {}).get("passed")}
        rows.append(row)
        (OUT / f"{phase}.json").write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)
    conn.close()
    return rows
