"""Development-only, assistant-authored probes. No reference function bodies.

Run with the SBK1 WSL venv. All compiles use isolated native workspaces and
a private attempt database; candidates and verdicts are also exported here.
This is experimental evidence, not an enabled solver rule or training data.
"""
import hashlib
import itertools
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import workspace

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/residual-repair-20260922"
REPO = Path.home() / "decomp/sbk1"
DEV = ROOT / "eval/results/dev-set-20260921"


def candidates(name, source):
    yield "baseline", source
    if "--round3" in sys.argv:
        if name.startswith("osEPiRaw"):
            text = (OUT / f"{name}--register-status-literal-1.c").read_text()
            # Hardware address is visible in target.s's instruction words.
            prefix, body = text.split(f"s32 {name}(", 1)
            body = body.replace("PI_STATUS_REG", "(*(volatile u32 *)0xA4600010U)")
            yield "literal-status-address", prefix + f"s32 {name}(" + body
        elif name == "FrandPan":
            text = source.replace("    s8 temp_v0;\n", "")
            text = text.replace("    temp_v0 = __MusIntRandom((s32) temp_a0);\n    arg0->pan = temp_v0;",
                                "    arg0->pan = __MusIntRandom((s32) temp_a0);")
            for compound in (False, True):
                variant = text.replace("arg0->pan = temp_v0 + *arg1;",
                    "arg0->pan += *arg1;" if compound else "arg0->pan = arg0->pan + *arg1;")
                yield f"field-local-eliminate-{int(compound)}", variant
        return
    if "--round2" in sys.argv:
        if name.startswith("osEPiRaw"):
            for literal in (False, True):
                text = source.replace("    if (PI_STATUS_REG & 3)",
                    "    register u32 status;\n    if ((status = PI_STATUS_REG) & 3)")
                text = text.replace("while (PI_STATUS_REG & 3)", "while ((status = PI_STATUS_REG) & 3)")
                text = text.replace("extern s32 PI_STATUS_REG;", "extern volatile u32 PI_STATUS_REG;")
                text = text.replace("*(s32 *)", "*(volatile u32 *)")
                if literal:
                    text = text.replace("(s32) &D_A0000000", "0xA0000000U")
                yield f"register-status-literal-{int(literal)}", text
        elif name == "FrandPan":
            for postinc, register_args, reread in itertools.product((False, True), repeat=3):
                text = source.replace("s8 temp_v0;", "s32 temp_v0;")
                if postinc:
                    text = text.replace("temp_a0 = *arg1;\n    arg1 += 1;", "temp_a0 = *arg1++;")
                    text = text.replace("temp_v0 + *arg1;", "temp_v0 + *arg1++;")
                    text = text.replace("return arg1 + 1;", "return arg1;")
                if register_args:
                    text = text.replace("PlayerCommandState *arg0, u8 *arg1", "register PlayerCommandState *arg0, register u8 *arg1")
                if reread:
                    text = text.replace("arg0->pan = temp_v0 +", "arg0->pan = arg0->pan +")
                yield f"postinc-{int(postinc)}-reg-args-{int(register_args)}-reread-{int(reread)}", text
        return
    if name.startswith("osEPiRaw"):
        # Independent knobs distinguish fixed-address codegen, volatile access,
        # and the status local visible as a3 in the target assembly.
        for literal, vol, local in itertools.product((False, True), repeat=3):
            if not any((literal, vol, local)):
                continue
            text = source
            if literal:
                text = text.replace("(s32) &D_A0000000", "0xA0000000U")
            if vol:
                text = text.replace("extern s32 PI_STATUS_REG;", "extern volatile u32 PI_STATUS_REG;")
                text = text.replace("*(s32 *)", "*(volatile u32 *)")
            if local:
                text = text.replace("    if (PI_STATUS_REG & 3)",
                                    "    u32 status;\n    if ((status = PI_STATUS_REG) & 3)")
                text = text.replace("while (PI_STATUS_REG & 3)", "while ((status = PI_STATUS_REG) & 3)")
            yield f"literal-{int(literal)}-volatile-{int(vol)}-local-{int(local)}", text
    elif name == "Fdistort":
        for wide, postinc in itertools.product((False, True), repeat=2):
            if not any((wide, postinc)):
                continue
            text = source.replace("u8 var_v1;", "s32 var_v1;") if wide else source
            if postinc:
                text = text.replace("var_v1 = *arg1;", "var_v1 = *arg1++;")
                text = text.replace("(arg1 + 1)", "arg1")
            yield f"wide-{int(wide)}-postinc-{int(postinc)}", text
    elif name == "FrandPan":
        for wide, arg_home in itertools.product((False, True), repeat=2):
            if not any((wide, arg_home)):
                continue
            text = source.replace("s8 temp_v0;", "s32 temp_v0;") if wide else source
            if arg_home:
                text = text.replace("PlayerCommandState *arg0, u8 *arg1", "PlayerCommandState *volatile arg0, u8 *volatile arg1")
            yield f"wide-{int(wide)}-arg-home-{int(arg_home)}", text


