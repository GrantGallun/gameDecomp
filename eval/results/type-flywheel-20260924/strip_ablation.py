"""Measurement for solved_audit.json's flagged functions: does the exact source still match without the game headers?

For each flagged function, its latest exact source with every `#include "game/..."` line removed is compiled in a
copied workspace (draft-reference-mining mirror, the function's recorded recipe). Outcomes: exact (the headers were
vestigial), compiles-not-exact, fails-to-compile (the source needs the headers: types, prototypes or macros). A
source that defines a game-only type LOCALLY is still flagged separately, since stripping includes cannot test it.
No KB writes. Counts only.

    python3 strip_ablation.py [--jobs 4] -> strip_ablation.json"""
import concurrent.futures, json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import harvest, pairs, mine
from game_type_leak import game_only, ident

INC = re.compile(r'^[ \t]*#[ \t]*include[ \t]+"game/[^"]+"[ \t]*\n', re.M)


def latest_exact(conn, name):
    return conn.execute("select a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                        "and a.exact=1 and a.source_code is not null order by a.id desc limit 1", (name,)).fetchone()[0]


def one(mirror, name, source):
    ws = pairs.workspace(mirror, name)
    stripped = INC.sub("", source)
    res = pairs.build(ws, "strip", stripped)
    target = (ws / "target_object_dump_normalized.s")
    exact = bool(res["compiled"] and target.exists() and mine.mask(res["dump"]) == mine.mask(target.read_text()))
    local_game_type = bool(set(ident.findall(re.sub(r"//[^\n]*|/\*.*?\*/", " ", stripped, flags=re.S))) & game_only)
    return {"function": name, "had_game_include": bool(INC.search(source)), "compiled": res["compiled"],
            "exact": exact, "score": res.get("score"), "still_uses_game_only_type": local_game_type,
            "error": None if res["compiled"] else (res.get("error") or "")[-300:]}


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    flagged = json.loads((HERE / "solved_audit.json").read_text())["either"]
    conn = sqlite3.connect(f"file:{harvest.KB}?mode=ro", uri=True)
    mirror = pairs.mirror_repo()
    todo = [(n, latest_exact(conn, n)) for n in flagged]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for r in pool.map(lambda a: one(mirror, *a), todo):
            rows.append(r)
    c = {"flagged": len(rows),
         "exact_without_game_includes_and_no_game_type": sum(r["exact"] and not r["still_uses_game_only_type"] for r in rows),
         "exact_but_defines_game_only_type_locally": sum(r["exact"] and r["still_uses_game_only_type"] for r in rows),
         "compiles_not_exact": sum(r["compiled"] and not r["exact"] for r in rows),
         "fails_to_compile_without_game_headers": sum(not r["compiled"] for r in rows)}
    (HERE / "strip_ablation.json").write_text(json.dumps({"counts": c, "rows": rows}, indent=1))
    print(json.dumps(c, indent=1))


if __name__ == "__main__" and sys.argv[1:2] != ["control"]:
    main()


def control():
    """Harness control: the UNMODIFIED latest exact sources must compile exact in the same harness."""
    flagged = json.loads((HERE / "solved_audit.json").read_text())["either"]
    conn = sqlite3.connect(f"file:{harvest.KB}?mode=ro", uri=True)
    mirror = pairs.mirror_repo()

    sources = {n: latest_exact(conn, n) for n in flagged}

    def run(name):
        ws = pairs.workspace(mirror, name)
        res = pairs.build(ws, "orig", sources[name])
        t = ws / "target_object_dump_normalized.s"
        return name, bool(res["compiled"] and t.exists() and mine.mask(res["dump"]) == mine.mask(t.read_text())), \
            (res.get("error") or "")[-200:]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(run, flagged))
    bad = [(n, e) for n, ok, e in rows if not ok]
    (HERE / "strip_control.json").write_text(json.dumps({"flagged": len(rows), "exact": len(rows) - len(bad),
                                                          "not_exact": bad}, indent=1))
    print("control:", len(rows) - len(bad), "of", len(rows), "exact unmodified;", bad[:3])


if len(sys.argv) > 1 and sys.argv[1] == "control":
    control()
