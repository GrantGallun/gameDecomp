"""Register-allocation bench: C in, immediate right/wrong out.

INPUT/OUTPUT (the gradient loop):

    python -m eval.regalloc_probe probe --cohort COHORT.json --function F [--source A.c ...]

compiles the function's campaign source (baseline) and each --source in one
isolated workspace and prints one JSON line per source:

    {"label", "compiled", "exact", "score", "gradient": [non_register, reg_insns, reg_operands],
     "signatures": {...}, "delta": {"verdict": better|worse|same|exact_shape, "fixed", "introduced"},
     "differences": [...]}

`exact` is the object oracle (workspace.score). `gradient` and `signatures` come
from `solver.regalloc_signature` and only say where to look.

SEARCH (enumerate mutations, follow the gradient):

    python -m eval.regalloc_probe search --cohort COHORT.json --out DIR [--only F] [--workers 2]
        [--budget 200] [--beam 3] [--depth 3]

writes DIR/<function>.jsonl (every compile: family, depth, parent, gradient, delta),
DIR/<function>.exact.c when a variant is object-exact, and DIR/summary.jsonl.
Nothing here writes campaign state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from solver import regalloc_mutations, regalloc_signature, workspace

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite"
SCHEMA = Path(__file__).resolve().parents[1] / "kb/schema.sql"


class Bench:
    """One isolated native workspace per function; compiles are cached by source hash."""

    def __init__(self, row: dict, repo: Path = REPO, kb: Path = KB):
        from eval import campaign_workers
        from solver import compiler_recipe
        self.function = row["name"]
        self.native = Path(tempfile.mkdtemp(prefix="regalloc-bench-"))
        self.isolated = campaign_workers.isolate(repo, self.native / "game", self.function)
        self.ws = self.isolated / "nonmatchings" / self.function
        with sqlite3.connect(f"file:{kb.as_posix()}?mode=ro", uri=True) as source_db:
            found = source_db.execute("SELECT f.addr,f.size,t.name FROM functions f JOIN tus t ON t.id=f.tu_id "
                                      "WHERE f.name=?", (self.function,)).fetchone()
        if found is None:
            raise ValueError("function/TU metadata missing from KB")
        self.conn = sqlite3.connect(self.native / "attempts.sqlite")
        self.conn.executescript(SCHEMA.read_text())
        self.conn.execute("INSERT INTO tus(id,name) VALUES(1,?)", (found[2],))
        self.conn.execute("INSERT INTO functions(addr,name,size,tu_id) VALUES(?,?,?,1)", (found[0], self.function, found[1]))
        self.conn.commit()
        compiler_recipe._resolve.cache_clear()
        self.cache: dict[str, dict] = {}
        self.target_text: str | None = None
        self.run_id = f"regalloc-probe-{time.time_ns()}"

    def close(self):
        self.conn.close()
        shutil.rmtree(self.native, ignore_errors=True)

    def run(self, source: str, label: str) -> dict:
        key = hashlib.sha256(source.encode()).hexdigest()
        if key in self.cache:
            return {**self.cache[key], "label": label, "cached": True}
        name = f"{self.function}_ra{len(self.cache)}"
        started = time.monotonic()
        attempt = workspace.score(self.ws, self.isolated, name, source, conn=self.conn, func=self.function,
                                  strategy="regalloc-probe", model="zero-model", run_id=self.run_id)
        if self.target_text is None:
            self.target_text = (self.ws / "target_object_dump_normalized.s").read_text()
        dump = self.ws / f"{name}_object_dump_normalized.s"
        frontend = attempt.frontend or {}
        result = {"label": label, "source_sha256": key, "compiled": bool(attempt.compiled),
                  "exact": bool(attempt.exact), "score": attempt.score,
                  "frontend_passed": frontend.get("passed"),
                  "seconds": round(time.monotonic() - started, 2)}
        result["_frontend_command"] = (frontend.get("recipe") or {}).get("command")
        if attempt.compiled and dump.is_file():
            report = regalloc_signature.compare(self.target_text, dump.read_text())
            result.update(report.to_dict(limit=12))
            result["_report"] = report
        else:
            result["compile_error"] = (attempt.compiler_stderr or "")[-400:]
        result["_diff"] = attempt.diff or ""
        for path in self.ws.glob(f"{name}*"):                 # keep /tmp small across hundreds of compiles
            path.unlink(missing_ok=True) if path.is_file() else shutil.rmtree(path, ignore_errors=True)
        self.cache[key] = result
        return result


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def probe(row: dict, sources: list[Path]) -> None:
    bench = Bench(row)
    try:
        base = bench.run(Path(row["source"]).read_text(), "baseline")
        print(json.dumps(public(base)), flush=True)
        for path in sources:
            result = bench.run(path.read_text(), str(path))
            if base.get("_report") and result.get("_report"):
                result["delta"] = regalloc_signature.delta(base["_report"], result["_report"])
            print(json.dumps(public(result)), flush=True)
    finally:
        bench.close()


def _key(result: dict):
    return tuple(result["gradient"]) if result.get("compiled") and "gradient" in result else (10**9, 0, 0)


def search(row: dict, out: Path, budget: int, beam: int, depth: int) -> dict:
    function = row["name"]
    log = (out / f"{function}.jsonl").open("w")
    bench = Bench(row)
    started = time.time()
    summary = {"function": function, "cohort": row.get("cohort"), "campaign_score": row.get("score")}
    try:
        source = Path(row["source"]).read_text()
        base = bench.run(source, "baseline")
        log.write(json.dumps({**public(base), "depth": 0}) + "\n")
        summary.update(baseline_gradient=base.get("gradient"), baseline_signatures=base.get("signatures"),
                       baseline_exact=base["exact"])
        if base["exact"] or not base.get("compiled"):
            summary["outcome"] = "baseline_exact" if base["exact"] else "baseline_does_not_compile"
            return summary
        frontier, best, compiles = [(base, source)], (base, source), 1
        for level in range(1, depth + 1):
            improving, sideways = [], []
            for parent, parent_source in frontier:
                for label, kind, variant in regalloc_mutations.variants(parent_source, function, parent.get("_diff", "")):
                    if compiles >= budget:
                        break
                    result = bench.run(variant, label)
                    if not result.get("cached"):
                        compiles += 1
                    row_out = {**public(result), "depth": level, "family": kind, "parent": parent["label"]}
                    if parent.get("_report") and result.get("_report"):
                        row_out["delta"] = regalloc_signature.delta(parent["_report"], result["_report"])
                    log.write(json.dumps(row_out) + "\n")
                    if result["exact"]:
                        (out / f"{function}.exact.c").write_text(variant)
                        summary.update(outcome="exact", exact_label=label, exact_depth=level, family=kind)
                        return summary
                    if not result.get("compiled") or result.get("cached"):
                        continue
                    if _key(result) < _key(parent):
                        improving.append((result, variant))
                    elif _key(result) == _key(parent):
                        # Plateau moves: one that changes WHICH registers differ first,
                        # then gradient-neutral ones. func_8005B49C needed a neutral
                        # step (inline a constant local) before the move that matched.
                        changed = result.get("signatures") != parent.get("signatures")
                        sideways.append((0 if changed else 1, len(sideways), result, variant))
                    if _key(result) < _key(best[0]):
                        best = (result, variant)
            chosen = sorted(improving, key=lambda item: _key(item[0]))[:beam]
            # A plateau move that changes WHICH registers differ can open a later fix.
            chosen += [(r, v) for _p, _i, r, v in sorted(sideways, key=lambda s: s[:2])][:max(0, beam - len(chosen))]
            if not chosen or compiles >= budget:
                break
            frontier = chosen
        (out / f"{function}.best.c").write_text(best[1])
        summary.update(outcome="improved" if _key(best[0]) < _key(base) else "no_gradient_progress",
                       best_gradient=best[0].get("gradient"), best_signatures=best[0].get("signatures"),
                       best_label=best[0]["label"])
        return summary
    except Exception as error:                                 # one function never stops the batch
        summary.update(outcome="error", error=f"{type(error).__name__}: {error}"[:400])
        return summary
    finally:
        summary["compiles"] = len(bench.cache)
        summary["seconds"] = round(time.time() - started, 1)
        log.close()
        bench.close()


def _search_one(args):
    row, out, budget, beam, depth = args
    return search(row, Path(out), budget, beam, depth)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("probe")
    one.add_argument("--cohort", type=Path, required=True)
    one.add_argument("--function", required=True)
    one.add_argument("--source", type=Path, action="append", default=[])
    many = sub.add_parser("search")
    many.add_argument("--cohort", type=Path, required=True)
    many.add_argument("--out", type=Path, required=True)
    many.add_argument("--only", action="append", default=[])
    many.add_argument("--cohort-name", default="regalloc_only")
    many.add_argument("--workers", type=int, default=2)
    many.add_argument("--budget", type=int, default=200)
    many.add_argument("--beam", type=int, default=3)
    many.add_argument("--depth", type=int, default=3)
    args = parser.parse_args()
    rows = {r["name"]: r for r in json.loads(args.cohort.read_text())["functions"]}
    if args.command == "probe":
        probe(rows[args.function], args.source)
        return
    args.out.mkdir(parents=True, exist_ok=True)
    done = set()
    if (args.out / "summary.jsonl").exists():
        done = {json.loads(line)["function"] for line in (args.out / "summary.jsonl").open()}
    selected = [r for name, r in sorted(rows.items())
                if (not args.only or name in args.only) and (args.only or r.get("cohort") == args.cohort_name)
                and name not in done]
    jobs = [(r, str(args.out), args.budget, args.beam, args.depth) for r in selected]
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for summary in pool.imap_unordered(_search_one, jobs):
            with (args.out / "summary.jsonl").open("a") as stream:
                stream.write(json.dumps(summary) + "\n")
            print(json.dumps({k: summary.get(k) for k in ("function", "outcome", "baseline_gradient", "best_gradient",
                                                          "family", "compiles", "seconds")}), flush=True)


if __name__ == "__main__":
    main()