def main():
    NATIVE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    ro = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        rows = ro.execute(f"SELECT {','.join(columns)} FROM {table}").fetchall()
        conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", rows)
    conn.commit()
    entries = {r["function"]: r for r in json.loads((DEV / "dev-set.json").read_text())["entries"]}
    names = [a for a in sys.argv[1:] if not a.startswith("--")] or ["osEPiRawWriteIo", "osEPiRawReadIo", "Fdistort", "FrandPan"]
    report = {"kind": "assistant-authored-development-probe", "training_eligible": False,
              "production_mutated": False, "private_database": str(NATIVE / "attempts.sqlite"), "rows": []}
    for name in names:
        repo = isolate(REPO, NATIVE / "builds" / name, name)
        ws = repo / "nonmatchings" / name
        source = (DEV / "sources" / f"{name}.c").read_text()
        assert hashlib.sha256(source.encode()).hexdigest() == entries[name]["sha256"]
        prior = ro.execute("SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1", (name,)).fetchone()[0]
        parent = None
        for label, code in candidates(name, source):
            started = time.monotonic()
            prompt = ("Development probe derived from frozen candidate and target.s only. "
                      "Test the labelled source hypothesis; never use reference bodies. " + label)
            attempt = workspace.score(ws, repo, name, code, conn=conn, func=name,
                strategy="residual-repair:" + label, model="codex-assistant-authored",
                run_id="residual-repair-20260922", parent_attempt_id=parent,
                action=label, prompt=prompt, extra={"training_eligible": False, "assistance": entries[name]["assistance"]})
            if label == "baseline":
                parent = attempt.receipt_id
            verdict = _attempt_to_verdict(attempt)
            stem = f"{name}--{label}"
            (OUT / (stem + ".c")).write_text(code)
            (OUT / (stem + ".json")).write_text(json.dumps(verdict, indent=2))
            dump = ws / f"{name}_object_dump_normalized.s"
            if attempt.compiled and dump.exists():
                (OUT / (stem + ".s")).write_bytes(dump.read_bytes())
            boundary = (attempt.verification or {}).get("function_boundary") or {}
            row = {"function": name, "label": label, "compiled": attempt.compiled,
                   "score": attempt.score, "exact": attempt.exact, "function_boundary": boundary,
                   "frontend": attempt.frontend, "receipt_id": attempt.receipt_id,
                   "prior_object_exact_attempts": prior, "assistance": entries[name]["assistance"]["tier"],
                   "sha256": hashlib.sha256(code.encode()).hexdigest(),
                   "seconds": round(time.monotonic() - started, 3)}
            report["rows"].append(row)
            print(json.dumps({k: v for k, v in row.items() if k not in ("function_boundary", "frontend")}), flush=True)
            report_name = "probe-round3.json" if "--round3" in sys.argv else "probe-round2.json" if "--round2" in sys.argv else "probe.json"
            (OUT / report_name).write_text(json.dumps(report, indent=2))
    conn.close()
    ro.close()


if __name__ == "__main__":
    main()
