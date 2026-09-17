"""Re-certify the five narrow-scope function-exact functions at schema 3.

Operator criterion: exact, no unwanted behaviour. A schema-1 certificate's recorded scope is "annotated
function bytes and external call relocations only", which does not SAY it covers rodata reads or
absolute-address literals. Schema 3 compares exactly those. So the question "are these faithful?" has a
mechanical answer in the ROM either way, and this asks it.

Three outcomes per function, and all three are results:
    schema_3_function_exact   the broader check passes -- move it into the counted row
    refused                   schema 3 refuses, and the refusal reason says what does not match
    raised                    the call cannot run here, with the exception recorded

    python3 eval/function_exact_v3_recertify.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import function_boundary                                     # noqa: E402

REPO = Path.home() / "decomp" / "sbk1"
DB = Path.home() / "decomp" / "kb-sbk1.sqlite"

NARROW = ["calculateFixedAngleBetweenXZPoints", "osSpTaskStartGo", "rmonPrintf",
          "updateRaceCameraMenuPreview", "updateRacePlayerPostUpdateAttack"]


def main() -> int:
    conn = sqlite3.connect(str(DB))
    out = Path(ROOT) / "eval/results/function-exact-v3-20260917"
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name in NARROW:
        ws = REPO / "nonmatchings" / name
        row: dict = {"function": name}
        meta = conn.execute("select addr, size from functions where name=?", (name,)).fetchone()
        candidate = ws / f"{name}.o"
        if not candidate.is_file():
            # The scored object is not kept for every workspace, and these functions have no COMPILING
            # attempt in the knowledge base either -- so the source is taken from the certificate's own
            # sibling file. A certificate named `<stem>.verification.json` was produced by
            # `<stem>.c` in the same directory, which makes the receipt and the source the same
            # artifact rather than two things that happen to be near each other.
            from solver import workspace as ws_mod
            source_text = None
            for cert in sorted(ws.glob("*.verification.json")):
                try:
                    body = json.loads(cert.read_text())
                except (OSError, ValueError):
                    continue
                if not (body.get("function_boundary") or {}).get("function_exact"):
                    continue
                sibling = ws / (cert.name[: -len(".verification.json")] + ".c")
                if sibling.is_file():
                    source_text = sibling.read_text(errors="replace")
                    row["source_from"] = sibling.name
                    break
            if source_text is None:
                found = conn.execute(
                    "select a.source_code from attempts a join functions f on f.addr = a.func_addr "
                    "where f.name = ? and a.compiled = 1 order by a.score desc, a.id limit 1",
                    (name,)).fetchone()
                source_text = found[0] if found else None
                row["source_from"] = "knowledge-base"
            if not source_text:
                row["status"] = "no-compiling-source"
                rows[name] = row
                print(f"{name:<40} {row['status']}", flush=True)
                continue
            att = ws_mod.score(ws, REPO, name, source_text, conn=None)
            row["recompiled_score"] = att.score
            row["recompiled_compiled"] = bool(att.compiled)
            if not candidate.is_file():
                row["status"] = "recompile-produced-no-object"
                rows[name] = row
                print(f"{name:<40} {row['status']} compiled={att.compiled}", flush=True)
                continue
        if not (ws / "target.o").is_file():
            row["status"] = "missing-target"
            rows[name] = row
            print(f"{name:<40} {row['status']}", flush=True)
            continue
        try:
            result = function_boundary._certify_v3(
                target=ws / "target.o", candidate=candidate, assembly=ws / "target.s",
                rom=REPO / "snowboardkids.z64", config=REPO / "snowboardkids.yaml",
                symbols=REPO / "symbol_addrs.txt", function=name,
                address=meta[0], size=meta[1])
            row.update(status=result.get("status"), function_exact=bool(result.get("function_exact")),
                       schema=result.get("schema_version"), scope=result.get("scope"),
                       error=result.get("error"))
            # Written as a NEW receipt beside the original, never over it. The evidence tier is
            # immutable: the schema-1 certificate stays exactly as produced, and this records that a
            # broader check was later asked and what it said.
            if row["function_exact"]:
                target_cert = ws / f"{name}.recertified.json"
                target_cert.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
                row["receipt"] = target_cert.name
        except Exception as exc:                                         # noqa: BLE001
            row.update(status="raised", error=f"{type(exc).__name__}: {exc}")
        rows[name] = row
        print(f"{name:<40} status={row.get('status')} exact={row.get('function_exact')} "
              f"{str(row.get('error') or '')[:70]}", flush=True)
    (out / "state.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    hit = sorted(n for n, r in rows.items() if r.get("function_exact"))
    print(f"\nschema-3 function_exact: {len(hit)} {hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
