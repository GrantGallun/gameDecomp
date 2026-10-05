"""Does moving the C toward the key (the reference decomp) show up as score? CHECKING ONLY.

The reference is read here to judge the SCORE as a signal; nothing from it is written to a KB,
a prompt or a dataset (CLAUDE.md: ground truth checks the pipeline, it never feeds it).

For every logged parent -> child step in the campaign:
  d = normalized token edit distance between a candidate's body and the reference body, after
      alpha-renaming (locals, params, fields and types are numbered by first occurrence, so
      naming a variable differently costs nothing; called function names, keywords, operators
      and literal values are kept).
  delta d < 0 means the step moved TOWARD the key.
Then: score change x key-distance change, and how often each cell leads to a match.

The reference is ONE key among many (different C compiles to the same object), so the ruler
is calibrated first: how far do actual exact matches sit from it?

Attempts descended from a reference seed (repair_dataset.REFERENCE_SEED_MARKERS) start at the
key by construction and are reported separately.

    ~/decomp/sbk1/.venv/bin/python key_distance.py   (cwd holding campaign.sqlite)
"""
import collections
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval.repair_dataset import REFERENCE_SEED_MARKERS  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402

sys.setrecursionlimit(100000)
REFERENCE = Path("/home/grant/decomp/sbk1/src")
KEYWORDS = frozenset("""auto break case char const continue default do double else enum extern
float for goto if int long register return short signed sizeof static struct switch typedef
union unsigned void volatile while s8 u8 s16 u16 s32 u32 s64 u64 f32 f64 NULL""".split())
TOKEN = re.compile(r"0[xX][0-9a-fA-F]+[uUlL]*|\d+\.\d*(?:[eE][-+]?\d+)?[fF]?|\d+[uUlLfF]*"
                   r"|[A-Za-z_]\w*|->|\+\+|--|<<=?|>>=?|[<>=!&|^+\-*/%]=|&&|\|\||\S")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


# Structural mode: the same memory access written two ways must not count as distance. Matches
# often spell a field as raw byte arithmetic, `(*(s16 *)((unsigned char *)arg0 + 0x1C))`, where
# the reference says `arg0->offsetX` -- one access, ~12 tokens vs 3 (a 0.85 "far match" read by
# eye was this and nothing else). Both become `E->F`; casts are dropped (IDO's front end flattens
# them: noop_kinds.out, ido_stage_probe.py). Lexical, so an approximation of the front end.
_BYTE_PTR = r"\(\s*(?:u8|s8|char|unsigned\s+char|signed\s+char)\s*\*\s*\)"
_OFFSET_ACCESS = re.compile(
    r"\(?\s*\*\s*\(\s*[A-Za-z_][\w\s]*\*+\s*\)\s*\(\s*" + _BYTE_PTR +
    r"\s*&?\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*\+\s*(?:0[xX][0-9a-fA-F]+|\d+)\s*\)\s*\)?")
_FIELD = re.compile(r"(->|\.)\s*[A-Za-z_]\w*")
_TYPE_WORDS = r"(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|long|float|double|void|unsigned|signed|const|volatile|struct\s+\w+|[A-Z]\w*)"
_CAST = re.compile(r"(?<![\w\])\s])(\s*)\(\s*(?:" + _TYPE_WORDS + r"\s*)+(?:\*\s*)*\)"
                   r"|\(\s*" + _TYPE_WORDS + r"\s*\(\s*\*\s*\)\s*\([^()]*\)\s*\)")


def structural(body: str) -> str:
    previous = None
    while previous != body:
        previous = body
        body = _OFFSET_ACCESS.sub(r"\1->F", body)
    body = _FIELD.sub(r"->F", body)
    body = _CAST.sub(r"\1", body)
    return body


def canonical(source: str, name: str | None = None, structure: bool = False) -> tuple:
    """Body tokens of function ``name``, alpha-renamed. None when there is no body.

    Candidates are whole translation units: type context, externs and helpers precede the
    function. Starting at the first ``{`` scored every struct definition as "distance from the
    key" (a 0.96 "far match" was a 3-line body behind 40 lines of binary-derived structs), so
    the named definition is extracted first."""
    if name is not None and source:
        source = function_definitions(source).get(name)
    if not source or "{" not in source:
        return None
    body = COMMENT.sub(" ", source)
    body = body[body.index("{"):]
    if structure:
        body = structural(body)
    toks = TOKEN.findall(body)
    names, out = {}, []
    for i, t in enumerate(toks):
        if t[0].isalpha() or t[0] == "_":
            if t in KEYWORDS or (i + 1 < len(toks) and toks[i + 1] == "("
                                 and (i == 0 or toks[i - 1] not in ("->", "."))):
                out.append(t)              # keyword or called function: its identity matters
            else:
                out.append(names.setdefault(t, f"$v{len(names)}"))
        elif t[0].isdigit():
            try:
                out.append(str(int(t.rstrip("uUlL"), 0)))
            except ValueError:
                out.append(t)
        else:
            out.append(t)
    return tuple(out)


