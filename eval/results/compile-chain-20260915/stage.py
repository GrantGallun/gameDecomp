"""Stage amendment `20260915-compile-chain` over the frozen campaign runtime.

    python eval/results/compile-chain-20260915/stage.py            # fresh stage
    python eval/results/compile-chain-20260915/stage.py --refresh  # re-apply onto an existing stage

Non-compiling functions got stuck at their first compiler error: placeholder recovery fixed that
error in 9/9 cases but only 2 then compiled, and a still-non-compiling child scores 0 like its
parent, so it was never kept and every later profile restarted at the same line. This amendment
chains fixes inside the same agentrepair work item: after compile recovery, if nothing compiles,
`solver.compile_chain` keeps applying the next fix (placeholders, then identifiers IDO reports as
undefined) to the most advanced child, re-scoring each through the normal logged path.
Acceptance, certificates, the ratchet, integration, scheduling, profiles and budgets are unchanged.
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


# 1. New modules and tests, verbatim from the main tree.
for rel in ("solver/compile_chain.py", "solver/undeclared_identifiers.py", "tests/test_compile_chain.py"):
    shutil.copy2(ROOT / rel, STAGE / rel)
    record(rel)

# 2. agentrepair: chain fixes after compile recovery when nothing compiles.
rel = "eval/agentrepair.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "                initial_states.append(modelrepair.CandidateState(candidate,att,\n"
    "                    ws/(tag+'.o') if att.compiled else None,\n"
    "                    recovery_seed.labels+(label,),recovery_seed.kinds+('compile-recovery',)))\n"
    "            seed_state=modelrepair._frontier([seed_state,*initial_states],1)[0]\n",
    "                initial_states.append(modelrepair.CandidateState(candidate,att,\n"
    "                    ws/(tag+'.o') if att.compiled else None,\n"
    "                    recovery_seed.labels+(label,),recovery_seed.kinds+('compile-recovery',)))\n"
    "            if not any(s.attempt.compiled for s in [seed_state,*initial_states]):\n"
    "                _compile_chain(repo,ws,function,source,recovery_seed,initial_states,compiled_names,conn,\n"
    "                               run_id,config,context_reports)\n"
    "            seed_state=modelrepair._frontier([seed_state,*initial_states],1)[0]\n", rel)
text = replace_once(text,
    "def run(*, repo: Path, db: Path, function: str, source: str,\n",
    "def _compile_chain(repo, ws, function, source, recovery_seed, initial_states, compiled_names, conn,\n"
    "                   run_id, config, context_reports):\n"
    "    \"\"\"Chain placeholder/undeclared-identifier fixes from the most advanced non-compiling state.\"\"\"\n"
    "    from solver import compile_chain, placeholder_declarations\n"
    "    pool = [recovery_seed, *(s for s in initial_states if not s.attempt.compiled)]\n"
    "    base_lines = source.count('\\n')\n"
    "    parent = max(pool, key=lambda s: compile_chain.progress(s.attempt, s.source, base_lines))\n"
    "    objects = {}\n"
    "\n"
    "    def score(label, code):\n"
    "        tag = f'{function}_chain_{time.time_ns()}'\n"
    "        tag, att = compiled_names.score(tag, code, conn=conn, func=function,\n"
    "            strategy='agentrepair-compile-chain:' + label, model='zero-model', run_id=run_id,\n"
    "            parent_attempt_id=parent.attempt.receipt_id, relation='compile-chain', action=label,\n"
    "            run_kind='agent-repair', run_config=config)\n"
    "        objects[code] = ws / (tag + '.o') if att.compiled else None\n"
    "        return att\n"
    "\n"
    "    try:\n"
    "        headers = placeholder_declarations.header_names(repo, parent.source)\n"
    "        chained, log = compile_chain.chain(function, 'chain', parent.source, parent.attempt, score, headers=headers)\n"
    "    except (OSError, ValueError, subprocess.SubprocessError) as exc:\n"
    "        chained, log = [], [{'stage': 'compile-chain', 'status': 'error', 'reason': str(exc)}]\n"
    "    context_reports.append({'kind': 'compile-chain', 'parent_attempt_id': parent.attempt.receipt_id,\n"
    "                            'compiled': any(a.compiled for _l, _c, a in chained), 'log': log})\n"
    "    seen = {s.source for s in initial_states} | {source}\n"
    "    for label, code, att in chained:\n"
    "        if code not in seen:\n"
    "            seen.add(code)\n"
    "            initial_states.append(modelrepair.CandidateState(code, att, objects.get(code),\n"
    "                parent.labels + (label,), parent.kinds + ('compile-chain',)))\n"
    "\n"
    "\n"
    "def run(*, repo: Path, db: Path, function: str, source: str,\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel)

# 3. Wiring test.
rel = "tests/test_compile_chain_campaign.py"
shutil.copy2(OUT / "test_compile_chain_campaign.py", STAGE / rel)
record(rel)

(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
print(json.dumps(manifest, indent=1))
