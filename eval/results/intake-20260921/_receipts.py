"""Compare the frozen frame's receipts: which measurement said what, and whether the frame moved."""
import json
from pathlib import Path

BASE = Path("eval/results/intake-20260921")
for path in sorted(BASE.glob("wide-*.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:                                  # noqa: BLE001
        print(f"{path.name:34} unreadable: {error}")
        continue
    if "rows" not in payload:
        continue
    print(f"{path.name:34} schema={payload.get('schema_version')} n={len(payload['rows'])} "
          f"conv={payload.get('sequence_converted')} exact={payload.get('sequence_exact')} "
          f"frontend={payload.get('sequence_frontend_passed')}")
