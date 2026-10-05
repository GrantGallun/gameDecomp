"""Whole-ROM dry run for the two object-exact lever results (guMtxIdent, drawCharacterSelectCourseExitPreviewPanel).
Rebuilds each rewrite with the lever from its census source, then probe -> prepare_integration -> integration_gate."""
import json, re, sqlite3, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]; sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1"); OUT = Path("/home/grant/decomp/runs/lead3-20260930/object-exact-integration")


def main():
    from eval import integration_gate, prepare_integration, probe_source
    from solver import file_scope_objects, rodata_symbol, workspace
    census = {json.loads(l)["name"]: json.loads(l) for l in (HERE / "census.jsonl").read_text().splitlines()}
    OUT.mkdir(parents=True, exist_ok=True)
    conn = probe_source._db()
    for fn, gen in (("guMtxIdent", file_scope_objects.variants),
                    ("drawCharacterSelectCourseExitPreviewPanel", rodata_symbol.address_variants)):
        r = census[fn]
        src = sqlite3.connect(f"file:{r['ledger']}?mode=ro", uri=True).execute(
            "select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
        ws = workspace.bootstrap(REPO, fn); tag = f"{fn}-{time.time_ns()}"
        workspace.score(ws, REPO, tag, src)
        (label, new), = list(gen(src, fn, target_obj=ws / "target.o", candidate_obj=ws / f"{tag}.o"))
        row = {"function": fn, "lever": label}
        try:
            a = probe_source.probe(fn, new, "object-exact-integration-dry", conn)
            row["object_exact"] = bool(a.exact)
            path = OUT / (tag + ".c"); path.write_text(new, encoding="utf-8")
            entry = {"function": fn, "source": str(path), "attempt_id": a.receipt_id, "verification": a.verification}
            manifest = prepare_integration.prepare(repo=REPO, db=probe_source.TRIAL, entries=[entry],
                                                   output_dir=OUT / (tag + "-prepared"))
            receipt = integration_gate.run(repo=REPO, manifest=manifest, output=OUT / (tag + "-integration.json"))
            row.update(status=receipt["status"], whole_rom_verified=bool(receipt.get("whole_rom_verified")))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        print(json.dumps(row), flush=True)
        for p in ws.glob(f"{tag}*"): p.unlink()


if __name__ == "__main__":
    main()
