"""Synthetic reproductions of outstanding review findings; no campaign/source access.

Run from the repository root with:
    python -m eval.results.claude-review-20260928.open_findings
These assertions document the observed bugs, not desired regression behavior.
"""
import tempfile
from pathlib import Path

from solver.experiment_memory import Notebook
from solver.investigation import Tools, compiler_identity
from solver.modelrepair import CandidateState, _frontier
from solver.workspace import Attempt


class ExtraCases:
    cases = [1]

    def evaluate(self, source, obj):
        failed = source == "wrong"
        return {
            "status": "observed_failure" if failed else "observed_pass_with_execution_debt",
            "receipt": "synthetic.json", "scope": "finite",
            "cases": [{"comparison": "failed" if failed else "passed", "input": {},
                       "reasons": ["return differs"] if failed else [],
                       "first_divergence": "return" if failed else ""}],
        }


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    tools = Tools(root, root, None, "f", root)
    tools.execution = ExtraCases()
    base_report = {
        "status": "observed_pass_with_execution_debt",
        "semantic_key": [8, 8, 0, 0, 0, 0, 0], "panel_sha256": "fixed",
        "counts": {"passed": 8}, "total": 8,
    }
    states = [CandidateState("wrong", Attempt(True, 99, False, "", "", ""), None),
              CandidateState("right", Attempt(True, 80, False, "", "", ""), None)]
    for state in states:
        state.semantic = tools.evaluate(state, lambda _: base_report)
    selected = _frontier(states, 1)[0]
    assert states[0].semantic["status"] == "observed_failure"
    assert states[0].semantic["counts"] == {"passed": 8}
    assert selected.source == "wrong"
    print("CONFIRMED: failed candidate retains passing rank and is selected")

    compiler = root / "tools" / "ido-recomp" / "linux"
    compiler.mkdir(parents=True)
    (compiler / "cc").write_bytes(b"same compiler")
    common_recipe = {"target": "build/src/f.o",
                     "command": ["tools/ido-recomp/linux/cc", "-c"],
                     "helper_sha256": "same-helper"}
    identities = []
    for slot in range(2):
        recipe = {**common_recipe,
                  "script": f"/slots/{slot}/repo/nonmatchings/f/.compiler-key.sh",
                  "manifest": f"/slots/{slot}/repo/nonmatchings/f/.compiler-key.json"}
        identities.append({"compiler": compiler_identity(root, recipe)})
    notebook_path = root / "f.jsonl"
    Notebook(notebook_path, "f", identities[0]).append({
        "action": "patch", "hypothesis": "synthetic experiment", "status": "valid"})
    recovered = Notebook(notebook_path, "f", identities[1]).retrieve()["events"]
    assert identities[0] != identities[1]
    assert recovered == []
    print("CONFIRMED: changing only worker paths hides prior notebook events")
