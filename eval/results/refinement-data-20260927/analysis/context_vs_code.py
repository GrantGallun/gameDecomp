"""How much compile failure is CONTEXT (headers, declarations, types) vs CODE? Read-only.

IDO's cfe is terse, so each failure is classified by its first error AND the offending source line:
  context:  'X' undefined; redeclaration; conflicting/incompatible declarations; a "Syntax Error" on
            a declaration-shaped line whose leading type name is not a C keyword or scalar typedef
            (IDO reports an unknown type name as a syntax error)
  layout:   struct/member/selector/subscript errors: the TYPE is known but its shape is wrong
  code:     other syntax errors, bad operands, casts, unterminated tokens
  infra:    no error line at all
"""
import collections
import re
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
KNOWN = set("""void char short int long float double signed unsigned struct union enum const volatile
static extern register typedef s8 u8 s16 u16 s32 u32 s64 u64 f32 f64 if else for while do return
switch case default break continue goto sizeof""".split())
DECL = re.compile(r"^\s*(?:extern\s+|static\s+|const\s+|volatile\s+|register\s+)*([A-Za-z_]\w*)"
                  r"[\s\*]+[A-Za-z_]\w*\s*(?:[\[;=,()])")


def classify(err: str, source: str) -> str:
    first = next((l for l in (err or "").splitlines() if "rror" in l), "")
    if not first:
        return "infra: no error line"
    low = first.lower()
    if "undefined" in low or "redeclaration" in low or "conflicting" in low or "incompatible" in low \
            and "declaration" in low:
        return "context: undefined/redeclared"
    if any(k in low for k in ("selector requires", "subscripting", "member", "not a struct",
                              "dereferenced a non-pointer", "non-scalar")):
        return "layout: struct/member/type shape"
    if "syntax error" in low:
        m = re.search(r"line (\d+)", first)
        if m:
            lines = (source or "").splitlines()
            n = int(m.group(1))
            line = lines[n - 1] if 0 < n <= len(lines) else ""
            d = DECL.match(line)
            if d and d.group(1) not in KNOWN:
                return "context: unknown type name (syntax error on a declaration)"
        return "code: syntax"
    return "code: other"


by_author = collections.defaultdict(collections.Counter)
for err, src, strategy, model, raw in db.execute(
        "select compiler_stderr, source_code, strategy, coalesce(model,''), "
        "length(coalesce(raw_response,'')) from attempts where coalesce(compiled,0)=0"):
    author = "model" if raw and model not in ("", "zero-model") else "deterministic"
    by_author[author][classify(err, src)] += 1
for author, counts in by_author.items():
    total = sum(counts.values())
    groups = collections.Counter()
    for k, v in counts.items():
        groups[k.split(":")[0]] += v
    print(f"\n{author}: {total} failed compiles  ->  "
          + ", ".join(f"{g} {v / total:.0%}" for g, v in groups.most_common()))
    for k, v in counts.most_common():
        print(f"  {v:6d}  {k}")
