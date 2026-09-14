"""One-variable probes for the seven queued guide claims; no model or ROM edits."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import compiler_recipe, workspace
from eval import matched


def sha(data):
    return hashlib.sha256(data).hexdigest()


PROBES = [
    (3, "comparison-normalization", "int probe(int x) { return x > 7; }\n",
     "int probe(int x) { return x >= 8; }\n"),
    (5, "loop-continue", "void probe(int *d, int *s) { int i; for(i=0;i<8;i++) { d[i]=s[i]+1; } }\n",
     "void probe(int *d, int *s) { int i; for(i=0;i<8;i++) { d[i]=s[i]+1; continue; } }\n"),
    (6, "array-address", "extern void sink(int *); void probe(int *a) { int i; for(i=0;i<10;i++) sink(&a[i]); }\n",
     "extern void sink(int *); void probe(int *a) { int i; for(i=0;i<10;i++) sink(a+i); }\n"),
    (7, "struct-copy", "struct S { int a,b,c,d; }; void probe(struct S *d,struct S *s) { *d=*s; }\n",
     "struct S { int a,b,c,d; }; void probe(struct S *d,struct S *s) { d->a=s->a;d->b=s->b;d->c=s->c;d->d=s->d; }\n"),
    (8, "switch-default-order", "extern void sink(int); void probe(int x) { switch(x) { default:sink(9);break;case 0:sink(2);break;case 1:sink(3);break;case 2:sink(4);break; } }\n",
     "extern void sink(int); void probe(int x) { switch(x) { case 0:sink(2);break;case 1:sink(3);break;case 2:sink(4);break;default:sink(9);break; } }\n"),
    (9, "rodata-visibility", "extern const float k; extern void sink(float); void probe(int n) { while(n-->0) sink(k); }\n",
     "const float k=1.25f; extern void sink(float); void probe(int n) { while(n-->0) sink(k); }\n"),
    (10, "conditional-load", "int probe(int c,int *p) { if(c) return *p; return 0; }\n",
     "int probe(int c,int *p) { int r=0; if(c) r=*p; return r; }\n"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    repo = Path.home() / "decomp/sbk1"
    conn = sqlite3.connect(Path.home() / "decomp/kb-sbk1.sqlite", timeout=60)
    frozen_dir = ROOT / "eval/experiments/decompedia/current-claims-v1"
    frozen = json.loads((frozen_dir / "receipt.json").read_text())
    recipe = compiler_recipe.resolve(repo, "build/src/menu/main_menu/main_menu_scene_model.o")
    cc = repo / "tools/ido-recomp/linux/cc"
    flags = shlex.split(recipe["settings"]["CFLAGS"]) + shlex.split(recipe["settings"]["C_OPT"])
    report = {"scope": "synthetic compiler behavior and frozen inspected DEV candidates",
              "heldout_used": False, "model_calls": 0, "reference_bodies_read": False,
              "recipe": recipe, "compiler_sha256": sha(cc.read_bytes()),
              "runner_sha256": sha(Path(__file__).read_bytes()),
              "probe_count": len(PROBES), "probe_arms": 2,
              "recorded_exact_pool": len(matched.already_matched(conn, str(ROOT / "matched_recovered"))),
              "probes": [], "replays": []}
    def save():
        (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    save()
    with tempfile.TemporaryDirectory(prefix="decompedia-queued-") as temp:
        temp = Path(temp)
        for queue_id, label, baseline, variant in PROBES:
            pair = {"queue_id": queue_id, "label": label, "arms": {}}
            for arm, source in (("baseline", baseline), ("variant", variant)):
                src, obj = temp / "probe.c", temp / "probe.o"
                src.write_text(source)
                (out / f"{label}-{arm}.c").write_text(source)
                proc = subprocess.run([str(cc), *flags, str(src), "-o", str(obj)],
                    cwd=repo, capture_output=True, text=True, timeout=60)
                result = {"returncode": proc.returncode, "diagnostics": proc.stdout + proc.stderr,
                          "source_sha256": sha(source.encode())}
                if proc.returncode == 0:
                    asm = subprocess.run(["mips-linux-gnu-objdump", "-dr", "-M", "regnames=32", str(obj)],
                        capture_output=True, text=True, check=True).stdout
                    raw = temp / "text.bin"
                    subprocess.run(["mips-linux-gnu-objcopy", "-O", "binary", "-j", ".text", str(obj), str(raw)], check=True)
                    result.update(text_sha256=sha(raw.read_bytes()),
                        object_sha256=sha(obj.read_bytes()),
                        instruction_count=len(re.findall(r"(?m)^\s*[0-9a-f]+:\s+[0-9a-f]{8}\s", asm)))
                    (out / f"{label}-{arm}.s").write_text(asm)
                    (out / f"{label}-{arm}.o").write_bytes(obj.read_bytes())
                pair["arms"][arm] = result
            pair["same_text"] = pair["arms"]["baseline"].get("text_sha256") == pair["arms"]["variant"].get("text_sha256")
            report["probes"].append(pair)
            save()
            print(queue_id, label, "same_text", pair["same_text"], flush=True)

    for item in frozen["functions"]:
        name = item["name"]
        if item["heldout"] or "best_stored" not in item:
            continue
        source = (frozen_dir / f"{name}.c").read_text()
        assert sha(source.encode()) == item["best_stored"]["source_sha256"]
        ws = workspace.bootstrap(repo, name)
        target_sha = sha((ws / "target.s").read_bytes())
        arms = [("baseline", source)]
        if name == "compressRaceRecordReplayData":
            for old, new, tag in [
                ("t4 = src + v0;", "t4 = &src[v0];", "current-address"),
                ("s1 = a2 + src;", "s1 = &src[a2];", "previous-address"),
                ("t5 = dst + 1;", "t5 = &dst[1];", "output-address")]:
                if source.count(old) != 1:
                    raise ValueError("Frozen address expression changed")
                arms.append((tag, source.replace(old, new, 1)))
        row = {"function": name, "parent_attempt_id": item["best_stored"]["id"],
               "target_sha256": target_sha, "arms": {}}
        baseline_id = None
        for arm, code in arms:
            att = workspace.score(ws, repo, name, code, conn=conn, func=name,
                strategy="decompedia-queued-" + arm, run_id=out.name,
                parent_attempt_id=item["best_stored"]["id"] if arm == "baseline" else baseline_id,
                relation="reverify" if arm == "baseline" else "derive",
                action=arm, run_kind="inspected-development", token_cost=0)
            if arm == "baseline":
                baseline_id = att.receipt_id
            (out / f"{name}-{arm}.c").write_text(code)
            row["arms"][arm] = {"source_sha256": sha(code.encode()), "attempt_id": att.receipt_id,
                "compiled": att.compiled, "exact": att.exact, "score": att.score,
                "frontend": att.frontend, "verification": att.verification,
                "compiler_recipe": att.compiler_recipe, "diff": att.diff,
                "diagnostics": att.compiler_stderr}
            print(name, arm, att.receipt_id, att.compiled, att.exact, att.score, flush=True)
        assert target_sha == sha((ws / "target.s").read_bytes())
        report["replays"].append(row)
        save()
    conn.close()


if __name__ == "__main__":
    main()
