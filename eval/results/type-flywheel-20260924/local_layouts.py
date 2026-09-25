"""For flagged functions that stay exact without game includes but define a game-only type locally: is the local
layout binary-derived (only unk/pad/field_ names, or a solver/typedecl banner) or does it carry named members?
Counts only."""
import json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import harvest
from strip_ablation import latest_exact, INC
from game_type_leak import game_only
rows = json.loads((HERE / "strip_ablation.json").read_text())["rows"]
conn = sqlite3.connect(f"file:{harvest.KB}?mode=ro", uri=True)
MEMBER = re.compile(r"^\s*(?:unsigned\s+|signed\s+|struct\s+)?\w+[\s\*]+(\w+)\s*(?:\[[^\]]*\])*\s*;", re.M)
GENERIC = re.compile(r"^(?:unk|pad|field|_?pad|filler|m2c|unused|arr)[_0-9A-Fa-fx]*$|^unk", re.I)
res = {"binary_derived_names": [], "named_members": []}
for r in rows:
    if not (r["exact"] and r["still_uses_game_only_type"]):
        continue
    src = INC.sub("", latest_exact(conn, r["function"]))
    named = []
    for m in re.finditer(r"(?:struct|union)\s*(\w*)\s*\{(.*?)\}\s*(\w*)\s*;", src, re.S):
        tag = m.group(1) or m.group(3)
        if tag not in game_only and m.group(3) not in game_only:
            continue
        named += [x for x in MEMBER.findall(m.group(2)) if not GENERIC.match(x)]
    key = "named_members" if named else "binary_derived_names"
    res[key].append({"function": r["function"], "examples": sorted(set(named))[:5]})
print({k: len(v) for k, v in res.items()})
print(res["named_members"][:6])
(HERE / "local_layouts.json").write_text(json.dumps(res, indent=1))
