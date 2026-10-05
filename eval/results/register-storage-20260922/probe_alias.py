"""Second causal probe: compose the storage fix with an existing address alias."""
import json
import sqlite3
from probe import CODE, SHARED, NATIVE, REPO, NAME, isolate, compile_logged


def main():
    assert not (SHARED / "probe-alias.json").exists()
    db = sqlite3.connect(NATIVE / "attempts.sqlite")
    prior = json.loads((SHARED / "probe.json").read_text())
    trials = []
    for label, parent in [("baseline", 0), ("register-both", 4)]:
        source = (SHARED / f"probe-{label}.c").read_text()
        source = source.replace("        var_a3 = var_a3->next;", "        var_a3 = *var_a2;")
        trials.append((f"{label}-alias", source, prior["runs"][parent]["receipt_id"]))
    cleaned = (SHARED / "probe-natural-loop.c").read_text()
    cleaned = cleaned.replace("    OSThread **var_a2;", "    register OSThread **var_a2;")
    cleaned = cleaned.replace("    OSThread *var_a3;", "    register OSThread *var_a3;")
    trials.append(("natural-loop-register", cleaned, prior["runs"][1]["receipt_id"]))
    rows = []
    for label, source, parent in trials:
        repo = isolate(REPO, NATIVE / label, NAME)
        verdict = compile_logged(repo / "nonmatchings" / NAME, repo, NAME, source, conn=db,
            strategy=f"register-storage-probe:{label}", run_id="register-storage-probe-alias",
            parent_attempt_id=parent, action=label, model="deterministic-probe",
            extra={"training_eligible": False, "assistance_tier": "header-assisted"})
        assert not verdict.get("error")
        rows.append({"label": label, **verdict})
        (SHARED / f"probe-{label}.c").write_text(source)
        print(json.dumps({"label": label, "score": verdict["score"], "exact": verdict["exact"]}), flush=True)
        if verdict["exact"]:
            confirm = isolate(REPO, NATIVE / f"confirm-{label}", NAME)
            result = compile_logged(confirm / "nonmatchings" / NAME, confirm, NAME, source, conn=db,
                strategy="register-storage-probe:confirmation", run_id="register-storage-probe-alias-confirm",
                parent_attempt_id=verdict["receipt_id"], action="independent-confirmation", model="deterministic-probe",
                extra={"training_eligible": False, "assistance_tier": "header-assisted"})
            assert result["exact"] and not result.get("error")
            rows.append({"label": f"confirm-{label}", **result})
    (SHARED / "probe-alias.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
