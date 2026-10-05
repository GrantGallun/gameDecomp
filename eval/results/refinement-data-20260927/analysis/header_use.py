"""Do exported children USE the reconstructed game/ headers they #include, or only include them?

Run under WSL:  python3 header_use.py [repair_dataset.jsonl] [sbk1 include dir]
"""
import collections
import json
import pathlib
import re
import sys

jsonl = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/grant/decomp/experiments/refinement-data-20260927/v2/repair_dataset.jsonl"
include = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/home/grant/decomp/sbk1/include")

recs = [json.loads(line) for line in open(jsonl)]
ident = re.compile(r"\b[A-Za-z_]\w*\b")
decl = re.compile(r"}\s*(\w+)\s*;|\bstruct\s+(\w+)\s*\{|\btypedef\s+[^;{]*?\b(\w+)\s*;"
                  r"|^\s*(?:extern\s+)?[\w\s\*]+?\b(\w+)\s*\(", re.M)
game, other = set(), set()
for path in include.rglob("*.h"):
    names = {x for groups in decl.findall(path.read_text(errors="replace")) for x in groups if x}
    (game if path.relative_to(include).parts[0] == "game" else other).update(names)
game_only = game - other


def code_identifiers(source: str) -> set[str]:
    # comments first (may span lines), then whole preprocessor lines -- WITHOUT re.S, or `#.*`
    # eats the rest of the file (the bug the first version of this script had: it reported 0)
    body = re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.S)
    body = re.sub(r"^[ \t]*#[^\n]*", " ", body, flags=re.M)
    return set(ident.findall(body))


probe_name = sorted(game_only)[0]
assert code_identifiers(f'#include "game/x.h"\nvoid f(void){{ {probe_name}; }}') & game_only, \
    "the use check must fire on a known use"

includes = collections.Counter()
uses = collections.Counter()
used_any = included_unused = 0
for record in recs:
    source = record["target"]["source_c"]
    headers = set(re.findall(r'^\s*#\s*include\s*[<"](game/[^>"]+)[>"]', source, re.M))
    includes.update(headers)
    used = code_identifiers(source) & game_only
    uses.update(used)
    used_any += bool(used)
    included_unused += bool(headers) and not used
print("records", len(recs), "| identifiers declared only under include/game:", len(game_only))
print("children including a game/ header:", sum(1 for r in recs if re.search(
    r'^\s*#\s*include\s*[<"]game/', r["target"]["source_c"], re.M)))
print("children using a game-only identifier:", used_any,
      "| include game/ but use none:", included_unused)
print("most included:", includes.most_common(8))
print("most used game-only identifiers:", uses.most_common(20))

clean = [r for r in recs if r["meta"].get("clean")]
clean_using = [r for r in clean if code_identifiers(r["target"]["source_c"]) & game_only]
print("records tagged clean:", len(clean), "| of which use a game-only identifier:", len(clean_using))
print("  e.g.", sorted((code_identifiers(clean_using[0]["target"]["source_c"]) & game_only))[:8]
      if clean_using else None)
