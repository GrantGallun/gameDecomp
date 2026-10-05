"""Stage the revalidate@<semantic-environment digest> profile over the frozen campaign runtime.

    python eval/results/pointer-contracts-20260914/stage_revalidate.py

A semantic-lane node's stored differential failure only refreshes when a job runs on it, and
nodes that already used both semantic profiles for their evidence key get no further work. After
the pointer-contract amendment their stored failures are stale. revalidate@<digest> re-scores
the unchanged source once (zero-model) whenever the semantic-environment code changes.
"""
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / "eval/results/resume-pipeline-20260908/code"
STAGE = OUT / "staged-revalidate2"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def replace_once(text, before, after, rel):
    if text.count(before) != 1:
        raise SystemExit(f"{rel}: anchor found {text.count(before)} times: {before[:80]!r}")
    return text.replace(before, after, 1)


shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "results"))
(STAGE / "eval/results").symlink_to(ROOT / "eval/results", target_is_directory=True)
manifest = {}
rel = "solver/repair_queue.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "def has_address_literals(node):\n",
    "_SEMANTIC_DIGEST = None\n"
    "\n\n"
    "def semantic_environment_digest():\n"
    "    \"\"\"Identity of the differential-execution environment code: a change re-validates failing nodes once.\"\"\"\n"
    "    global _SEMANTIC_DIGEST\n"
    "    if _SEMANTIC_DIGEST is None:\n"
    "        import hashlib\n"
    "        from pathlib import Path\n"
    "        here = Path(__file__).resolve().parent\n"
    "        digest = hashlib.sha256()\n"
    "        for path in (here / 'pointer_contracts.py', here / 'callee_execution.py', here / 'mips_differential.py',\n"
    "                     here.parent / 'eval' / 'semantic_lane.py'):\n"
    "            digest.update(path.read_bytes() if path.exists() else b'')\n"
    "        _SEMANTIC_DIGEST = digest.hexdigest()[:16]\n"
    "    return _SEMANTIC_DIGEST\n"
    "\n\n"
    "def has_address_literals(node):\n", rel)
text = replace_once(text,
    "    if (residual.get('faults') or {}).get('relocation') and has_address_literals(node):\n",
    "    semantic = (node.get('semantic_validation') or {}).get('status')\n"
    "    if semantic in ('observed_failure', 'inconclusive'):\n"
    "        name = 'revalidate@' + semantic_environment_digest()\n"
    "        if (name, node['source_sha256']) not in done:\n"
    "            return {'name': name, 'model': False, 'deterministic_budget': 0}\n"
    "    if (residual.get('faults') or {}).get('relocation') and has_address_literals(node):\n", rel)
text = replace_once(text,
    "        if profile['name'] == 'address_symbols' or profile['name'].startswith('recertify@'):\n",
    "        if profile['name'].startswith('revalidate@') and \\\n"
    "                (node.get('semantic_validation') or {}).get('status') == 'observed_failure':\n"
    "            # 2026-09-14: stale stack-offset failures kept 66 nodes in model-only semantic jobs.\n"
    "            band = -1\n"
    "        if profile['name'] == 'address_symbols' or profile['name'].startswith('recertify@'):\n", rel)
compile(text, rel, "exec")
(STAGE / rel).write_text(text)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel),
                 "scope": "revalidate@<semantic environment digest> for failing/inconclusive semantic nodes"}
rel = "tests/test_census_campaign.py"
text = (LIVE / rel).read_text() + '''

def test_failing_semantic_nodes_revalidate_once_per_environment_code_and_source(tmp_path):
    failing = node(tmp_path, faults={"structural": 9}, source="void f(void) {}\\n", sha="v1")
    failing["semantic_validation"] = {"status": "observed_failure", "source_sha256": "v1"}
    profile = queue.next_profile(failing, 3, campaign.PROFILES)
    assert profile["name"] == "revalidate@" + queue.semantic_environment_digest() and not profile["model"]
    failing["jobs"] = [{"profile": profile["name"], "source_sha256": "v1"}]
    assert not queue.next_profile(failing, 3, campaign.PROFILES)["name"].startswith("revalidate@")
    passing = node(tmp_path, faults={"structural": 9}, source="void f(void) {}\\n", sha="v2")
    assert not queue.next_profile(passing, 3, campaign.PROFILES)["name"].startswith("revalidate@")
    state = {"config": {"model_calls": 3}, "nodes": {"failing": node(tmp_path, faults={"structural": 9},
             source="void f(void) {}\\n", sha="v3", jobs=[{"profile": "old", "source_sha256": "x", "evidence_key": "o"}] * 12)}}
    state["nodes"]["failing"]["semantic_validation"] = {"status": "observed_failure", "source_sha256": "v3"}
    snapshot, _selected = queue.project(state, campaign.PROFILES)
    assert snapshot["work_items"]["failing"]["priority"][0] == -1
'''
(STAGE / rel).write_text(text)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel), "scope": "revalidate wiring test"}
rel = "tests/test_repair_queue.py"
text = (LIVE / rel).read_text()
text = replace_once(text,
    "def state(nodes, **config):\n",
    "def revalidated(n):\n"
    "    \"\"\"The node's stored semantic verdict is current for this environment code (revalidate@ already spent).\"\"\"\n"
    "    n['jobs'].append({'profile': 'revalidate@' + queue.semantic_environment_digest(),\n"
    "                      'source_sha256': n['source_sha256']})\n"
    "    return n\n"
    "\n\n"
    "def state(nodes, **config):\n", rel)
text = replace_once(text,
    "def test_route_by_evidence(semantic, expected):\n    n = node(semantic)\n",
    "def test_route_by_evidence(semantic, expected):\n    n = revalidated(node(semantic))\n", rel)
text = replace_once(text,
    "    n = node({'status': 'inconclusive', 'counts': {'passed': 8, 'inconclusive': 56}})\n    key = queue.evidence_key(n)\n",
    "    n = revalidated(node({'status': 'inconclusive', 'counts': {'passed': 8, 'inconclusive': 56}}))\n    key = queue.evidence_key(n)\n", rel)
text = replace_once(text,
    "def test_retry_requires_new_measured_evidence_not_logging_changes():\n    n = node({'status': 'observed_failure', 'counts': {'failed': 1}})\n",
    "def test_retry_requires_new_measured_evidence_not_logging_changes():\n    n = revalidated(node({'status': 'observed_failure', 'counts': {'failed': 1}}))\n", rel)
(STAGE / rel).write_text(text)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel),
                 "scope": "semantic routing fixtures model nodes already revalidated for the current environment"}

(OUT / "staged-revalidate-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"stage": str(STAGE), "files": list(manifest)}))
