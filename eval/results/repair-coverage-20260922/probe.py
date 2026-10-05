"""Assembly-led repair coverage probe; no reference implementation bodies.

All compiles and failures are logged privately. Development candidates only.
The pre-existing binary helper recognizer is the control, not a new discovery.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_repair, wide_runtime_interfaces, workspace

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/repair-coverage-20260922"
REPO = Path.home() / "decomp/sbk1"
DEV = ROOT / "eval/results/dev-set-20260921"


def candidate_rows(name, source, assembly, recipe, target):
    changed, report = wide_runtime_interfaces.reconstruct_helper(source, name, assembly,
        big_endian_o32=frontend_repair.big_endian_o32(target),
        compiler_mips=recipe.get("settings", {}).get("C_MIPS", ""))
    if changed != source:
        yield "existing-closed-reconstruction", changed, report
    if name == "__ull_divremi":
        # Target: wide value in a2/a3; signed halfword at caller sp+0x12;
        # repeated unsigned division, quotient/remainder stored through a0/a1.
        for typ in ("u16", "s16", "s32"):
            for expression in ("(s16) divisor", "(u64) (s16) divisor", "divisor"):
                code = ('#include "common.h"\n\n'
                    f'void {name}(u64 *quotient, u64 *remainder, u64 value, {typ} divisor) {{\n'
                    f'    *quotient = value / ({expression});\n'
                    f'    *remainder = value % ({expression});\n}}\n')
                yield f"packed-abi-{typ}-{expression.replace(' ', '_')}", code, {"evidence": "target instruction stream"}
    if name == "__ll_mod":
        # Target tests remainder/divisor signs, then adds divisor on disagreement.
        for extra_temp in (False, True):
            for volatile in (False, True):
                code = ('#include "common.h"\n\n'
                    f's64 {name}(s64 dividend, s64 divisor) {{\n'
                    f'    {"volatile " if volatile else ""}s64 remainder;\n'
                    + ('    s64 first;\n    first = dividend % divisor;\n    remainder = first;\n' if extra_temp
                       else '    remainder = dividend % divisor;\n')
                    + f'    if ((({"first" if extra_temp else "remainder"} < 0) && (divisor > 0)) || ((remainder > 0) && (divisor < 0))) {{\n'
                    '        remainder += divisor;\n    }\n    return remainder;\n}\n')
                yield f"signed-modulo-{int(extra_temp)}-{int(volatile)}", code, {"evidence": "target instruction stream"}


def main():
    NATIVE.mkdir(parents=True, exist_ok=True)
    db = NATIVE / "attempts.sqlite"
    if db.exists():
        raise SystemExit("probe already exists; preserve its receipts")
    conn = sqlite3.connect(db)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    ro = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         ro.execute(f"SELECT {','.join(cols)} FROM {table}"))
    conn.commit()
    report = {"kind": "assembly-led-development-probe", "training_eligible": False, "rows": []}
    names = ["__ll_rem", "__ull_divremi", "__ll_mod", "__ull_rem", "__ull_div"]
    for name in names:
        repo = isolate(REPO, NATIVE / "probe" / name, name)
        ws = repo / "nonmatchings" / name
        path = DEV / "sources" / f"{name}.c"
        source = path.read_text() if path.exists() else (ws / "base.c").read_text()
        (OUT / f"{name}--initial.c").write_text(source)
        assembly = workspace.target_asm(ws, name)
        (OUT / f"{name}--target.s").write_text(assembly)
        prior = ro.execute("SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1", (name,)).fetchone()[0]
        parent = None
        def compile_one(label, code, evidence):
            nonlocal parent
            started = time.monotonic()
            kw = dict(strategy=f"coverage-probe:{label}", run_id=f"coverage-probe:{name}",
                      parent_attempt_id=parent, action=label, model="assistant-development-probe",
                      prompt="Candidate from frozen draft and binary-derived target assembly only.",
                      extra={"training_eligible": False, "assistance_tier": "source-independent", "evidence": evidence})
            try:
                att = workspace.score(ws, repo, name, code, conn=conn, func=name, **kw)
            except Exception as exc:
                att = workspace.Attempt(False, 0, False, "", f"{type(exc).__name__}: {exc}", "")
                workspace.record_attempt(conn, name, code, att, **kw)
            assert att.receipt_id is not None
            if parent is None:
                parent = att.receipt_id
            sha = hashlib.sha256(code.encode()).hexdigest()
            (OUT / f"{name}--{att.receipt_id}.c").write_text(code)
            verdict = _attempt_to_verdict(att)
            (OUT / f"{name}--{att.receipt_id}.json").write_text(json.dumps(verdict, indent=2))
            row = {"function": name, "label": label, "receipt_id": att.receipt_id,
                   "source_sha256": sha, "compiled": att.compiled, "exact": att.exact,
                   "frontend": (att.frontend or {}).get("passed"), "score": att.score,
                   "seconds": time.monotonic() - started, "prior_main_exact_attempts": prior}
            report["rows"].append(row)
            (OUT / "probe.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(row), flush=True)
            return att
        baseline = compile_one("baseline", source, {"source": str(path)})
        for label, code, evidence in candidate_rows(name, source, assembly, baseline.compiler_recipe or {}, ws / "target.o"):
            compile_one(label, code, evidence)
    conn.close()
    ro.close()


if __name__ == "__main__":
    main()
