"""How much of the harvest says more than a binary-width scalar (what solver/globaldecl already emits)? No compiles."""
import collections, json, re
from pathlib import Path
E = Path.home() / "decomp/experiments/type-flywheel-20260924"
h = json.loads((E / "harvest.json").read_text())
SCALAR = r"(?:volatile\s+)?(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|long|float|double|void)"
c = collections.Counter()
examples = collections.defaultdict(list)
for s, kinds in h["symbols"].items():
    for g in kinds.get("global", []):
        k = "global:scalar" if re.fullmatch(rf"extern\s+{SCALAR}\s+\w+\s*;", g) else "global:informative"
        c[k] += 1
        if k.endswith("informative") and len(examples[k]) < 6:
            examples[k].append(g)
    for sig in kinds.get("signature", []) + kinds.get("prototype", []):
        params = sig[sig.find("(") + 1:sig.rfind(")")]
        ret = sig[:sig.find("(")]
        rich = "*" in sig or "struct" in sig or re.search(r"\b[A-Z]\w*\b", ret + params)
        k = "sig:informative" if rich else "sig:scalar-only"
        c[k] += 1
        if rich and len(examples[k]) < 8:
            examples[k].append(sig)
print(dict(c))
for k, v in examples.items():
    print(k); [print("   ", x) for x in v]
print("aggregates:", list(h["aggregates"])[:20])
