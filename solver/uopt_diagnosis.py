"""Why a candidate's registers differ from the target's, decision by decision, from uopt's own trace.

Given the target's and the candidate's normalized object dumps, the candidate's uopt trace
(`-zdbug:5`, `-zdbug:6`) and ugen tree dump, `diagnose` attributes every aligned register operand
to a candidate live range (`solver.uopt_attribution`) and votes the target's register for that
range. Each range with a vote is classified:

    ok         every vote agrees with the colour uopt chose
    ugen_temp  the target holds the value in a register uopt never colours (t7-t9, at): the range
               should not exist, e.g. a local or a forwarded store made a shared value
    blocked    the target's colour was taken by a neighbour coloured earlier (blockers listed)
    selection  the target's colour was free and the selection rule chose another
    split      the target agrees with uopt's colour in some places and not others: range
               structure or instruction order differs, not a colouring decision
    (votes that all disagree are classified by the majority register, even if they vary)

`first` is the earliest non-ok range in colouring order: later decisions depend on it.

Measured 2026-09-14 on 28 register-only residuals from the regalloc transfer cohort: all 28
attributed; first non-ok: 16 ugen_temp, 3 selection, 8 split, 1 none. Following the diagnosis
by hand made 6 of them object-exact (updateCharacterSelectRosterIcons, five SlideIn popups),
which became the `truth_test` and `typed_reread` generators.

This is diagnosis, not an oracle: exactness is only ever the object comparison.
"""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from solver import regalloc_signature, uopt_attribution, uopt_trace

CLASS_FAMILIES = {
    # generator families to try first for each first-decision class (solver.regalloc_mutations kinds)
    "ugen_temp": ("typed_reread", "field_local", "readonly_field_local", "single_use", "result_local",
                  "store_value_local", "self_update", "compound_assign"),
    "selection": ("truth_test", "local_type", "decl_order", "stmt_move", "const_inline"),
    "blocked": ("truth_test", "stmt_move", "decl_order", "single_use", "local_type", "const_inline"),
    "split": ("stmt_move", "stmt_order", "commutative", "decl_order"),
}


def diagnose(target_dump: str, candidate_dump: str, level5: str, level6: str, ugen_dump: str,
             function: str) -> dict:
    report: dict = {"function": function}
    try:
        attribution = uopt_attribution.attribute(candidate_dump, level5, level6, ugen_dump, function)
    except uopt_attribution.Declined as reason:
        report["declined"] = str(reason)
        return report
    proc = uopt_trace.join(level5, level6)[function]
    target = regalloc_signature.parse(uopt_attribution.strip_padding(target_dump))
    candidate = regalloc_signature.parse(uopt_attribution.strip_padding(candidate_dump))
    if len(candidate) != len(attribution.instructions):
        report["declined"] = "instruction count differs between attribution and signature parse"
        return report
    matcher = difflib.SequenceMatcher(a=[i.shape() for i in target], b=[i.shape() for i in candidate],
                                      autojunk=False)
    votes: dict[int, Counter] = defaultdict(Counter)
    unattributed = Counter()
    non_register = 0
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op != "equal":
            non_register += max(a1 - a0, b1 - b0)
            continue
        for k in range(a1 - a0):
            wanted_by_position = dict(target[a0 + k].registers())
            seen_by_signature = dict(candidate[b0 + k].registers())
            for position, register, lr in attribution.operands.get(b0 + k, []):
                # regalloc_signature does not read a base behind `%lo(sym)(s1)`; vote only where both
                # sides were parsed as registers, or an unparsed target operand votes "None".
                if position not in seen_by_signature or position not in wanted_by_position:
                    continue
                wanted = wanted_by_position[position]
                if lr is None:
                    if wanted != register:
                        unattributed[f"{register}->{wanted}"] += 1
                    continue
                votes[lr][wanted] += 1
    order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
    ranges = []
    for lr, counter in votes.items():
        record = proc.ranges[lr]
        actual = uopt_attribution.REGISTER_NAMES[uopt_attribution.colour_register(record.color)]
        entry = {"lr": lr, "node": record.node, "kind": record.kind, "offset": record.offset,
                 "constant": record.constant, "colour": record.color, "actual": actual,
                 "votes": dict(counter), "adjsave": record.adjsave,
                 "order": order.index(lr) if lr in order else None}
        desired = counter.most_common(1)[0][0]
        if set(counter) == {actual}:
            entry["class"] = "ok"
        elif actual in counter:
            entry["class"] = "split"          # right in some places, other registers elsewhere
        else:
            colour = uopt_attribution.REGISTER_COLOUR.get(uopt_attribution.REGISTER_NUMBER.get(desired))
            entry["desired"] = desired
            if colour is None:
                entry["class"] = "ugen_temp"
            elif colour in (record.forbidden or frozenset()):
                entry["class"] = "blocked"
                entry["blockers"] = [n for n in record.interferes
                                     if n in proc.ranges and proc.ranges[n].color == colour and n in order
                                     and entry["order"] is not None and order.index(n) < entry["order"]]
            else:
                entry["class"] = "selection"
                band = uopt_trace.band_of(record.color)
                entry["selected_by_model"] = uopt_trace.select_colour(record, band) if band else None
        ranges.append(entry)
    ranges.sort(key=lambda e: (e["order"] is None, e["order"] if e["order"] is not None else 0))
    wrong = [e for e in ranges if e["class"] != "ok"]
    report.update(non_register=non_register, ranges=ranges, unattributed=dict(unattributed),
                  wrong_ranges=len(wrong), first=wrong[0] if wrong else None,
                  elided_jumps=attribution.elided_jumps)
    return report


