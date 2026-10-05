"""Bounded development probes from source residuals; every compile gets a receipt."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "eval/results/repair-coverage-20260922"))
from logged_compile import score_logged
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import structural_mutations

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/repair-mechanisms-20260922"
REPO = Path.home() / "decomp/sbk1"


def proposals(name, source):
    if name in ("Fvibup", "Fvibdown"):
        wide = source.replace("u8 temp_t8;", "u32 temp_t8;")
        signed_cast = wide.replace("(f32) temp_t8;", "(f32) (s32) temp_t8;")
        for label, code in [("wide", wide), ("conversion-idiom", signed_cast)]:
            yield label, code
            step = code.replace("    temp_t8 =", "    arg1 = (u8 *)arg1 + 2;\n    temp_t8 =")
            step = step.replace("((u8 *)arg1) + 0x2", "((u8 *)arg1) + 0x0")
            step = step.replace("((u8 *)arg1) + 2 + 1", "((u8 *)arg1) + 1")
            yield label + "+cursor", step
    elif name == "releaseMenuAssetHandles":
        bytebase = source.replace("&gAssetHandles + 0xE", "(u8 *)&gAssetHandles + 0xE")
        yield "global-byte-address", bytebase
        stride = source.replace("var_s0 += 2", "var_s0 += 1")
        yield "pointer-element-stride", stride
        both = bytebase.replace("var_s0 += 2", "var_s0 += 1")
        yield "base-and-stride", both
        yield "base-stride-localword", both.replace("s16 temp_a0;", "s32 temp_a0;")
    elif name == "loadMusicSequenceBank":
        bytebase = source.replace("(arg0 * 8) + &gMusicSequenceRomRanges", "(arg0 * 8) + (u8 *)&gMusicSequenceRomRanges")
        yield "global-byte-address", bytebase
        for label, kind, code in structural_mutations.variants(bytebase, name):
            yield "byte-address+" + label, code
    elif name == "FrandPan":
        for typ in ("s32", "u32"):
            code = source.replace("s8 temp_v0;", typ + " temp_v0;")
            yield "word-result-" + typ, code
            chain = code.replace("temp_v0 = __MusIntRandom((s32) temp_a0);\n    arg0->pan = temp_v0;",
                                 "arg0->pan = temp_v0 = __MusIntRandom((s32) temp_a0);")
            yield "chain-store-" + typ, chain
            final = chain.replace("temp_v0 + *arg1", "temp_v0 + *arg1++").replace("return arg1 + 1;", "return arg1;")
            yield "chain-final-cursor-" + typ, final


def main():
    NATIVE.mkdir(parents=True, exist_ok=True)
    db = NATIVE / "attempts.sqlite"
    if db.exists():
        raise SystemExit("Preserve the existing probe; use a separate run for further work.")
    conn = sqlite3.connect(db)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    ro = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         ro.execute(f"SELECT {','.join(cols)} FROM {table}"))
    conn.commit()
    rows = []
    for name in ("Fvibup", "Fvibdown", "releaseMenuAssetHandles", "loadMusicSequenceBank", "FrandPan"):
        path = OUT / "inputs" / name / "initial.c"
        if name == "FrandPan":
            path = ROOT / "eval/results/residual-repair-20260922/FrandPan--baseline.c"
        source = path.read_text()
        repo = isolate(REPO, NATIVE / "probe" / name, name)
        ws = repo / "nonmatchings" / name
        parent = None
        seen = set()
        for label, code in [("baseline", source), *proposals(name, source)]:
            if code in seen:
                continue
            seen.add(code)
            attempt = score_logged(ws, repo, name, code, conn=conn,
                strategy="repair-mechanisms-probe:" + label, run_id="repair-mechanisms-probe:" + name,
                parent_attempt_id=parent, action=label, model="assistant-development-probe",
                prompt="Read generated draft and binary assembly only; bounded hypothesis probe.",
                extra={"training_eligible": False, "assistance_tier": "project-header-assisted"})
            if parent is None:
                parent = attempt.receipt_id
            verdict = _attempt_to_verdict(attempt)
            stem = OUT / f"{name}--{attempt.receipt_id}"
            stem.with_suffix(".c").write_text(code)
            stem.with_suffix(".json").write_text(json.dumps(verdict, indent=2))
            row = {"function": name, "label": label, "receipt_id": attempt.receipt_id,
                   "source_sha256": hashlib.sha256(code.encode()).hexdigest(),
                   "compiled": attempt.compiled, "score": attempt.score, "exact": attempt.exact,
                   "frontend": (attempt.frontend or {}).get("passed")}
            rows.append(row)
            (OUT / "probe.json").write_text(json.dumps(rows, indent=2))
            print(json.dumps(row), flush=True)
    conn.close()
    ro.close()


if __name__ == "__main__":
    main()
