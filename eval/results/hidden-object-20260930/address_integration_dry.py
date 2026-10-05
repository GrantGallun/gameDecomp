"""Dry run: does each rodata_address rewrite survive whole-ROM integration on its own?

Function-exact is not enough for address-taken rodata: drawRaceSplitscreenSelectEntryFee's own literal passed the
function certificate and failed the ROM checksum (2026-09-14). So each rewritten source from address_cases.json goes
through probe_source.probe, prepare_integration.prepare and integration_gate.run on a disposable game copy, the same
batch-of-one path as loop-shape-20260930/jtbl_integration_dry.py. Nothing touches the campaign state or ledger.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
OUT = Path("/home/grant/decomp/runs/lead3-20260930/address-integration")


def main():
    from eval import integration_gate, prepare_integration, probe_source
    cases = [c for c in json.loads((HERE / "address_cases.json").read_text(encoding="utf-8")) if c.get("rewritten")]
    OUT.mkdir(parents=True, exist_ok=True)
    results = HERE / "address_integration_dry.jsonl"
    conn = probe_source._db()
    for case in cases:
        fn, source = case["function"], case["rewritten"]
        row = {"function": fn}
        try:
            a = probe_source.probe(fn, source, "rodata-address-integration-dry", conn)
            fb = a.verification["function_boundary"]
            if not fb.get("function_exact"):
                raise ValueError("not function-exact: " + str(fb.get("schema_3_error") or fb.get("error")))
            tag = f"{fn}-{time.time_ns()}"
            path = OUT / (tag + ".c")
            path.write_text(source, encoding="utf-8")
            entry = {"function": fn, "source": str(path), "attempt_id": a.receipt_id, "verification": a.verification}
            manifest = prepare_integration.prepare(repo=REPO, db=probe_source.TRIAL, entries=[entry],
                                                   output_dir=OUT / (tag + "-prepared"))
            receipt = integration_gate.run(repo=REPO, manifest=manifest, output=OUT / (tag + "-integration.json"))
            row.update(status=receipt["status"], whole_rom_verified=bool(receipt.get("whole_rom_verified")),
                       receipt=str(OUT / (tag + "-integration.json")))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        with results.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(fn, row.get("status"), row.get("whole_rom_verified"), row.get("error", ""), flush=True)


if __name__ == "__main__":
    main()