def main():
    keys = {}
    for path in REFERENCE.rglob("*.c"):
        try:
            for name, text in function_definitions(path.read_text(errors="replace")).items():
                keys.setdefault(name, canonical(text, structure=True))
        except Exception:
            continue
    db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
    names = dict(db.execute("select addr, name from functions"))
    att = {}
    for aid, addr, src, score, compiled, exact, strategy in db.execute(
            "select id, func_addr, source_code, score, coalesce(compiled,0), coalesce(exact,0), "
            "coalesce(strategy,'') from attempts"):
        att[aid] = (names.get(addr), src, score or 0.0, compiled, exact, strategy)
    parents, kids = collections.defaultdict(list), collections.defaultdict(list)
    for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
        parents[c].append(p)
        kids[p].append(c)

    seeded_memo = {}

    def seeded(n):
        if n in seeded_memo:
            return seeded_memo[n]
        seeded_memo[n] = False
        s = any(m in att.get(n, ("", "", 0, 0, 0, ""))[5] for m in REFERENCE_SEED_MARKERS) or \
            any(seeded(p) for p in parents.get(n, ()))
        seeded_memo[n] = s
        return s

    exact_memo = {}

    def exact_below(n):
        if n in exact_memo:
            return exact_memo[n]
        exact_memo[n] = False
        r = any(att[k][4] or exact_below(k) for k in kids.get(n, ()) if k in att)
        exact_memo[n] = r
        return r

    dist_memo = {}

    def dist(aid):
        if aid not in dist_memo:
            name, src = att[aid][0], att[aid][1]
            key, cand = keys.get(name), canonical(src, name, structure=True)
            dist_memo[aid] = (None if key is None or cand is None
                              else Levenshtein.normalized_distance(cand, key))
        return dist_memo[aid]

    # 1. Calibrate the ruler: where do matches sit relative to the reference?
    calib = collections.defaultdict(list)
    for aid, a in att.items():
        if a[3] and a[0] in keys:
            d = dist(aid)
            if d is not None:
                calib[("seeded" if seeded(aid) else "clean",
                       "exact" if a[4] else "not exact")].append(d)
    print("RULER: normalized token distance to the reference body (0 = identical)")
    for k in sorted(calib):
        v = sorted(calib[k])
        q = lambda f: round(v[int(f * (len(v) - 1))], 3)
        print(f"  {k[0]:7s} {k[1]:9s} n={len(v):7d}  p10 {q(.1)}  median {q(.5)}  p90 {q(.9)}"
              f"  share at 0: {sum(1 for x in v if x == 0) / len(v):.1%}")

    # 2. Steps: score change x key-distance change.
    cells = collections.defaultdict(lambda: [0, 0])
    corr = collections.Counter()
    for p, cs in kids.items():
        if p not in att or not att[p][3] or seeded(p):
            continue
        dp = dist(p)
        if dp is None:
            continue
        for c in cs:
            if c not in att or not att[c][3] or att[c][4]:
                continue
            dc = dist(c)
            if dc is None:
                continue
            ds = att[c][2] - att[p][2]
            s = "score up" if ds > 0 else "score same" if ds == 0 else "score down"
            k = "closer" if dc < dp else "same dist" if dc == dp else "farther"
            cell = cells[(s, k)]
            cell[0] += 1
            cell[1] += exact_below(c)
            if ds != 0 and dc != dp:
                corr["agree" if (ds > 0) == (dc < dp) else "disagree"] += 1
    # Controls: only children the search expanded (a node nobody searched from cannot lead
    # anywhere), and only functions that matched somewhere (flat steps pile up on near-done ones).
    matched_fns = {a[0] for a in att.values() if a[4]}
    ctl = collections.defaultdict(lambda: [0, 0])
    for p, cs in kids.items():
        if p not in att or not att[p][3] or seeded(p) or att[p][0] not in matched_fns:
            continue
        dp = dist(p)
        if dp is None:
            continue
        for c in cs:
            if c not in att or not att[c][3] or att[c][4] or not kids.get(c):
                continue
            dc = dist(c)
            if dc is None:
                continue
            ds = att[c][2] - att[p][2]
            s = "score up" if ds > 0 else "score same" if ds == 0 else "score down"
            k = "closer" if dc < dp else "same dist" if dc == dp else "farther"
            ctl[(s, k)][0] += 1
            ctl[(s, k)][1] += exact_below(c)
    print("\nCONTROLLED (expanded children, functions that matched): count, led to a match")
    for s in ("score up", "score same", "score down"):
        for k in ("closer", "same dist", "farther"):
            n, e = ctl[(s, k)]
            print(f"  {s:10s} {k:9s} {n:8d}   {100 * e / max(n, 1):5.2f}%")
    print("\nSTEPS (clean lineage, both compiled, child not itself exact): count, led to a match")
    for s in ("score up", "score same", "score down"):
        for k in ("closer", "same dist", "farther"):
            n, e = cells[(s, k)]
            print(f"  {s:10s} {k:9s} {n:8d}   {100 * e / max(n, 1):5.2f}%")
    total = corr["agree"] + corr["disagree"]
    print(f"\nwhen both move: score and key distance agree on direction "
          f"{corr['agree']}/{total} = {corr['agree'] / max(total, 1):.1%}")
    closer = sum(cells[(s, 'closer')][0] for s in ('score up', 'score same', 'score down'))
    print(f"of steps toward the key: score up {cells[('score up','closer')][0] / max(closer,1):.1%}, "
          f"same {cells[('score same','closer')][0] / max(closer,1):.1%}, "
          f"down {cells[('score down','closer')][0] / max(closer,1):.1%}")
    print(json.dumps({"functions_with_reference": len(keys),
                      "attempts_measured": sum(1 for v in dist_memo.values() if v is not None)}))


if __name__ == "__main__":
    main()
