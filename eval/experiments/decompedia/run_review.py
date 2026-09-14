"""Bounded Decompedia follow-up: compiler probes and inspected DEV replays.

Run in WSL with SBK1's venv. No target C bodies or heldout sources are read.
Each arm changes one source feature; attempts use the existing oracle/logger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import compiler_recipe, workspace
from eval.zero_token_harvest import heldout_names


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    conn = sqlite3.connect(args.db, timeout=60)
    panel = ROOT / "eval/sets/autonomy_wavefront_dev_24_v1.json"
    members = {r["function"] for r in json.loads(panel.read_text())["dev"]}
    names = ["waitRaceIntroFlyoverShortPanFinal", "initCharacterSelectCourseStatsBadge",
             "initCharacterSelectCoursePreviewPanel6", "fadeInEndingCreditsFlow"]
    assert set(names) <= members
    assert not (set(names) & heldout_names(ROOT / "eval/sets"))
    frozen = []
    for name in names:
        row = conn.execute("SELECT a.id, a.source_code, t.name FROM attempts a "
            "JOIN functions f ON f.addr=a.func_addr JOIN tus t ON t.id=f.tu_id "
            "WHERE f.name=? AND a.compiled=1 ORDER BY a.score DESC,a.id DESC LIMIT 1",
            (name,)).fetchone()
        if not row:
            raise RuntimeError("No compiling DEV parent: " + name)
        frozen.append({"function": name, "parent_attempt_id": row[0],
                       "source": row[1], "source_sha256": sha(row[1].encode()), "tu": row[2]})
    recipe = compiler_recipe.resolve(args.repo, frozen[0]["tu"])
    cc = args.repo / "tools/ido-recomp/linux/cc"
    import shlex
    command = [str(cc), *shlex.split(recipe["settings"]["CFLAGS"]),
               *shlex.split(recipe["settings"]["C_OPT"])]
    receipt = {"schema_version": 1, "scope": "synthetic probes plus inspected header-assisted DEV",
        "heldout_used": False, "target_c_bodies_read": False, "model_calls": 0,
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "panel_sha256": sha(panel.read_bytes()), "recipe": recipe,
        "tool_hashes": {p.name: sha(p.read_bytes()) for p in cc.parent.iterdir()
                        if p.name in {"cc", "cfe", "uopt", "ugen", "as0", "as1"}},
        "frozen": [{k: v for k, v in r.items() if k != "source"} for r in frozen],
        "probes": [], "dev": []}

    def save():
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    save()  # Freeze identities and budget before compiling either arm.
    probes = [
        ("definition-leaf", "extern int g;\nvoid probe() { g++; }\n",
         "extern int g;\nvoid probe(void) { g++; }\n"),
        ("definition-calling", "extern void sink(int); extern int g;\nvoid probe() { sink(g); }\n",
         "extern void sink(int); extern int g;\nvoid probe(void) { sink(g); }\n"),
        ("callee-prototype", "extern void sink();\nvoid probe(void) { sink(); }\n",
         "extern void sink(void);\nvoid probe(void) { sink(); }\n"),
        ("ignored-return", "extern void sink(void);\nvoid probe(void) { sink(); }\n",
         "extern int sink(void);\nvoid probe(void) { sink(); }\n"),
        ("named-expression", "extern int g; extern void sink(int);\nvoid probe(void) { sink(g + 1); }\n",
         "extern int g; extern void sink(int);\nvoid probe(void) { int x; x = g + 1; sink(x); }\n"),
    ]
    with tempfile.TemporaryDirectory(prefix="decompedia-probe-") as tmp:
        temp = Path(tmp)
        for label, baseline, variant in probes:
            arms = {}
            for arm, source in (("baseline", baseline), ("variant", variant)):
                source_path, obj = temp / "probe.c", temp / "probe.o"
                source_path.write_text(source)
                (out / f"{label}-{arm}.c").write_text(source)
                proc = subprocess.run([*command, str(source_path), "-o", str(obj)],
                    cwd=args.repo, capture_output=True, text=True, timeout=60)
                result = {"source_sha256": sha(source.encode()), "returncode": proc.returncode,
                          "diagnostics": proc.stdout + proc.stderr, "command": command}
                if not proc.returncode:
                    dump = subprocess.run(["mips-linux-gnu-objdump", "-dr", "-M", "regnames=32", str(obj)],
                        check=True, capture_output=True, text=True).stdout
                    raw = temp / "text.bin"
                    subprocess.run(["mips-linux-gnu-objcopy", "-O", "binary", "-j", ".text", str(obj), str(raw)], check=True)
                    m = re.search(r"addiu\s+sp,sp,-(\d+)", dump)
                    result.update(text_sha256=sha(raw.read_bytes()), frame_bytes=int(m[1]) if m else 0)
                    (out / f"{label}-{arm}.s").write_text(dump)
                    (out / f"{label}-{arm}.o").write_bytes(obj.read_bytes())
                arms[arm] = result
            receipt["probes"].append({"hypothesis": label, "arms": arms})
            save()
            print(label, {k: (v.get("frame_bytes"), v.get("text_sha256")) for k,v in arms.items()}, flush=True)

    for parent in frozen:
        name, source = parent["function"], parent["source"]
        pattern = re.compile(rf"(\b{re.escape(name)}\s*\()(\s*void\s*|\s*)(\)\s*\{{)")
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            receipt["dev"].append({"function": name, "status": "declined-nonzero-or-ambiguous-parameters"})
            save()
            continue
        match = matches[0]
        replacement = "" if match[2].strip() else "void"
        variant = source[:match.start(2)] + replacement + source[match.end(2):]
        ws = workspace.bootstrap(args.repo, name)
        target_hash = sha((ws / "target.s").read_bytes())
        row = {"function": name, "parent_attempt_id": parent["parent_attempt_id"],
               "target_sha256": target_hash, "arms": {}}
        baseline_id = None
        for arm, code in (("baseline", source), ("variant", variant)):
            att = workspace.score(ws, args.repo, name, code, conn=conn, func=name,
                strategy="decompedia-definition-prototype-" + arm,
                run_id=out.name, parent_attempt_id=parent["parent_attempt_id"] if arm == "baseline" else baseline_id,
                relation="reverify" if arm == "baseline" else "derive",
                action="reverify frozen source" if arm == "baseline" else "toggle only definition void/empty parameters",
                run_kind="inspected-development", token_cost=0)
            if arm == "baseline":
                baseline_id = att.receipt_id
            (out / f"{name}-{arm}.c").write_text(code)
            row["arms"][arm] = {"source_sha256": sha(code.encode()), "attempt_id": att.receipt_id,
                "compiled": att.compiled, "exact": att.exact, "score": att.score,
                "frontend": att.frontend, "verification": att.verification,
                "compiler_recipe": att.compiler_recipe, "diff": att.diff,
                "diagnostics": att.compiler_stderr}
            print(name, arm, att.receipt_id, att.compiled, att.exact, att.score, flush=True)
        assert target_hash == sha((ws / "target.s").read_bytes())
        receipt["dev"].append(row)
        save()
    conn.close()


if __name__ == "__main__":
    main()
