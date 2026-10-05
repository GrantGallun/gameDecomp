"""Follow-up hypotheses selected from the first probe's compiler residuals."""
import json
import re
import sqlite3
from probe import OUT, NATIVE, REPO, ROOT, isolate, score_logged, _attempt_to_verdict


def variants(name):
    if name in ("Fvibup", "Fvibdown"):
        number = 3 if name == "Fvibup" else 8
        code = (OUT / f"{name}--{number}.c").read_text()
        collapsed = re.sub(r"    if \(\(s32\) temp_t8 < 0\) \{\s*var_f6 \+= 4294967296\.0f;\s*\}\n", "", code)
        yield "native-unsigned-conversion+cursor", collapsed
        yield "inline-unsigned-conversion+cursor", collapsed.replace("    temp_t8 = ", "    var_f6 = (u32) ").replace("    var_f6 = (f32) temp_t8;\n", "")
        unstepped = (OUT / f"inputs/{name}/initial.c").read_text()
        collapsed = re.sub(r"    if \(\(s32\) temp_t8 < 0\) \{\s*var_f6 \+= 4294967296\.0f;\s*\}\n", "", unstepped)
        yield "native-unsigned-conversion", collapsed.replace("u8 temp_t8;", "u32 temp_t8;")
    elif name == "releaseMenuAssetHandles":
        code = (OUT / f"{name}--14.c").read_text().replace("temp_a0 != -1", "-1 != temp_a0")
        yield "base-stride-reversed-compare", code
        code = code.replace("s16 *var_s0;", "s16 *var_s0;\n    s16 *end;")
        code = code.replace("    var_s0 =", "    end = (s16 *)&gMenuAsciiFontPaletteIndex;\n    var_s0 =")
        code = code.replace("var_s0 != (s16 *)&gMenuAsciiFontPaletteIndex", "var_s0 != end")
        yield "explicit-loop-end", code
        yield "explicit-loop-end-original-compare", code.replace("-1 != temp_a0", "temp_a0 != -1")


def main():
    if (OUT / "probe2.json").exists():
        raise SystemExit("preserve prior probe")
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    rows = []
    for name in ("Fvibup", "Fvibdown", "releaseMenuAssetHandles"):
        repo = isolate(REPO, NATIVE / "probe2" / name, name)
        ws = repo / "nonmatchings" / name
        for label, code in variants(name):
            parent = {"Fvibup": 3, "Fvibdown": 8, "releaseMenuAssetHandles": 14}[name]
            if label == "native-unsigned-conversion":
                parent = 1 if name == "Fvibup" else 6
            attempt = score_logged(ws, repo, name, code, conn=conn,
                strategy="repair-mechanisms-probe2:" + label, run_id="repair-mechanisms-probe2:" + name,
                parent_attempt_id=parent, action=label, model="assistant-development-probe",
                prompt="Follow-up to recorded target instruction differences; no reference body.",
                extra={"training_eligible": False, "assistance_tier": "project-header-assisted"})
            stem = OUT / f"{name}--{attempt.receipt_id}"
            stem.with_suffix(".c").write_text(code)
            stem.with_suffix(".json").write_text(json.dumps(_attempt_to_verdict(attempt), indent=2))
            row = {"function": name, "label": label, "receipt_id": attempt.receipt_id,
                   "compiled": attempt.compiled, "score": attempt.score, "exact": attempt.exact,
                   "frontend": (attempt.frontend or {}).get("passed")}
            rows.append(row)
            (OUT / "probe2.json").write_text(json.dumps(rows, indent=2))
            print(json.dumps(row), flush=True)
    conn.close()


if __name__ == "__main__":
    main()
