"""Development-only replay-policy pilot. Run in WSL with the SBK1 venv.

python eval/results/dream-search-20260922/pilot.py --run-id pilot-v1

All inputs are frozen solver candidates, never reference function bodies. Existing
training exclusions remain in force. Costs include world collection and baselines.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.search_replay import Policy, Replay, digest, merge_worlds, run, save_world, select_policy
from eval.search_scheduler import Online
from eval.tool_agent_run import _attempt_to_verdict
from solver import regalloc_mutations, regalloc_search, workspace

OUT = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
DEV = ROOT / "eval/results/dev-set-20260921"
CALIBRATION = ["Fdistort", "__MusIntProcessVibrato", "__ull_divremi"]
FRESH = ["__MusIntProcessWobble", "FrandPan", "updateRacePlayerAirborneLaunch", "updateRacePlayerMode06TerrainFall"]
POLICIES = [Policy("breadth", "breadth"), Policy("greedy", "greedy"),
            Policy("balanced-2", "balanced", 2), Policy("balanced-8", "balanced", 8)]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def compiler_identity(repo, ws):
    # Hash binary/config/header content without reading reference implementation C.
    files = [repo / "Makefile", ws / "build.sh", ws / "prelude.inc", ws / ".compiler-target.json",
             ws / ".diff_algorithm", repo / "tools/textconv.py", repo / "tools/charmap.txt"]
    files += sorted(ws.glob("*.py")) + sorted(ws.glob("target*"))
    files += sorted((repo / "include").rglob("*.h"))
    files += sorted((repo / "src").rglob("*.h"))
    files += sorted((repo / "tools/ido-recomp").rglob("*"))
    files += sorted((repo / "tools/asm_processor").rglob("*.py"))
    for executable in ("clang", "mips-linux-gnu-as", "mips-linux-gnu-objcopy", "mips-linux-gnu-objdump",
                       "mips-linux-gnu-nm", "diff", "bash"):
        found = shutil.which(executable)
        if found:
            files.append(Path(found))
    files.append(Path(sys.executable))
    from solver import compiler_recipe
    files += [Path(compiler_recipe.__file__)]
    rows = [(str(p.relative_to(repo)) if p.is_relative_to(repo) else p.name,
             hashlib.sha256(p.read_bytes()).hexdigest()) for p in files if p.is_file()]
    return digest(rows)


def logged_score(ws, repo, name, source, conn, **kwargs):
    try:
        attempt = workspace.score(ws, repo, name, source, conn=conn, func=name, **kwargs)
    except Exception as exc:
        attempt = workspace.Attempt(False, 0.0, False, "", f"{type(exc).__name__}: {exc}", "")
        workspace.record_attempt(conn, name, source, attempt, **kwargs)
    if attempt.receipt_id is None:
        raise RuntimeError("compiler attempt missing durable receipt")
    return attempt


def confirmation_sources(rows):
    return {(r["function"], digest(r["source"])): r["source"] for r in rows if r["exact"]}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--calibration-budget", type=int, default=32)
    parser.add_argument("--budget", type=int, default=48)
    args = parser.parse_args()
    if not args.run_id.replace("-", "").isalnum() or min(args.budget, args.calibration_budget) <= 0:
        parser.error("safe run ID and positive budgets required")
    native = Path.home() / "decomp/experiments/dream-search-20260922" / args.run_id
    output = OUT / args.run_id
    native.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    conn = sqlite3.connect(native / "attempts.sqlite")
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    ro = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         ro.execute(f"SELECT {','.join(cols)} FROM {table}"))
    conn.commit()
    ro.close()
    entries = {r["function"]: r for r in json.loads((DEV / "dev-set.json").read_text())["entries"]}
    generator_files = sorted((ROOT / "solver").glob("*.py"))
    generator_files += [ROOT / "eval/search_replay.py", ROOT / "eval/search_scheduler.py", Path(__file__)]
    generator_sha = digest({p.name: digest(p.read_text()) for p in generator_files})
    frozen_sources = {}
    for name in CALIBRATION + FRESH:
        assert entries[name]["assistance"]["tier"] != "reference-source-assisted"
        source = (DEV / "sources" / f"{name}.c").read_text()
        assert digest(source) == entries[name]["sha256"]
        frozen_sources[name] = source
    assert not set(CALIBRATION) & set(FRESH)
    report = {"kind": "dream-inspired-development-scheduler-pilot", "run_id": args.run_id,
              "training_eligible": False, "production_mutated": False,
              "calibration": CALIBRATION, "fresh_development": FRESH,
              "calibration_budget": args.calibration_budget, "fresh_budget": args.budget,
              "policies": [asdict(p) for p in POLICIES], "generator_sha256": generator_sha,
              "sources": {k: digest(v) for k, v in frozen_sources.items()},
              "database": str(native / "attempts.sqlite"), "collection": [], "comparison": [], "confirmations": []}
    write(output / "preregistration.json", report)

    def make_compiler(name, phase, arm):
        repo = isolate(REPO, native / phase / name / arm, name)
        ws = repo / "nonmatchings" / name
        receipts = []

        def compile_candidate(source, label, parent_receipt):
            started = time.monotonic()
            kwargs = dict(strategy=f"dream-search:{phase}:{arm}:{label}",
                          run_id=f"dream-search:{args.run_id}:{phase}:{name}:{arm}",
                          parent_attempt_id=parent_receipt, action=label,
                          model="deterministic-regalloc", prompt="Frozen development candidate; deterministic mutation.",
                          extra={"training_eligible": False, "assistance": entries[name]["assistance"]["tier"],
                                 "generator_sha256": generator_sha,
                                 "lineage_status": "unavailable-legacy-callback" if arm == "beam" else "explicit"})
            attempt = logged_score(ws, repo, name, source, conn, **kwargs)
            verdict = _attempt_to_verdict(attempt)
            verdict["exact"] = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
            verdict["seconds"] = time.monotonic() - started
            dump = ws / f"{name}_object_dump_normalized.s"
            verdict["dump"] = dump.read_text() if attempt.compiled and dump.exists() else None
            receipts.append({"receipt_id": attempt.receipt_id, "source_sha256": digest(source),
                             "exact": verdict["exact"], "compiled": attempt.compiled})
            return verdict

        return repo, ws, compile_candidate, receipts

    def scheduled(name, policy, phase, budget):
        repo, ws, compile_candidate, receipts = make_compiler(name, phase, policy.name)
        context = {"task": name, "initial_sha256": digest(frozen_sources[name]),
                   "target_sha256": hashlib.sha256((ws / "target.o").read_bytes()).hexdigest(),
                   "compiler_sha256": compiler_identity(repo, ws), "generator_sha256": generator_sha,
                   "assistance": entries[name]["assistance"]["tier"], "training_eligible": False}
        path = output / f"{phase}--{name}--{policy.name}.world.json"
        env = Online(frozen_sources[name], compile_candidate,
                     lambda source, diff: regalloc_mutations.variants(source, name, diff), context,
                     checkpoint=lambda w: save_world(w, path))
        started = time.monotonic()
        result = run(env, policy, budget)
        assert compiler_identity(repo, ws) == context["compiler_sha256"], "build/scoring inputs changed during run"
        replayed = run(Replay(env.world), policy, budget)
        assert result == replayed, "online/replay disagreement"
        best = next(n for n in env.world["nodes"] if n["id"] == result["best_id"])
        row = {"function": name, "arm": policy.name, "assistance": context["assistance"],
               **result, "seconds": time.monotonic() - started, "world": path.name,
               "best_source_sha256": best["source_sha256"], "receipts": receipts}
        (output / f"{phase}--{name}--{policy.name}.best.c").write_text(best["source"])
        print(json.dumps({k: v for k, v in row.items() if k not in {"trace", "receipts"}}), flush=True)
        return row, env.world, best["source"]

    worlds, observed_sources = [], []
    for name in CALIBRATION:
        branches = []
        for policy in POLICIES:
            row, world, source = scheduled(name, policy, "collection", args.calibration_budget)
            observed_sources.append({"function": name, "exact": row["exact"], "source": source})
            report["collection"].append(row)
            branches.append(world)
            write(output / "report.json", report)
        merged = merge_worlds(branches)
        save_world(merged, output / f"calibration--{name}.world.json")
        worlds.append(merged)
    selection = select_policy(worlds, POLICIES, POLICIES[0], budget=args.calibration_budget)
    write(output / "selection.json", selection)  # Freeze BEFORE reading fresh outcomes.
    selected = Policy(**selection["policy"])
    report["selection_sha256"] = digest((output / "selection.json").read_text())
    report["selected"] = selected.name
    print(json.dumps({"selected": selected.name, "selection_valid": selection["selection_valid"]}), flush=True)

    for name in FRESH:
        for policy in dict.fromkeys([POLICIES[0], selected]):
            row, _, source = scheduled(name, policy, "fresh", args.budget)
            report["comparison"].append(row)
            observed_sources.append({"function": name, "exact": row["exact"], "source": source})
            write(output / "report.json", report)
        repo, ws, compile_candidate, receipts = make_compiler(name, "fresh", "beam")

        def legacy_compile(source, label):
            v = compile_candidate(source, label, None)
            return regalloc_search.Compiled(v["compiled"], v["exact"], v["dump"], v["diff"])

        started = time.monotonic()
        baseline = legacy_compile(frozen_sources[name], "baseline")
        target = (ws / "target_object_dump_normalized.s").read_text()
        result = regalloc_search.search(name, frozen_sources[name], legacy_compile, target,
                                       budget=args.budget - 1, beam=3, depth=4, baseline=baseline)
        best_score = conn.execute("SELECT MAX(score) FROM attempts WHERE id IN (%s)" % ",".join("?" for _ in receipts),
                                  [r["receipt_id"] for r in receipts]).fetchone()[0]
        row = {"function": name, "arm": "beam", "exact": result.exact, "best_score": best_score,
               "compiles": len(receipts), "seconds": time.monotonic() - started,
               "best_source_sha256": digest(result.best_source), "search": result.summary(), "receipts": receipts,
               "assistance": entries[name]["assistance"]["tier"]}
        (output / f"fresh--{name}--beam.best.c").write_text(result.best_source)
        observed_sources.append({"function": name, "exact": result.exact, "source": result.best_source})
        report["comparison"].append(row)
        write(output / "report.json", report)
        print(json.dumps({k: v for k, v in row.items() if k != "receipts"}), flush=True)

    for (name, sha), source in confirmation_sources(observed_sources).items():
        _, _, compile_candidate, _ = make_compiler(name, "confirm", sha[:12])
        verdict = compile_candidate(source, "independent-recompile", None)
        write(output / f"confirmed--{name}--{sha[:12]}.json", verdict)
        report["confirmations"].append({"function": name, "source_sha256": sha, "exact": verdict["exact"],
                                        "receipt_id": verdict["receipt_id"]})
    report["total_compiles"] = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    report["total_edges"] = conn.execute("SELECT COUNT(*) FROM attempt_edges").fetchone()[0]
    expected = sum(r["compiles"] for r in report["collection"] + report["comparison"]) + len(report["confirmations"])
    assert report["total_compiles"] == expected
    assert all(c["exact"] for c in report["confirmations"])
    assert generator_sha == digest({p.name: digest(p.read_text()) for p in generator_files}), "implementation changed during run"
    report["complete"] = True
    write(output / "report.json", report)
    print(json.dumps({"complete": True, "total_compiles": report["total_compiles"],
                      "confirmation_count": len(report["confirmations"])}), flush=True)
    conn.close()


if __name__ == "__main__":
    main()
