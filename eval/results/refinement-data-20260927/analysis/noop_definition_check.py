"""Measure, don't infer: is a logged "no-op" really the same object, and is the optimizer key a safe
stand-in for "same object" on CAMPAIGN edits (not just the tree pilot's 293 pairs)?

Truth here is byte_certificate.certify(parent.o, child.o): allocated sections and relocation
expressions, the comparison exactness itself uses. The logged diff compares NORMALIZED listings,
so "identical diff" was only assumed to mean "identical object".

Sample: real edits (source changed) from the campaign, stratified by strategy family, half with an
identical diff (logged no-ops), half with a different diff. Both sides are recompiled today in an
isolated copy; a pair whose recompiled diff disagrees with the log is reported as drift, not used.

    python3 noop_definition_check.py [per_stratum]   (cwd holding campaign.sqlite)
"""
import collections
import hashlib
import json
import random
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from solver import byte_certificate, ido_stages, workspace  # noqa: E402

REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/noop-definition-20260927")
PER = int(sys.argv[1]) if len(sys.argv) > 1 else 25
db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
names = dict(db.execute("select addr, name from functions"))


def body(diff):
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return "\n".join(lines)


meta = {}
for i, addr, comp, ex, sha, strategy, diff in db.execute(
        "select id, func_addr, coalesce(compiled,0), coalesce(exact,0), source_sha256, "
        "coalesce(strategy,''), diff_summary from attempts"):
    meta[i] = (addr, comp, ex, sha, strategy.split(":")[0],
               hashlib.sha1(body(diff).encode()).hexdigest())
strata = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    if p in meta and c in meta and meta[p][1] and meta[c][1] and not meta[p][2] and not meta[c][2] \
            and meta[p][3] != meta[c][3] and meta[p][0] == meta[c][0]:
        strata[(meta[c][4], meta[p][5] == meta[c][5])].append((p, c))
rng = random.Random(20260927)
families = collections.Counter(k[0] for k, v in strata.items() for _ in v)
top = [f for f, _ in families.most_common(8)]
sample = []
for fam in top:
    for same in (True, False):
        pool = strata.get((fam, same), [])
        sample += [(fam, same, p, c) for p, c in rng.sample(pool, min(PER, len(pool)))]
print(f"strategy families: {top}; pairs: {len(sample)}", flush=True)


def build(ws, iso, tag, src, name):
    att = workspace.score(ws, iso, tag, src)
    obj = ws / f"{tag}.o"
    keep = NATIVE / "objs" / f"{tag}.o"
    keep.parent.mkdir(parents=True, exist_ok=True)
    if att.compiled and obj.is_file():
        shutil.copy2(obj, keep)
        return att, keep
    return att, None


cells = collections.Counter()
by_family = collections.defaultdict(collections.Counter)
for n, (fam, same_logged, p, c) in enumerate(sample):
    name = names[meta[p][0]]
    p_src, p_diff = db.execute("select source_code, diff_summary from attempts where id=?", (p,)).fetchone()
    c_src, c_diff = db.execute("select source_code, diff_summary from attempts where id=?", (c,)).fetchone()
    iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    pa, po = build(ws, iso, f"{name}_p{n}", p_src, name)
    ca, co = build(ws, iso, f"{name}_c{n}", c_src, name)
    if po is None or co is None:
        cells["did not compile today"] += 1
        continue
    same_diff_now = body(pa.diff) == body(ca.diff)
    if same_diff_now != same_logged:
        cells["drift: today's diffs disagree with the log"] += 1
        continue
    receipt = byte_certificate.certify(po, co, source=c_src)
    if receipt.get("status") == "unverified":
        cells["certificate unverified"] += 1
        continue
    same_obj = bool(receipt["exact"])
    kp, kc = ido_stages.optimizer_key(iso, ws, name, p_src), ido_stages.optimizer_key(iso, ws, name, c_src)
    key = "key n/a" if kp is None or kc is None else ("key same" if kp == kc else "key differs")
    cells[("diff same" if same_diff_now else "diff differs", "object same" if same_obj else "object differs")] += 1
    cells[(key, "object same" if same_obj else "object differs")] += 1
    by_family[fam][(key, "object same" if same_obj else "object differs")] += 1
    if n % 20 == 0:
        print(json.dumps({"done": n + 1}), flush=True)

print("\nNO-OP DEFINITION (logged diff equality vs object bytes):")
for d in ("diff same", "diff differs"):
    for o in ("object same", "object differs"):
        print(f"  {d:12s} {o:15s} {cells[(d, o)]:5d}")
print("\nOPTIMIZER KEY vs OBJECT BYTES (all families):")
for k in ("key same", "key differs", "key n/a"):
    for o in ("object same", "object differs"):
        print(f"  {k:12s} {o:15s} {cells[(k, o)]:5d}")
ks, kd = cells[("key same", "object same")], cells[("key same", "object differs")]
noops = cells[("key same", "object same")] + cells[("key differs", "object same")]
print(f"  precision (same key => same object): {ks}/{ks + kd}"
      f"   recall (same objects the key catches): {ks}/{noops}")
print("\nper strategy family: key same & object differs (a wrong skip) / key same")
for fam, c in by_family.items():
    print(f"  {fam:40s} {c[('key same', 'object differs')]:3d} / {c[('key same', 'object same')] + c[('key same', 'object differs')]:3d}"
          f"   no-ops caught {c[('key same', 'object same')]:3d} / {c[('key same', 'object same')] + c[('key differs', 'object same')]:3d}")
print("\nexcluded:", {k: v for k, v in cells.items() if isinstance(k, str)})
