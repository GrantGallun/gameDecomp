"""Run the main-tree ROM-backed certificate on the saved v3 fixtures.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/v3_check.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import function_boundary as fb  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
for fixture in sorted((Path.home() / "decomp/v3-fixtures").iterdir()):
    meta = json.loads((fixture / "meta.json").read_text())
    result = fb.certify(target=fixture / "target.o", candidate=fixture / "candidate.o", assembly=fixture / "target.s",
                        rom=REPO / "snowboardkids.z64", config=REPO / "snowboardkids.yaml",
                        symbols=REPO / "symbol_addrs.txt", function=meta["function"],
                        address=meta["address"], size=meta["size"])
    print(meta["function"], result.get("schema_version"), result.get("status"), result.get("error"),
          result.get("schema_3_error"), result.get("data_sites"), result.get("absolute_literals"))
