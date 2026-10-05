"""Run uopt's allocation trace over every C file of a built game tree and check the model.

    python3 -m eval.uopt_trace_census --tree ~/decomp/tools-src/sbk1-trace-gate --out DIR

The tree must already be built with the patched `uopt` (tools/ido-trace/README.md);
its objects are the reference each trace-mode compile is compared with. The build's
own compile commands come from `make -n -B`, run serially from the tree root with
`-Wo,-zdbug:5` and then `-Wo,-zdbug:6` inserted, because uopt writes `uoptlist` to
the working directory. Afterwards the original objects are restored.

Held-out sources: `--candidates ~/decomp/sbk1/nonmatchings --compiler ~/decomp/tools-src/ido-trace/cc`
traces one m2c-generated candidate per workspace (seeded sample, `--limit`) with the
workspace's recorded compile command, from the same tree so relative includes resolve.

Writes DIR/traces/<object>.l5|l6 (raw), DIR/functions.jsonl (one `uopt_trace.check`
report per procedure) and DIR/summary.json. A compile whose object differs from
the build's is recorded and its traces are not scored: a diagnostic that changes
the code it describes is not describing that code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from solver import uopt_calls, uopt_trace

CHECKS = ("colour_mismatch", "missing_record", "forbidden_not_at_time", "selection_miss", "order")


def compile_commands(tree: Path) -> list[tuple[str, str, list[str]]]:
    """(object, compile command, post-processing commands) for every game C object.

    The recipe does not stop at cc: `objcopy --remove-section .mdebug` (and, for some
    files, an ABI-bit patch) rewrites the object afterwards. Those are the following
    lines that name the same object; without them no compile reproduces the build.
    """
    dry = subprocess.run(["make", "-n", "-B"], cwd=tree, capture_output=True, text=True, check=True).stdout
    lines = [line.strip() for line in dry.replace("\\\n", " ").splitlines()]
    commands = []
    for i, line in enumerate(lines):
        if "ido-recomp/linux/cc" not in line:
            continue
        found = re.search(r"\s-o\s+(build/src/\S+\.o)\s", line)   # game C; asset commands cd elsewhere
        if not found:
            continue
        post = []
        for following in lines[i + 1:]:
            if following == ":":                                   # empty C_OBJ_POSTPROCESS
                continue
            if found.group(1) not in following or "ido-recomp/linux/cc" in following:
                break
            post.append(following)
        commands.append((found.group(1), line, post))
    return commands


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _ugen_flags(traces: Path, stem: str) -> str:
    """ugen's tree dump (`-Wc,-d,-e,PATH`), taken in its OWN compile.

    `-d` is not code-neutral: on SBK1 it renumbered ugen temporaries (t6 -> t8 in struct copies) in
    27 of 193 TUs. The dump's "Tree dump after Build" section is ugen's input, uopt's u-code, so its
    blocks and calls are still valid; the object from that compile is recorded and never trusted.
    """
    return f"-Wc,-d,-e,{(traces / f'{stem}.ugen').resolve()} "


def trace_all(tree: Path, out: Path, ugen: bool = False) -> list[dict]:
    traces = out / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    rows = []
    for obj, command, post in compile_commands(tree):
        target = tree / obj
        reference = _sha(target)
        saved = target.with_suffix(".o.census-orig")
        shutil.copy2(target, saved)
        row = {"object": obj, "reference_sha256": reference, "levels": {}}
        stem = obj.replace("/", "__")
        try:
            if ugen:
                dumped = command.replace(f" -o {obj} ", f" {_ugen_flags(traces, stem)}-o {obj} ", 1)
                run = subprocess.run(["bash", "-c", " && ".join([dumped, *post])], cwd=tree,
                                     capture_output=True, text=True)
                row["ugen"] = {"returncode": run.returncode, "dump": (traces / f"{stem}.ugen").is_file(),
                               "object_equal": _sha(target) == reference}
            for level in (5, 6):
                (tree / "uoptlist").unlink(missing_ok=True)
                traced = command.replace(f" -o {obj} ", f" -Wo,-zdbug:{level} -o {obj} ", 1)
                run = subprocess.run(["bash", "-c", " && ".join([traced, *post])], cwd=tree,
                                     capture_output=True, text=True)
                listing = tree / "uoptlist"
                has_trace = listing.is_file()
                if has_trace:
                    shutil.move(listing, traces / f"{stem}.l{level}")
                row["levels"][level] = {"returncode": run.returncode, "trace": has_trace,
                                        "object_equal": _sha(target) == reference,
                                        "stderr_tail": run.stderr[-300:] if run.returncode else ""}
        finally:
            shutil.move(saved, target)
        rows.append(row)
    return rows


M2C_BANNER = "This is a decompilation attempt by the m2c tool"


def candidate_sources(workspaces: Path, limit: int, seed: int) -> list[tuple[str, Path, list[str]]]:
    """One m2c-generated candidate per workspace, with the workspace's recorded compile command.

    The m2c banner excludes candidates copied from the reference decomp, so these sources
    are held out from the census the selection model was fitted on.
    """
    chosen = []
    for workspace in sorted(workspaces.iterdir()):
        recipes = sorted(workspace.glob(".compiler-*.json"))
        if not recipes:
            continue
        command = json.loads(recipes[0].read_text()).get("command")
        sources = [p for p in workspace.glob("*.c") if p.with_suffix(".o").is_file()
                   and ".source-lines" not in p.name and M2C_BANNER in p.read_text(errors="replace")]
        if command and sources:
            chosen.append((workspace.name, max(sources, key=lambda p: (p.stat().st_mtime, p.name)), command))
    random.Random(seed).shuffle(chosen)
    return sorted(chosen[:limit])


def trace_candidates(tree: Path, compiler: Path, workspaces: Path, out: Path, limit: int, seed: int,
                     ugen: bool = False) -> list[dict]:
    """Trace held-out candidate sources. No build object exists for them, so the check that
    tracing changes nothing is internal: plain, level-5 and level-6 objects must agree."""
    traces = out / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    rows = []
    scratch = tree / "census-candidate.o"
    for label, source, command in candidate_sources(workspaces, limit, seed):
        base = [str(compiler) if part == "tools/ido-recomp/linux/cc" else part for part in command]
        row = {"object": label, "source": str(source), "levels": {}}
        hashes = {}
        if ugen:
            scratch.unlink(missing_ok=True)
            run = subprocess.run(base + _ugen_flags(traces, label).split() + ["-o", str(scratch), str(source)],
                                 cwd=tree, capture_output=True, text=True)
            row["ugen"] = {"returncode": run.returncode, "dump": (traces / f"{label}.ugen").is_file()}
        for level in (None, 5, 6):
            (tree / "uoptlist").unlink(missing_ok=True)
            scratch.unlink(missing_ok=True)
            flags = [f"-Wo,-zdbug:{level}"] if level else []
            run = subprocess.run(base + flags + ["-o", str(scratch), str(source)], cwd=tree, capture_output=True,
                                 text=True)
            if run.returncode == 0:
                subprocess.run(["mips-linux-gnu-objcopy", "--remove-section", ".mdebug", str(scratch)], check=True)
            hashes[level] = _sha(scratch) if run.returncode == 0 else None
            listing = tree / "uoptlist"
            if level:
                has_trace = listing.is_file()
                if has_trace:
                    shutil.move(listing, traces / f"{label}.l{level}")
                row["levels"][level] = {"returncode": run.returncode, "trace": has_trace,
                                        "stderr_tail": run.stderr[-300:] if run.returncode else ""}
        for level in (5, 6):
            row["levels"][level]["object_equal"] = hashes[None] is not None and hashes[level] == hashes[None]
        rows.append(row)
    scratch.unlink(missing_ok=True)
    return rows


def _call_nodes(dump: Path, level5: str, totals: Counter) -> tuple[dict[str, list[int]], dict]:
    """procedure -> call-ending uopt nodes, for procedures whose flow graph maps onto the ugen dump."""
    if not dump.is_file():
        return {}, {}
    order = uopt_calls.flow_graph_order(level5)
    graphs = uopt_calls.flow_graphs(level5)
    procedures = uopt_calls.ugen_procedures(dump.read_text(errors="replace"))
    if len(order) != len(procedures):
        totals["call_map_tu_procedure_count_mismatch"] += 1
        return {}, {}
    mapped = {}
    for name, statements in zip(order, procedures):
        calls = uopt_calls.call_nodes(graphs[name], uopt_calls.blocks_of(statements))
        totals["call_map_procedures_mapped" if calls is not None else "call_map_procedures_unmapped"] += 1
        if calls is not None:
            mapped[name] = calls
    return mapped, uopt_calls.loop_depths(level5)


def _score_band(proc, calls: list[int], depths: dict[int, int], totals: Counter) -> list:
    misses = []
    for decision in proc.decisions:
        record = proc.ranges.get(decision.piece)
        if decision.outcome == "not_colored" or record is None or not 1 <= decision.color < 24:
            continue
        actual = uopt_trace.band_of(decision.color)
        predicted = uopt_calls.predict_band(record, calls, depths)
        totals["band_scored"] += 1
        totals["band_correct"] += predicted == actual
        # end to end: predicted band, then the selection model inside it (forbidden set still observed)
        totals["colour_with_predicted_band_correct"] += uopt_trace.select_colour(record, predicted) == decision.color
        if predicted != actual:
            totals[f"band_miss_actual_{actual}"] += 1
            misses.append((decision.piece, decision.color, predicted))
    return misses


def score(out: Path, rows: list[dict]) -> dict:
    totals = Counter()
    examples: dict[str, list] = {k: [] for k in CHECKS}
    with (out / "functions.jsonl").open("w") as log:
        for row in rows:
            levels = row["levels"]
            if not all(levels[l]["object_equal"] and levels[l]["returncode"] == 0 for l in (5, 6)):
                totals["tus_object_changed_or_failed"] += 1
                continue
            if not (levels[5]["trace"] and levels[6]["trace"]):
                totals["tus_without_trace"] += 1     # e.g. files compiled without uopt
                continue
            stem = row["object"].replace("/", "__")
            l5 = (out / "traces" / f"{stem}.l5").read_text(errors="replace")
            l6 = (out / "traces" / f"{stem}.l6").read_text(errors="replace")
            level5, level6 = uopt_trace.parse_level5(l5), uopt_trace.parse_level6(l6)
            unpaired = level5.keys() ^ level6.keys()
            totals["tus_traced"] += 1
            totals["procedures_unpaired"] += len(unpaired)
            calls_by_procedure, depths = _call_nodes(out / "traces" / f"{stem}.ugen", l5, totals)
            for name, proc in uopt_trace.join(l5, l6).items():
                report = uopt_trace.check(proc)
                report["object"] = row["object"]
                if name in calls_by_procedure:
                    report["band_miss"] = _score_band(proc, calls_by_procedure[name], depths.get(name, {}), totals)
                log.write(json.dumps(report) + "\n")
                totals["procedures"] += 1
                totals["decisions"] += report["decisions"]
                totals["nonfinite_adjsave"] += len(report["nonfinite_adjsave"])
                for piece, colour, _predicted in report["selection_miss"]:
                    totals[f"selection_miss_{uopt_trace.band_of(colour)}"] += 1
                totals["procedures_consistent"] += report["consistent"]
                for key in CHECKS:
                    if report[key]:
                        totals[f"procedures_with_{key}"] += 1
                        totals[f"{key}_items"] += len(report[key])
                        if len(examples[key]) < 10:
                            examples[key].append({"function": name, "object": row["object"], "items": report[key][:5]})
    return {"totals": dict(totals), "examples": examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rescore", action="store_true", help="score DIR's existing traces without compiling")
    parser.add_argument("--candidates", type=Path, help="trace held-out m2c candidates from these workspaces")
    parser.add_argument("--compiler", type=Path, help="patched cc used for --candidates")
    parser.add_argument("--limit", type=int, default=800)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--ugen", action="store_true", help="also keep ugen's tree dump (DIR/traces/*.ugen)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.candidates and not args.rescore:
        rows = trace_candidates(args.tree.expanduser(), args.compiler.expanduser(), args.candidates.expanduser(),
                                args.out, args.limit, args.seed, args.ugen)
        (args.out / "compiles.json").write_text(json.dumps(rows, indent=1))
    elif args.rescore:
        rows = json.loads((args.out / "compiles.json").read_text())
        for row in rows:                                     # JSON turned the level keys into strings
            row["levels"] = {int(level): value for level, value in row["levels"].items()}
    else:
        rows = trace_all(args.tree.expanduser(), args.out, args.ugen)
        (args.out / "compiles.json").write_text(json.dumps(rows, indent=1))
    summary = {"tree": str(args.tree), "compiles": len(rows), **score(args.out, rows)}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary["totals"], indent=1))


if __name__ == "__main__":
    main()
