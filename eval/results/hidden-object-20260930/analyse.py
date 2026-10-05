"""Summarise census.jsonl: which unsolved functions carry an object difference the text diff cannot show."""
import collections, json, sys
from pathlib import Path

rows = [json.loads(l) for l in (Path(__file__).parent / "census.jsonl").read_text().splitlines()]
ok = [r for r in rows if "nontext" in r]
print(f"rows {len(rows)}  compared {len(ok)}  errors {sum('error' in r for r in rows)}  "
      f"no-objects {sum(r.get('note') == 'no objects' for r in rows)}")
hidden = [r for r in ok if r["nontext"]]
print(f"functions with >=1 non-.text difference: {len(hidden)} of {len(ok)}")
kinds = collections.Counter((d["section"], d["kind"]) for r in hidden for d in r["nontext"])
funcs = collections.Counter()
for r in hidden:
    for k in {(d["section"], d["kind"]) for d in r["nontext"]}:
        funcs[k] += 1
print("\n(section, kind) -> functions")
for k, n in funcs.most_common():
    print(f"  {k}: {n}")
print("\ntext byte-identical, only non-.text differs (object would be exact but for invisible rows):")
for r in sorted(hidden, key=lambda r: -(r["score"] or 0)):
    if r.get("text_sha_equal"):
        print(f"  {r['score']:7.2f} {r['name']:45} {r['nontext']}")
print("\n.rodata same size, different bytes (wrong constant values; invisible to the diff):")
for r in sorted(hidden, key=lambda r: -(r["score"] or 0)):
    for d in r["nontext"]:
        if d["kind"] == "bytes":
            print(f"  {r['score']:7.2f} {r['name']:45} {d['section']} {d['n_bytes']}/{d['size']}B first={d['first']}")
jtbl = set(json.loads((Path(__file__).parents[1] / "loop-shape-20260930/jtbl_only.json").read_text()))
print(f"\nNOT covered by the 9/30 jump-table certificate ({len(jtbl)} functions), by first difference:")
rest = [r for r in hidden if r["name"] not in jtbl]
for r in sorted(rest, key=lambda r: -(r["score"] or 0)):
    print(f"  {r['score']:7.2f} text_eq={int(bool(r.get('text_sha_equal')))} {r['name']:45} "
          f"{[(d['section'], d['kind'], d.get('target_size'), d.get('cand_size', d.get('cand'))) for d in r['nontext']]}")
print(f"  total {len(rest)}; text-identical {sum(bool(r.get('text_sha_equal')) for r in rest)}")
print("\nscore bands of hidden-difference functions:")
band = collections.Counter(("100" if (r["score"] or 0) >= 100 else ">=99" if r["score"] >= 99 else ">=90"
                            if r["score"] >= 90 else "<90") for r in hidden)
print(" ", dict(band))
