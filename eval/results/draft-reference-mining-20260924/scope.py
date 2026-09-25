"""Exploration: how much of the reference's matching knowledge the first mining pass could not see (counts only).

1. Quirk comments in mining functions' reference definitions (keyword hits: fake, match, IDO, regalloc, pad, ...).
2. Local primitive-type changes draft -> reference (the first pass had no type features): pairs where the multiset of
   declared primitive local types differs, and how often that co-occurs with width residuals (sll/sra/andi/lh/lb)."""
import collections, json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mine
from tools import n64_corpus

KEYS = re.compile(r"\b(fake|match(?:ing|es)?|ido|regalloc|reg alloc|permuter|pad(?:ding)?|required|needed|"
                  r"stack|spill|swap|order|unused|hack|volatile|dummy|nonmatching|delay slot)\b", re.I)
PRIM = r"(?:unsigned\s+|signed\s+)?(s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|long|float|double)"
DECL = re.compile(r"^\s*(?:register\s+|volatile\s+|const\s+)*" + PRIM + r"\s+\**\s*\w+\s*(?:\[[^\]]*\])?\s*(?:=[^;]*)?;", re.M)
WIDTH = {"sll", "sra", "srl", "andi", "lh", "lhu", "lb", "lbu", "sh", "sb"}

comments, hits, fn_with = collections.Counter(), 0, 0
type_changed = width_res = both = n = 0
for path in sorted((mine.E / "rows").glob("*.json")):
    row = json.loads(path.read_text())
    ref_def = row.get("ref_def") or ""
    cs = re.findall(r"/\*.*?\*/|//[^\n]*", ref_def, re.S)
    found = [m.group(1).lower() for c in cs for m in KEYS.finditer(c)]
    fn_with += bool(found)
    comments.update(found)
    ref, draft = row.get("ref") or {}, row.get("draft") or {}
    if not (ref.get("compiled") and draft.get("compiled") and row.get("target_dump")):
        continue
    if mine.mask(ref["dump"]) != mine.mask(row["target_dump"]):
        continue
    res = mine.residual(row["target_dump"], draft["dump"])
    if res is None:
        continue
    n += 1
    body = lambda d: n64_corpus._mask_noncode(d)[d.find("{"):]
    tc = collections.Counter(DECL.findall(body(row["draft_def"]))) != collections.Counter(DECL.findall(body(row["ref_def"])))
    wr = any(f.split(":")[-1].split("/")[0] in WIDTH or any(p in WIDTH for p in f.split(":")[-1].split("/"))
             for f in res[0] if f.startswith("R:"))
    type_changed += tc; width_res += wr; both += tc and wr
print(json.dumps({"mining_functions_with_quirk_comments": fn_with, "keyword_hits": comments.most_common(20),
                  "pairs": n, "local_primitive_types_differ": type_changed, "width_residual": width_res,
                  "both": both}, indent=1))
