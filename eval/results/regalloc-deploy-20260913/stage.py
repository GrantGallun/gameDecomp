"""Stage the register-allocation search amendment over the frozen campaign runtime.

    python eval/results/regalloc-deploy-20260913/stage.py            # fresh stage
    python eval/results/regalloc-deploy-20260913/stage.py --refresh  # re-apply edits onto an existing stage

Adds a zero-model `regalloc_search` profile. Register-dominant pending nodes run
`solver.regalloc_search` (mutation generators ranked by the register gradient)
inside the normal work item. Search compiles go to a scratch in-memory attempt
log; only the best candidate, and only when it improves, is re-scored into the
campaign database. Acceptance, certificates, the ratchet and integration are
unchanged. Nodes with at most two non-register faults are scheduled ahead of
their visit band, because that is the measured high-yield set.
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
    shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "results"))
    (STAGE / "eval/results").symlink_to(ROOT / "eval/results", target_is_directory=True)

manifest = {}


def record(rel, scope):
    manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel), "scope": scope}


# 1. New modules, copied verbatim from the main tree.
for rel, scope in (("solver/regalloc_signature.py", "register-difference signatures and gradient"),
                   ("solver/regalloc_mutations.py", "tested source-mutation generators"),
                   ("solver/regalloc_search.py", "gradient beam search over the generators"),
                   ("tests/test_regalloc_signature.py", "fire tests"),
                   ("tests/test_regalloc_mutations.py", "fire tests"),
                   ("tests/test_regalloc_search.py", "search tests")):
    shutil.copy2(ROOT / rel, STAGE / rel)
    record(rel, scope)

# 2. agentrepair: optional zero-model register search before the retained frontier.
rel = "eval/agentrepair.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "        runtime_captures: tuple[dict, ...] = (), memory_context: dict | None = None) -> dict:\n",
    "        runtime_captures: tuple[dict, ...] = (), memory_context: dict | None = None,\n"
    "        regalloc_budget: int = 0) -> dict:\n", rel)
text = replace_once(text,
    '        "constraint_plan_budget":8 if resilient else 0,\n',
    '        "constraint_plan_budget":8 if resilient else 0,\n'
    '        "regalloc_budget": regalloc_budget,\n', rel)
text = replace_once(text,
    "    # Resilient handoff reserves room for semantic/byte champions plus intact\n",
    "    if regalloc_budget > 0 and root.compiled and not root.exact:\n"
    "        searched = _regalloc_search(repo, db, ws, function, source, root_packet, regalloc_budget)\n"
    "        if searched is not None:\n"
    "            context_reports.append({'kind': 'regalloc-search', **searched.summary(),\n"
    "                                    'log_tail': searched.log[-20:]})\n"
    "            if searched.improved and searched.best_source != source:\n"
    "                tag = f\"{function}_agentrepair_regalloc_{time.time_ns()}\"\n"
    "                tag, verified = compiled_names.score(tag, searched.best_source, conn=conn, func=function,\n"
    "                    strategy=\"agentrepair-regalloc-search\", model=\"zero-model\", run_id=run_id,\n"
    "                    parent_attempt_id=root.receipt_id, relation=\"regalloc-search\",\n"
    "                    action=searched.best_label, extra={\"regalloc_search\": searched.summary()},\n"
    "                    run_kind=\"agent-repair\", run_config=config)\n"
    "                initial_states.append(modelrepair.CandidateState(\n"
    "                    searched.best_source, verified, ws / f\"{tag}.o\" if verified.compiled else None,\n"
    "                    (\"register search: \" + searched.best_label,), (\"regalloc\",)))\n"
    "    # Resilient handoff reserves room for semantic/byte champions plus intact\n", rel)
text = replace_once(text,
    "def run(*, repo: Path, db: Path, function: str, source: str,\n",
    "def _regalloc_search(repo, db, ws, function, source, root_packet, budget):\n"
    "    \"\"\"Zero-model register search. Search compiles use a scratch attempt log, never the campaign DB.\"\"\"\n"
    "    from solver import regalloc_search\n"
    "    if not regalloc_search.register_dominant(getattr(root_packet, 'faults', None) or {}):\n"
    "        return None\n"
    "    target = ws / 'target_object_dump_normalized.s'\n"
    "    if not target.is_file():\n"
    "        return None\n"
    "    scratch = sqlite3.connect(':memory:')\n"
    "    scratch.executescript((Path(__file__).resolve().parents[1] / 'kb/schema.sql').read_text())\n"
    "    with sqlite3.connect(f'file:{Path(db).as_posix()}?mode=ro', uri=True) as source_db:\n"
    "        row = source_db.execute('SELECT f.addr,f.size,t.name FROM functions f JOIN tus t ON t.id=f.tu_id '\n"
    "                                'WHERE f.name=?', (function,)).fetchone()\n"
    "    if row is None:\n"
    "        return None\n"
    "    scratch.execute('INSERT INTO tus(id,name) VALUES(1,?)', (row[2],))\n"
    "    scratch.execute('INSERT INTO functions(addr,name,size,tu_id) VALUES(?,?,?,1)', (row[0], function, row[1]))\n"
    "\n"
    "    def compile_candidate(candidate, label):\n"
    "        tag = f'{function}_regalloc_{time.time_ns()}'\n"
    "        attempt = workspace.score(ws, repo, tag, candidate, conn=scratch, func=function,\n"
    "                                  strategy='regalloc-search-probe', model='zero-model')\n"
    "        dump = ws / f'{tag}_object_dump_normalized.s'\n"
    "        text = dump.read_text() if attempt.compiled and dump.is_file() else None\n"
    "        for path in ws.glob(tag + '*'):\n"
    "            if path.is_file():\n"
    "                path.unlink(missing_ok=True)\n"
    "        return regalloc_search.Compiled(bool(attempt.compiled), bool(attempt.exact), text, attempt.diff or '')\n"
    "\n"
    "    try:\n"
    "        return regalloc_search.search(function, source, compile_candidate, target.read_text(), budget=budget)\n"
    "    finally:\n"
    "        scratch.close()\n"
    "\n"
    "\n"
    "def run(*, repo: Path, db: Path, function: str, source: str,\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "optional zero-model register search step; results enter as ordinary initial states")

# 3. completion_campaign: the profile and its budget.
rel = "eval/completion_campaign.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    'PROFILES = (\n    {"name": "local_rewrites",',
    'PROFILES = (\n    {"name": "regalloc_search", "deterministic_budget": 0, "regalloc_budget": 300, "model": False},\n'
    '    {"name": "local_rewrites",', rel)
text = replace_once(text,
    '        deterministic_budget=profile["deterministic_budget"],\n',
    '        deterministic_budget=profile["deterministic_budget"],\n'
    '        regalloc_budget=profile.get("regalloc_budget", 0),\n', rel)
text = replace_once(text,
    '        if (node["source_sha256"], profile["name"]) not in used:\n            return profile\n',
    '        if profile["name"] == "regalloc_search" and not _register_dominant(node):\n            continue\n'
    '        if (node["source_sha256"], profile["name"]) not in used:\n            return profile\n', rel)
text = replace_once(text,
    "def next_profile(node: dict, model_calls: int) -> dict | None:\n",
    "def _register_dominant(node: dict) -> bool:\n"
    "    from solver import regalloc_search\n"
    "    return regalloc_search.register_dominant((node.get('residual') or {}).get('faults') or {})\n"
    "\n\n"
    "def next_profile(node: dict, model_calls: int) -> dict | None:\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "regalloc_search profile (register-dominant nodes only) and budget pass-through")

# 4. repair_queue: gate in byte/environment lanes and schedule the measured high-yield set first.
rel = "solver/repair_queue.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "        profiles = [p for p in legacy_profiles if not p.get('type_transaction')]\n",
    "        profiles = [p for p in legacy_profiles if not p.get('type_transaction')]\n"
    "        from solver import regalloc_search\n"
    "        dominant = regalloc_search.register_dominant((node.get('residual') or {}).get('faults') or {})\n"
    "        profiles = [p for p in profiles if p['name'] != 'regalloc_search' or dominant]\n", rel)
text = replace_once(text,
    "        priority = (len(node.get('jobs', [])) // 2, order[phase], depth, -leverage, locality,\n",
    "        band = len(node.get('jobs', [])) // 2\n"
    "        if profile['name'] == 'regalloc_search':\n"
    "            from solver import regalloc_search\n"
    "            # Measured 2026-09-13: 73/78 register-only and 63/145 with <=2 other faults went\n"
    "            # object-exact offline. Run that set ahead of its visit band; it is CPU-only.\n"
    "            if regalloc_search.register_dominant((node.get('residual') or {}).get('faults') or {}, max_other=2):\n"
    "                band = -1\n"
    "        priority = (band, order[phase], depth, -leverage, locality,\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
record(rel, "regalloc_search gating and priority for register-dominant nodes with <=2 other faults")

# 5. Tests: the legacy walk skips a gated profile on nodes without register faults; new wiring tests.
rel = "tests/test_completion_campaign.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "    assert visited == [p[\"name\"] for p in campaign.PROFILES if not p.get('type_transaction')]\n",
    "    assert visited == [p[\"name\"] for p in campaign.PROFILES\n"
    "                       if not p.get('type_transaction') and p['name'] != 'regalloc_search']\n", rel)
(STAGE / rel).write_text(text)
record(rel, "legacy walk excludes the gated regalloc_search profile for nodes without register faults")
rel = "tests/test_regalloc_campaign.py"
shutil.copy2(OUT / "test_regalloc_campaign.py", STAGE / rel)
record(rel, "gating, priority and budget wiring")

(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"stage": str(STAGE), "files": list(manifest)}))
