"""Does the candidate's own TU-local preamble (typedefs, #define aliases) survive whole-ROM integration?

The six function-exact cases of address_cases.json were blocked at `prepare_integration` (2026-09-30). The preparer now
carries file-scope typedefs and object-like #defines. The `rewritten` (extern D_800E...) form cannot link: the label is
in no linker script and its bytes were only ever emitted by a TU literal. So this probe runs the ORIGINAL source of each
case (inline literals) through replace_function -> integration_gate.run, bypassing the function certificate on purpose:
it asks only whether the whole ROM is byte-identical. Disposable game copy; trial DB read-only; nothing written to a
campaign or ledger.

    wsl: /home/grant/decomp/sbk1/.venv/bin/python eval/results/hidden-object-20260930/linking_probe.py [variant] [fn...]
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
TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"
OUT = Path("/home/grant/decomp/runs/lead3-20260930/linking-probe")


def main():
    from eval import integration_gate, prepare_integration as prep
    variant = sys.argv[1] if len(sys.argv) > 1 else "source"
    only = set(sys.argv[2:])
    cases = json.loads((HERE / "address_cases.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    results = HERE / "linking_probe.jsonl"
    conn = sqlite3.connect(f"file:{TRIAL}?mode=ro", uri=True)
    for case in cases:
        fn = case["function"]
        if only and fn not in only:
            continue
        source = case.get(variant)
        row = {"function": fn, "variant": variant}
        try:
            if not source:
                raise ValueError("no such variant")
            tu = conn.execute("SELECT t.name FROM functions f JOIN tus t ON f.tu_id=t.id WHERE f.name=?", (fn,)).fetchone()[0]
            path = tu[6:-2] + ".c" if tu.startswith("build/") and tu.endswith(".o") else tu
            original = (REPO / path).read_text(encoding="utf-8")
            _, _, _, preamble = prep._candidate_split(source, fn)
            replaced = prep.replace_function(original, source, fn)
            tag = f"{fn}-{variant}-{time.time_ns()}"
            prepared = OUT / (tag + "-prepared")
            prepared.mkdir()
            data = replaced.encode("utf-8")
            (prepared / "000.c").write_bytes(data)
            manifest = prepared / "manifest.json"
            manifest.write_text(json.dumps({
                "kind": "exact-function-integration", "reference_rom": "snowboardkids.z64",
                "reference_sha256": integration_gate.sha((REPO / "snowboardkids.z64").read_bytes()),
                "built_rom": "build/snowboardkids.z64",
                "replacements": [{"path": path, "base_sha256": integration_gate.sha(original.encode("utf-8")),
                                  "replacement": "000.c", "replacement_sha256": integration_gate.sha(data)}],
                "scope": "probe: certificate bypassed"}, indent=2))
            receipt = integration_gate.run(repo=REPO, manifest=manifest, output=OUT / (tag + "-integration.json"))
            row.update(status=receipt["status"], whole_rom_verified=bool(receipt.get("whole_rom_verified")),
                       preamble=len(preamble), receipt=str(OUT / (tag + "-integration.json")))
        except Exception as exc:
            row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        with results.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        print(fn, row.get("status"), row.get("whole_rom_verified"), row.get("error", ""), flush=True)


BATCH = ["updateEndingObjectSpriteDebugViewer", "func_8005D558", "func_8005AC44", "func_8005905C",
         "func_8005A884", "func_8005CF60"]


def batch():
    """All verified-alone functions in ONE combined ROM: same-TU typedef sharing, ordering, rodata layout."""
    from eval import integration_gate, prepare_integration as prep
    cases = {c["function"]: c for c in json.loads((HERE / "address_cases.json").read_text(encoding="utf-8"))}
    conn = sqlite3.connect(f"file:{TRIAL}?mode=ro", uri=True)
    grouped, bases = {}, {}
    for fn in BATCH:
        tu = conn.execute("SELECT t.name FROM functions f JOIN tus t ON f.tu_id=t.id WHERE f.name=?", (fn,)).fetchone()[0]
        path = tu[6:-2] + ".c" if tu.startswith("build/") and tu.endswith(".o") else tu
        original = (REPO / path).read_text(encoding="utf-8")
        bases.setdefault(path, original)
        grouped[path] = prep.replace_function(grouped.get(path, original), cases[fn]["source"], fn)
    tag = f"batch-{time.time_ns()}"
    prepared = OUT / (tag + "-prepared")
    prepared.mkdir(parents=True)
    replacements = []
    for index, (path, source) in enumerate(sorted(grouped.items())):
        data = source.encode("utf-8")
        (prepared / f"{index:03d}.c").write_bytes(data)
        replacements.append({"path": path, "base_sha256": integration_gate.sha(bases[path].encode("utf-8")),
                             "replacement": f"{index:03d}.c", "replacement_sha256": integration_gate.sha(data)})
    manifest = prepared / "manifest.json"
    manifest.write_text(json.dumps({
        "kind": "exact-function-integration", "reference_rom": "snowboardkids.z64",
        "reference_sha256": integration_gate.sha((REPO / "snowboardkids.z64").read_bytes()),
        "built_rom": "build/snowboardkids.z64", "replacements": replacements,
        "scope": "probe: certificate bypassed"}, indent=2))
    receipt = integration_gate.run(repo=REPO, manifest=manifest, output=OUT / (tag + "-integration.json"))
    row = {"batch": BATCH, "tus": sorted(grouped), "status": receipt["status"],
           "whole_rom_verified": bool(receipt.get("whole_rom_verified"))}
    with (HERE / "linking_probe.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    print(row, flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["batch"]:
        batch()
    else:
        main()

