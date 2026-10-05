"""Dry run: does each newly function-exact candidate survive whole-ROM integration on its own?

For every function the jump-table certificate admits (rescore_fb.jsonl, function_exact and not already exact): compile
its best source once more (probe DB), then prepare_integration.prepare + integration_gate.run on a disposable game
copy, exactly as the campaign sweep would for a batch of one. Nothing touches the campaign state or ledger; receipts go
under ~/decomp/runs/loop-shape-20260930/jtbl-integration/.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
OUT = Path("/home/grant/decomp/runs/loop-shape-20260930/jtbl-integration")


def main():
    from eval import integration_gate, prepare_integration, probe_source
    rows = [json.loads(l) for l in (HERE / "rescore_fb.jsonl").read_text().splitlines()]
    todo = [r for r in rows if r.get("function_exact") and not r.get("exact")]
    only = set(sys.argv[1:])
    if only:
        todo = [r for r in todo if r["name"] in only]
    OUT.mkdir(parents=True, exist_ok=True)
    results_path = HERE / "jtbl_integration_dry.jsonl"
    done = {json.loads(l)["function"] for l in results_path.read_text().splitlines()} if results_path.exists() else set()
    conn = probe_source._db()
    for r in todo:
        fn = r["name"]
        if fn in done:
            continue
        db = sqlite3.connect(f"file:{r['ledger']}?mode=ro", uri=True)
        source = db.execute("select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
        db.close()
        row = {"function": fn}
        try:
            a = probe_source.probe(fn, source, "jtbl-integration-dry", conn)
            fb = a.verification["function_boundary"]
            if not fb.get("function_exact"):
                raise ValueError("no longer function-exact: " + str(fb.get("schema_3_error")))
            tag = f"{fn}-{time.time_ns()}"
            source_path = OUT / (tag + ".c")           # the candidate exactly as scored, like a campaign artifact
            source_path.write_text(source, encoding="utf-8")
            entry = {"function": fn, "source": str(source_path), "attempt_id": a.receipt_id,
                     "verification": a.verification}
            manifest = prepare_integration.prepare(repo=REPO, db=probe_source.TRIAL, entries=[entry],
                                                   output_dir=OUT / (tag + "-prepared"))
            receipt = integration_gate.run(repo=REPO, manifest=manifest, output=OUT / (tag + "-integration.json"))
            row.update(status=receipt["status"], whole_rom_verified=bool(receipt.get("whole_rom_verified")),
                       receipt=str(OUT / (tag + "-integration.json")))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        with results_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(fn, row.get("status"), row.get("whole_rom_verified"), row.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
