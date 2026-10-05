"""Is the frame build reproducible? Compare stored runs on the fields that decide the rate."""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")


def summary(name: str) -> dict:
    path = BASE / name
    if not path.is_file():
        return {"name": name, "missing": True}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"name": name, "converted": payload["sequence_converted"],
            "exact": payload["sequence_exact"], "errors": len(payload["errors"]),
            "crashes": len(payload.get("action_crashes") or []),
            "seconds": payload["seconds"],
            "per_action": {k.split(".")[-1]: {kk: vv for kk, vv in v.items()}
                           for k, v in payload["per_action"].items()},
            "rows": {r["function"]: (r["sequence"]["compiled"], r["sequence"]["exact"],
                                     (r.get("draft_sha256") or "")[:10],
                                     tuple(sorted(k.split(".")[-1] for k, v in r["actions"].items()
                                                  if v.get("changed"))))
                     for r in payload["rows"]}}


stored = summary("class-frame.json")
again = summary("class-frame-r4.json")
for item in (stored, again):
    if item.get("missing"):
        print(f"{item['name']}: MISSING")
        continue
    print(f"{item['name']:22} converted={item['converted']} exact={item['exact']} "
          f"errors={item['errors']} crashes={item['crashes']} sec={item['seconds']}")
    print(f"   per_action {json.dumps(item['per_action'])}")

if not again.get("missing"):
    a, b = stored["rows"], again["rows"]
    print(f"\nsame function set: {set(a) == set(b)} ({len(a)} vs {len(b)})")
    differing = [k for k in a if k in b and a[k] != b[k]]
    print(f"functions with a differing outcome: {len(differing)}")
    for name in differing[:12]:
        print(f"   {name}")
        print(f"      stored: {a[name]}")
        print(f"      again : {b[name]}")
