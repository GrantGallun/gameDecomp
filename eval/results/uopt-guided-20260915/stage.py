"""Stage amendment `20260915-uopt-guided-regalloc` over the frozen campaign runtime. Stages only; never deploys.

    python eval/results/uopt-guided-20260915/stage.py            # fresh stage
    python eval/results/uopt-guided-20260915/stage.py --refresh  # re-apply onto an existing stage

Adds to the live `regalloc_search` profile:
  * two generator families from the uopt trace diagnosis (`typed_reread`, `truth_test`);
  * trace guidance: when the profile names a tracing toolchain whose uopt hash matches the pin,
    the search diagnoses the baseline and each frontier member and tries the preferred families
    first. A missing or mismatched toolchain silently leaves the search unguided (as before).
Acceptance, certificates, the ratchet, integration, scheduling and budgets are unchanged.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / "eval/results/resume-pipeline-20260908/code"
STAGE = OUT / "staged-code"
TRACE_CC = "/home/grant/decomp/tools-src/ido-trace/cc"
TRACE_UOPT_SHA256 = "2776d48b1654ff6a74498311b376ad4959eda875c710ae97f0fc98f9a09dff15"   # gated: tools/ido-trace/README.md
LIVE_MUTATIONS_SHA256 = "a5d43c79857a"                      # prefix; main tree equalled live before these edits


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


def record(rel, scope):
    manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel), "scope": scope}


# 1. New modules and their tests, verbatim from the main tree.
for rel, scope in (("solver/uopt_trace.py", "uopt trace parser and colour-selection model"),
                   ("solver/uopt_calls.py", "uopt flow graph <-> ugen u-code match, calls, band"),
                   ("solver/uopt_attribution.py", "assembly operand -> uopt live range"),
                   ("solver/uopt_diagnosis.py", "register-difference diagnosis and traced compile"),
                   ("tests/test_uopt_trace.py", "parser/model tests"),
                   ("tests/test_uopt_calls.py", "call-map tests"),
                   ("tests/test_uopt_diagnosis.py", "attribution/diagnosis tests on real traces")):
    (STAGE / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / rel, STAGE / rel)
    record(rel, scope)
fixtures = STAGE / "tests/fixtures/uopt"
if fixtures.exists():
    shutil.rmtree(fixtures)
shutil.copytree(ROOT / "tests/fixtures/uopt", fixtures)
manifest["tests/fixtures/uopt/"] = {"files": len(list(fixtures.iterdir())), "scope": "real trace fixtures"}

# 2. Generators: the main tree equalled live before these edits, so copy it (verified).
rel = "solver/regalloc_mutations.py"
if not sha(LIVE / rel).startswith(LIVE_MUTATIONS_SHA256):
    raise SystemExit(f"{rel}: live changed since staging was prepared; re-derive the edit")
shutil.copy2(ROOT / rel, STAGE / rel)
record(rel, "typed_reread and truth_test families; variants(prefer=...)")

# 3. Search: main tree edits, with the live tie rule kept.
rel = "solver/regalloc_search.py"
text = (ROOT / rel).read_text()
text = replace_once(text,
    '    return max(faults, key=lambda k: faults[k]) == "register_allocation"\n',
    '    # Ties go to register allocation: equal "structural" counts are usually its reorders.\n'
    '    return faults["register_allocation"] >= max(faults.values())\n', rel)
live_gate = (LIVE / rel).read_text()
assert 'return faults["register_allocation"] >= max(faults.values())' in live_gate
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "optional trace guidance (trace, trace_budget); live tie rule preserved")

# 4. Tests appended to the live test files (the live copies carry tests the main tree lacks).
for rel, marker in (("tests/test_regalloc_mutations.py", "\n\nGUIDED = "), ("tests/test_regalloc_search.py", "\n\nGUIDED = ")):
    main = (ROOT / rel).read_text()
    if main.count(marker) != 1:
        raise SystemExit(f"{rel}: marker not found once in the main tree")
    live = (LIVE / rel).read_text()
    if marker in live:
        raise SystemExit(f"{rel}: live already contains the guided tests")
    text = live.rstrip("\n") + "\n" + main[main.index(marker):]
    compile(text, rel, "exec")
    (STAGE / rel).write_text(text)
    record(rel, "fire tests for new families, prefer ordering, trace guidance")

# 5. agentrepair: pass an optional pinned tracing toolchain into the register search.
rel = "eval/agentrepair.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "def _regalloc_search(repo, db, ws, function, source, root_packet, budget):\n",
    "def _regalloc_trace(repo, ws, function, target_text, trace_cc, trace_uopt_sha256):\n"
    "    \"\"\"A trace callback for regalloc_search, or None unless the pinned tracing toolchain is present.\"\"\"\n"
    "    if not trace_cc or not trace_uopt_sha256:\n"
    "        return None\n"
    "    import hashlib\n"
    "    uopt = Path(trace_cc).with_name('uopt')\n"
    "    if not uopt.is_file() or hashlib.sha256(uopt.read_bytes()).hexdigest() != trace_uopt_sha256:\n"
    "        return None\n"
    "    from solver import uopt_diagnosis\n"
    "\n"
    "    def trace(candidate, label, compiled):\n"
    "        if not compiled.compiled or not compiled.dump:\n"
    "            return None\n"
    "        texts = uopt_diagnosis.traced_compile(ws, repo, candidate, Path(trace_cc), function)\n"
    "        if texts is None:\n"
    "            return None\n"
    "        return uopt_diagnosis.diagnose(target_text, compiled.dump, texts['level5'], texts['level6'],\n"
    "                                       texts['ugen'], function)\n"
    "    return trace\n"
    "\n"
    "\n"
    "def _regalloc_search(repo, db, ws, function, source, root_packet, budget, trace_cc=None, trace_uopt_sha256=None):\n",
    rel)
text = replace_once(text,
    "        return regalloc_search.search(function, source, compile_candidate, target.read_text(), budget=budget)\n",
    "        target_text = target.read_text()\n"
    "        trace = _regalloc_trace(repo, ws, function, target_text, trace_cc, trace_uopt_sha256)\n"
    "        return regalloc_search.search(function, source, compile_candidate, target_text, budget=budget, trace=trace)\n",
    rel)
text = replace_once(text,
    "        regalloc_budget: int = 0, frontend_fixits: bool = False,",
    "        regalloc_budget: int = 0, regalloc_trace_cc: str | None = None,\n"
    "        regalloc_trace_uopt_sha256: str | None = None, frontend_fixits: bool = False,", rel)
text = replace_once(text,
    '        "regalloc_budget": regalloc_budget,\n',
    '        "regalloc_budget": regalloc_budget,\n'
    '        "regalloc_trace_cc": regalloc_trace_cc,\n'
    '        "regalloc_trace_uopt_sha256": regalloc_trace_uopt_sha256,\n', rel)
text = replace_once(text,
    "        searched = _regalloc_search(repo, db, ws, function, source, root_packet, regalloc_budget)\n",
    "        searched = _regalloc_search(repo, db, ws, function, source, root_packet, regalloc_budget,\n"
    "                                    regalloc_trace_cc, regalloc_trace_uopt_sha256)\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "optional pinned trace toolchain for the register search")

# 6. completion_campaign: the profile names the pinned toolchain and passes it through.
rel = "eval/completion_campaign.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    '    {"name": "regalloc_search", "deterministic_budget": 0, "regalloc_budget": 300, "model": False},\n',
    '    {"name": "regalloc_search", "deterministic_budget": 0, "regalloc_budget": 300, "model": False,\n'
    f'     "regalloc_trace_cc": "{TRACE_CC}",\n'
    f'     "regalloc_trace_uopt_sha256": "{TRACE_UOPT_SHA256}"}},\n', rel)
text = replace_once(text,
    '        regalloc_budget=profile.get("regalloc_budget", 0),\n',
    '        regalloc_budget=profile.get("regalloc_budget", 0),\n'
    '        regalloc_trace_cc=profile.get("regalloc_trace_cc"),\n'
    '        regalloc_trace_uopt_sha256=profile.get("regalloc_trace_uopt_sha256"),\n', rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "regalloc_search profile names the pinned tracing toolchain")

# 7. Wiring test.
rel = "tests/test_uopt_guided_campaign.py"
shutil.copy2(OUT / "test_uopt_guided_campaign.py", STAGE / rel)
record(rel, "profile -> agentrepair -> search trace wiring; pin mismatch leaves search unguided")

(OUT / "staged-manifest.json").write_text(json.dumps({
    "amendment": "20260915-uopt-guided-regalloc", "live": str(LIVE), "stage": str(STAGE),
    "trace_toolchain": {"cc": TRACE_CC, "uopt_sha256": TRACE_UOPT_SHA256},
    "files": manifest}, indent=1) + "\n")
print(json.dumps(manifest, indent=1))