def preferred_families(report: dict | None) -> tuple[str, ...]:
    if not report or not report.get("first"):
        return ()
    return CLASS_FAMILIES.get(report["first"]["class"], ())


def _recipe_command(workspace: Path, repo: Path) -> list[str] | None:
    """The workspace's compile command: its recipe manifest, else resolved from its target identity
    exactly as `solver.compiler_recipe.prepare` does (isolated campaign workspaces start without one)."""
    manifests = [p for p in sorted(workspace.glob(".compiler-*.json")) if not p.name.endswith("-target.json")]
    if manifests:
        return json.loads(manifests[0].read_text()).get("command")
    identity = workspace / ".compiler-target.json"
    if not identity.is_file():
        return None
    try:
        from solver import compiler_recipe
        return compiler_recipe.resolve(repo, json.loads(identity.read_text())["target"])["command"]
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        return None


def traced_compile(workspace: Path, repo: Path, source: str, trace_cc: Path, function: str) -> dict | None:
    """Compile `source` with the workspace's recorded recipe through the tracing toolchain.

    Three compiles, each from `repo` so relative includes resolve: `-zdbug:5`, `-zdbug:6`, and
    ugen's tree dump in its own compile (`-d` renumbers ugen temporaries, so that object is
    discarded). uopt writes `uoptlist` to the working directory; callers must not share `repo`
    between concurrent traced compiles. Returns None if any compile fails.
    """
    if not trace_cc.is_file():
        return None
    command = _recipe_command(workspace, repo)
    if not command:
        return None
    base = [str(trace_cc) if part.endswith("ido-recomp/linux/cc") else part for part in command]
    scratch = Path(tempfile.mkdtemp(prefix="uopt-trace-"))
    try:
        raw = scratch / "raw.c"
        raw.write_text(source)
        path = scratch / "candidate.c"
        textconv, charmap = repo / "tools/textconv.py", repo / "tools/charmap.txt"
        if textconv.is_file() and charmap.is_file():            # the workspace helper converts text first
            converted = subprocess.run(["python3", str(textconv), str(charmap), str(raw), str(path)],
                                       capture_output=True, text=True, timeout=60)
            if converted.returncode:
                return None
        else:
            shutil.copy(raw, path)
        texts = {}
        listing = repo / "uoptlist"
        for label, flags in (("ugen", [f"-Wc,-d,-e,{scratch / 'tree.ugen'}"]), ("level6", ["-Wo,-zdbug:6"]),
                             ("level5", ["-Wo,-zdbug:5"])):
            listing.unlink(missing_ok=True)
            run = subprocess.run(base + flags + ["-o", str(scratch / "candidate.o"), str(path)], cwd=repo,
                                 capture_output=True, text=True, timeout=120)
            if run.returncode:
                return None
            if label == "ugen":
                texts[label] = (scratch / "tree.ugen").read_text(errors="replace")
            else:
                if not listing.is_file():
                    return None
                texts[label] = listing.read_text(errors="replace")
        listing.unlink(missing_ok=True)
        return texts
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
