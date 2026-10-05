"""Stage amendment `20260915-enabling-roots` over the frozen campaign runtime.

    python eval/results/const-store-20260915/stage.py            # fresh stage
    python eval/results/const-store-20260915/stage.py --refresh  # re-apply onto an existing stage

Register search could not reach the AerialTrick family (30 pending siblings): the needed first edit
(`v = K; F = K;` -> `F = v`) leaves the gradient unchanged, so it never won a beam slot. This amendment adds
`regalloc_mutations.constant_store_locals` as an ENABLING edit and a phased `regalloc_search.search(enable=True)`:
the ordinary search keeps half the budget when enabling edits exist, then each edit no worse than the baseline is
searched from with the rest. Functions without such an edit are searched exactly as before. Only these edits are
staged; the main tree's typed_reread/truth_test generators, trace guidance and diverse beam are not.
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / "eval/results/resume-pipeline-20260908/code"
STAGE = OUT / "staged-code"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def replace_once(text, before, after, rel):
    if text.count(before) != 1:
        raise SystemExit(f"{rel}: anchor found {text.count(before)} times: {before[:80]!r}")
    return text.replace(before, after, 1)


if "--refresh" not in sys.argv:
    if STAGE.exists():
        raise SystemExit("staged-code exists; use --refresh or remove it")
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "results"))
    (STAGE / "eval/results").symlink_to(ROOT / "eval/results", target_is_directory=True)

manifest = {}


def record(rel):
    manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel)}


# 1. regalloc_mutations: the enabling generator, verbatim from the main tree, before `existing`.
rel = "solver/regalloc_mutations.py"
main = (ROOT / rel).read_text()


def top_level(start_marker):
    """From `start_marker` to the next top-level `def`/`class` (exclusive)."""
    start = main.index(start_marker)
    body = main.index("def constant_store_locals(") if start_marker.startswith("CONSTANT") else start
    found = re.search(r"\n(?=(?:def|class) )", main[body + 1:])
    return main[start:body + 1 + found.start()] if found else main[start:]


generator = top_level('CONSTANT = r"-?(?:0[xX]')
enabling = top_level("def enabling_variants(")
assert generator.count("\ndef ") + generator.startswith("def ") == 1 and "def constant_store_locals(" in generator
assert enabling.startswith("def enabling_variants(") and enabling.count("\ndef ") == 0
text = (LIVE / rel).read_text()
if "def constant_store_locals(" in text:
    raise SystemExit(f"{rel}: already carries constant_store_locals")
text = replace_once(text, "\ndef existing(source: str, diff: str):",
                    "\n" + generator.rstrip() + "\n\n\n" + enabling.rstrip() + "\n\n\ndef existing(source: str, diff: str):",
                    rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel)

# 2. regalloc_search: the phased search with enabling roots (no trace guidance, no diverse beam).
rel = "solver/regalloc_search.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "at most two other faults, with no model involved.\n\"\"\"\n",
    "at most two other faults, with no model involved.\n\n"
    "ENABLING ROOTS (`enable=True`). `regalloc_mutations.enabling_variants` are edits that leave the\n"
    "gradient unchanged but let another family fire, so they never win a beam slot. When the starting\n"
    "source has any, the ordinary search keeps half the budget, then each root no worse than the baseline is\n"
    "searched on its own with the rest (roots of phase 1's best source first). Every exact of the budget-300 runs in eval/results/regalloc-20260913\n"
    "needed at most 97 compiles. Motivating residual: the AerialTrick family (2026-09-15 progress census).\n"
    "\"\"\"\n", rel)
old_search = text[text.index("def search(function: str, source: str, compile_candidate, target_dump: str, *,"):]
new_search = '''def search(function: str, source: str, compile_candidate, target_dump: str, *,
           budget: int = 300, beam: int = 3, depth: int = 4, baseline: Compiled | None = None,
           enable: bool = False) -> Outcome:
    base = baseline or compile_candidate(source, "baseline")
    base_report = regalloc_signature.compare(target_dump, base.dump) if base.compiled and base.dump else None
    outcome = Outcome(base.exact, source, "baseline", _key(base_report) if base_report else None, None, 0 if baseline else 1)
    if base.exact or base_report is None:
        outcome.best_gradient = outcome.baseline_gradient
        return outcome
    best = (base_report, source, "baseline")

    def record_exact(variant, label):
        outcome.exact, outcome.best_source, outcome.best_label = True, variant, label
        outcome.best_gradient = (0, 0, 0)

    def run_beam(frontier, seen, cap):
        """Beam from `frontier` until `cap` compiles in total; True when a candidate is object-exact."""
        nonlocal best
        for level in range(1, depth + 1):
            improving, sideways = [], []
            for parent_report, parent, parent_source, parent_label in frontier:
                for label, kind, variant in regalloc_mutations.variants(parent_source, function, parent.diff):
                    if outcome.compiles >= cap:
                        break
                    if variant in seen:
                        continue
                    seen.add(variant)
                    result = compile_candidate(variant, label)
                    outcome.compiles += 1
                    row = {"depth": level, "family": kind, "label": label, "parent": parent_label,
                           "compiled": result.compiled, "exact": result.exact}
                    if result.exact:
                        outcome.log.append(row)
                        record_exact(variant, label)
                        return True
                    if not result.compiled or not result.dump:
                        outcome.log.append(row)
                        continue
                    report = regalloc_signature.compare(target_dump, result.dump)
                    row["gradient"] = list(report.gradient)
                    outcome.log.append(row)
                    if _key(report) < _key(parent_report):
                        improving.append((report, result, variant, label))
                    elif _key(report) == _key(parent_report):
                        changed = report.signatures != parent_report.signatures
                        sideways.append((0 if changed else 1, len(sideways), (report, result, variant, label)))
                    if _key(report) < _key(best[0]):
                        best = (report, variant, label)
            chosen = sorted(improving, key=lambda item: _key(item[0]))[:beam]
            chosen += [item for _p, _i, item in sorted(sideways, key=lambda s: s[:2])][:max(0, beam - len(chosen))]
            if not chosen or outcome.compiles >= cap:
                return False
            frontier = chosen
        return False

    roots = list(regalloc_mutations.enabling_variants(source, function)) if enable else []
    first_cap = budget - (budget // 2 if roots else 0)
    if run_beam([(base_report, base, source, "baseline")], {source}, first_cap):
        return outcome
    parents = [(best[1], best[0])] if roots and best[1] != source else []
    parents.append((source, base_report))
    staged, offered = [], {source}
    for parent_source, parent_report in parents:
        for label, kind, variant in (regalloc_mutations.enabling_variants(parent_source, function)
                                     if parent_source != source else roots):
            if variant not in offered:
                offered.add(variant)
                staged.append((label, kind, variant, parent_report))
    for label, kind, variant, parent_report in staged:
        if outcome.compiles >= budget:
            break
        result = compile_candidate(variant, label)
        outcome.compiles += 1
        row = {"depth": 0, "family": kind, "label": label, "parent": "baseline",
               "compiled": result.compiled, "exact": result.exact}
        if result.exact:
            outcome.log.append(row)
            record_exact(variant, label)
            return outcome
        if not result.compiled or not result.dump:
            outcome.log.append(row)
            continue
        report = regalloc_signature.compare(target_dump, result.dump)
        row["gradient"] = list(report.gradient)
        outcome.log.append(row)
        if _key(report) > _key(parent_report):
            continue
        if _key(report) < _key(best[0]):
            best = (report, variant, label)
        if run_beam([(report, result, variant, label)], {source, variant}, budget):
            return outcome
    outcome.best_source, outcome.best_label = best[1], best[2]
    outcome.best_gradient = _key(best[0])
    return outcome
'''
text = text.replace(old_search, new_search)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel)

# 3. agentrepair: the campaign's register search uses enabling roots.
rel = "eval/agentrepair.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "        return regalloc_search.search(function, source, compile_candidate, target.read_text(), budget=budget)\n",
    "        return regalloc_search.search(function, source, compile_candidate, target.read_text(), budget=budget,\n"
    "                                      enable=True)\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel)

# 4. Tests for the staged behaviour.
rel = "tests/test_enabling_roots_campaign.py"
shutil.copy2(OUT / "test_enabling_roots_campaign.py", STAGE / rel)
record(rel)

(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
print(json.dumps(manifest, indent=1))
