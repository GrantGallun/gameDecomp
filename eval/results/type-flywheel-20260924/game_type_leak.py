"""Integrity check: SOLVED exact sources that use a TYPE name defined only in the reconstructed include/game/**
headers (the decomp team's vocabulary; eval.status's header-assisted tier keys on strategy strings and
`#include "game/`, so a source that defines such a type locally is not caught). Counts only; changes no tier.

A type counts if it is defined (struct/union tag or typedef name) in include/game/** and NOT in any other include/
header (SDK / libultra / common). Every exact source of a function is checked; a function is flagged only if ALL its
exact sources use such a type (the same conservative rule eval.status uses for reference-only types)."""
import json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harvest
REPO = Path.home() / "decomp/sbk1"
DEF = [re.compile(r"\b(?:struct|union)\s+([A-Za-z_]\w*)\s*\{"), re.compile(r"\}\s*([A-Za-z_]\w*)\s*;"),
       re.compile(r"\btypedef\s+[^;{]*?\b([A-Za-z_]\w*)\s*;")]


def defined(paths):
    out = set()
    for p in paths:
        t = p.read_text(errors="replace")
        for d in DEF:
            out.update(d.findall(t))
    return out


game = defined((REPO / "include/game").rglob("*.h"))
other = defined(p for p in (REPO / "include").rglob("*.h") if "include/game/" not in str(p))
game_only = game - other
conn = sqlite3.connect(f"file:{harvest.KB}?mode=ro", uri=True)
solved = harvest.solved_sources(conn)          # the harvest's own SOLVED filter, latest source per function
ident = re.compile(r"\b[A-Za-z_]\w*\b")
flag_all, examples = [], {}
for (name,) in [(n,) for n in solved]:
    rows = [s for (s,) in conn.execute("select a.source_code from attempts a join functions f on f.addr=a.func_addr "
                                       "where f.name=? and a.exact=1 and a.source_code is not null", (name,))]
    hits = [set(ident.findall(re.sub(r"//[^\n]*|/\*.*?\*/", " ", s, flags=re.S))) & game_only for s in rows]
    if rows and all(hits):
        flag_all.append(name)
        examples[name] = sorted(set.intersection(*hits))[:4]
out = {"game_only_type_names": len(game_only), "solved_sources_checked": len(solved),
       "solved_using_game_only_types_in_every_exact_source": len(flag_all), "functions": examples}
(HERE / "game_type_leak.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "functions"}, indent=1))
print(list(examples.items())[:12])


def full_solved_audit():
    """Across the eval.status SOLVED set: functions whose every exact source either #includes a game/ header or
    uses a game-only type name. Counts only."""
    import sqlite3 as _s
    from eval import status as st
    c = _s.connect(f"file:{harvest.KB}?mode=ro", uri=True)
    q = c.execute
    from eval import matched as matched_mod  # noqa: F401  (status uses it)
    recovered = {n for (n,) in q("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                 "where a.exact=1 and (a.strategy like '%history-recovery%' or a.strategy like "
                                 "'%historical-provenance%' or a.strategy like '%symbol-restoration%')")}
    header = {n for (n,) in q("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                              "where a.exact=1 and a.strategy like '%project-header%'")}
    ref_types = st.reference_only_types(REPO)
    srcs = {}
    for name, source in q("select f.name, a.source_code from attempts a join functions f on f.addr=a.func_addr "
                          "where a.exact=1 and a.source_code is not null"):
        srcs.setdefault(name, []).append(re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.S))
    solved = {n for n, ss in srcs.items() if n not in recovered | header
              and not all(set(ident.findall(s)) & ref_types for s in ss)}
    inc = {n for n in solved if all('#include "game/' in s for s in srcs[n])}
    typ = {n for n in solved if all(set(ident.findall(s)) & game_only for s in srcs[n])}
    either = {n for n in solved if all('#include "game/' in s or set(ident.findall(s)) & game_only for s in srcs[n])}
    res = {"solved_with_logged_source": len(solved), "every_source_includes_game_header": len(inc),
           "every_source_uses_game_only_type": len(typ), "every_source_either": len(either),
           "clean_by_this_audit": len(solved - either)}
    (HERE / "solved_audit.json").write_text(json.dumps(res | {"either": sorted(either)}, indent=1))
    print(json.dumps(res, indent=1))


if len(sys.argv) > 1 and sys.argv[1] == "audit":
    full_solved_audit()
