"""Which guard excluded the 218, and what is actually left unsolved.

The panel builder refused every near miss. Two different things could have done that, and they mean
opposite things for where the remaining work is:
  * the function is IN the reference decomp (`src/**`)            -> genuinely solved, nothing to do
  * the KB's `functions.state` says matched but it is not in src/ -> a state column I must understand
                                                                    before trusting it either way
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
sys.path.insert(0, "/mnt/c/Code/gameDecomp")


def solved_names() -> set:
    names = set()
    for path in (REPO / "src").rglob("*.c"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names |= set(re.findall(r"(?m)^[A-Za-z_][\w \t*]*?\b(\w+)\s*\([^;{]*\)\s*\{", text))
    return names


solved = solved_names()
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    print("functions.state distribution:", dict(Counter(
        row[0] for row in conn.execute("select state from functions"))))
    print("functions with an exact attempt:", conn.execute(
        "select count(distinct func_addr) from attempts where coalesce(exact,0)=1").fetchone()[0])

    rows = conn.execute(
        "select a.func_addr, f.name, f.state, f.best_score, max(a.score) best "
        "from attempts a join functions f on f.addr = a.func_addr "
        "where coalesce(a.compiled,0)=1 "
        "  and a.func_addr not in (select func_addr from attempts where coalesce(exact,0)=1) "
        "group by a.func_addr").fetchall()
    by_state, by_src, neither, both = Counter(), 0, [], 0
    for addr, name, state, best_score, best in rows:
        in_src = bool(name) and name in solved
        by_state[str(state)] += 1
        by_src += int(in_src)
        both += int(in_src and str(state) == "matched")
        if not in_src:
            neither.append({"func_addr": addr, "name": name, "state": state,
                            "best": best, "best_score": best_score})
    print(f"\ncompiling-but-not-exact functions: {len(rows)}")
    print(f"  their states: {dict(by_state)}")
    print(f"  defined in src/**: {by_src}")
    print(f"  both matched-state AND in src: {both}")
    print(f"  NOT in src (whatever the state says): {len(neither)}")
    for row in neither[:25]:
        print(f"    {str(row['name']):38} state={str(row['state']):10} best={row['best']}")

    # AND WHAT IS ACTUALLY LEFT: functions the KB knows about with NO compiling attempt at all.
    never = conn.execute(
        "select count(*) from functions f where f.addr not in "
        "(select func_addr from attempts where coalesce(compiled,0)=1)").fetchone()[0]
    exact_attempt = conn.execute(
        "select count(*) from functions f where f.addr in "
        "(select func_addr from attempts where coalesce(exact,0)=1)").fetchone()[0]
    print(f"\nfunctions with NO compiling attempt: {never}")
    print(f"functions with an exact attempt:     {exact_attempt}")
    print(f"functions total:                     {conn.execute('select count(*) from functions').fetchone()[0]}")
finally:
    conn.close()
