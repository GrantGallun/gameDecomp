"""Paired no-andor experiment on the four frozen game-medium DEV functions."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import m2c_input, workspace
from eval.zero_token_harvest import heldout_names


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    repo = Path.home() / "decomp/sbk1"
    conn = sqlite3.connect(Path.home() / "decomp/kb-sbk1.sqlite", timeout=60)
    panel = ROOT / "eval/sets/autonomy_wavefront_dev_24_v1.json"
    names = [r["function"] for r in json.loads(panel.read_text())["dev"] if r["stratum"] == "game_medium"]
    assert len(names) == 4 and not set(names) & heldout_names(ROOT / "eval/sets")
    report = {"hypothesis": "Disabling m2c compound-condition reconstruction improves fresh draft matching",
        "scope": "inspected header-assisted DEV; fixed context per pair; one draw per arm",
        "source_bodies_read": False, "model_calls": 0, "heldout_used": False,
        "panel_sha256": sha(panel.read_bytes()), "functions": names,
        "adapter_sha256": sha(Path(m2c_input.__file__).read_bytes()),
        "runner_sha256": sha(Path(__file__).read_bytes()), "rows": []}
    def save():
        (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    save()
    for name in names:
        ws = workspace.bootstrap(repo, name)
        target = ws / "target.s"
        assembly = workspace.target_asm(ws, name)
        raw, _ = m2c_input.draft(repo, target)
        if raw.returncode:
            raise RuntimeError(raw.stderr)
        base, meta = m2c_input.header_draft(repo, name, target, assembly, raw.stdout)
        row = {"function": name, "target_sha256": sha(target.read_bytes()), "arms": {}}
        other, other_meta = m2c_input.draft(repo, target, context_headers=tuple(meta["headers"]), no_andor=True)
        assert meta["preprocessed_sha256"] == other_meta["preprocessed_sha256"]
        prefix = "".join(f'#include "{h}"\n' for h in meta["headers"])
        for label, proc, metadata in (("baseline", base, meta), ("no-andor", other, other_meta)):
            code = prefix + proc.stdout
            record = {"m2c": metadata, "m2c_returncode": proc.returncode, "m2c_stderr": proc.stderr,
                      "source_sha256": sha(code.encode())}
            (args.output / f"{name}-{label}.c").write_text(code)
            if proc.returncode == 0:
                attempt = workspace.score(ws, repo, name, code, conn=conn, func=name,
                    strategy="decompedia-m2c-" + label, run_id=args.output.name,
                    run_kind="inspected-development", token_cost=0)
                record.update(attempt_id=attempt.receipt_id, compiled=attempt.compiled,
                    score=attempt.score, exact=attempt.exact, frontend=attempt.frontend,
                    compiler_recipe=attempt.compiler_recipe, verification=attempt.verification,
                    diff=attempt.diff, diagnostics=attempt.compiler_stderr)
                print(name, label, attempt.receipt_id, attempt.compiled, attempt.exact, attempt.score, flush=True)
            row["arms"][label] = record
        assert row["target_sha256"] == sha(target.read_bytes())
        report["rows"].append(row)
        save()
    conn.close()


if __name__ == "__main__":
    main()
