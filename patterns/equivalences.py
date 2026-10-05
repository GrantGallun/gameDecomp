"""Empirical IDO spelling patterns mined from logged normalized-listing comparisons.

REVIEW CORRECTION (2026-09-28)
------------------------------
The legacy schema calls these patterns "equivalence" and their labels "same_object".
They are not object certificates: the September 27 byte check found 3/200 equal
diffs with unequal objects. Local edit abstraction also omits operand types and
whole-function context. Use this index only for advice/predictions, never to skip
a candidate. The pilot now compiles predictions. Historical mining/evaluation
fields are retained so old receipts remain interpretable.

WHAT A NO-OP EDGE PROVES, AND WHAT IT DOES NOT
----------------------------------------------
An attempt edge with identical normalized instruction diffs establishes only listing equality
in that recorded context. It does not establish identical section bytes or relocations, nor prove the spellings are
interchangeable everywhere: register allocation and scheduling are whole-function, so an edit that
is inert in one context can move code in another.

So a RULE is an abstracted edit (identifiers and constants replaced by placeholders, consistently
across both sides, both directions pooled) that was a no-op on many edges across several functions
and never changed the observed listing in the mining sample. A pattern with even one
listing-changing edge is context-dependent. Skipping is the dangerous direction -- a wrong
skip hides a real move -- so ``evaluate`` measures a rule set's precision on held-out translation
units. Even perfect measured precision cannot prove applicability to unseen typing contexts.

WHY
---
On the campaign, 96,120 of 106,975 flat steps were no-ops, and on the branch-point pilot's canary
82% of the model's alternatives compiled to the identical object. Most apparent choices are not
choices to IDO. Knowing the equivalences lets both proposal sources -- the deterministic search
and the model -- spend compiles only on spellings that can change code.

Deterministic and LLM-free: mined from logged compiler verdicts only.
"""
from __future__ import annotations

import collections
import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
MAX_HUNKS = 2            # an edit touching more places is not a local spelling change
MAX_HUNK_TOKENS = 40
CONTEXT_TOKENS = 1
MERGE_GAP = 3            # equal tokens allowed inside one hunk

# Type and qualifier words are part of a spelling, not names to abstract.
KEEP_WORDS = frozenset("""
    s8 u8 s16 u16 s32 u32 s64 u64 f32 f64 int char short long unsigned signed float double void
    register volatile const static extern struct union enum typedef sizeof if else for while do
    return goto break continue switch case default M2C_UNK
""".split())

COMMUTATIVE = frozenset({"+", "*", "&", "|", "^", "==", "!="})
BITWISE = frozenset({"&", "|", "^", "<<", ">>", "&=", "|=", "^=", "<<=", ">>="})
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_TOKEN = re.compile(r"""
    0[xX][0-9a-fA-F]+[uUlL]* | \d+\.\d*(?:[eE][-+]?\d+)?[fF]? | \d+[uUlL]*   # numbers
  | [A-Za-z_]\w*                                                              # words
  | "(?:\\.|[^"\\])*" | '(?:\\.|[^'\\])*'                                     # literals
  | ->|\+\+|--|<<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=                  # operators
  | \S                                                                        # anything else
""", re.X)


def tokens(source: str) -> list[str]:
    return _TOKEN.findall(_COMMENT.sub(" ", source or ""))


def _abstract(before: list[str], after: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Consistent placeholders across BOTH sides: `x = x + 1` / `x += 1` -> I0 = I0 + 1 / I0 += 1.
    0 and 1 stay literal (they change instruction selection); other numbers become N0, N1..."""
    names: dict[str, str] = {}

    def side(seq):
        out = []
        for index, tok in enumerate(seq):
            prev = seq[index - 1] if index else ""
            nxt = seq[index + 1] if index + 1 < len(seq) else ""
            if re.fullmatch(r"[A-Za-z_]\w*", tok) and tok not in KEEP_WORDS:
                out.append(names.setdefault(tok, f"I{sum(1 for v in names.values() if v[0] == 'I')}"))
            elif re.fullmatch(r"0[xX][0-9a-fA-F]+[uUlL]*|\d[\w.]*", tok):
                # 0/1 change instruction selection; a mask or shift amount IS the meaning
                # (`x & 0xFF` is a no-op only on a u8): both stay literal. The first held-out
                # evaluation's wrong skips were masks with abstracted constants.
                if tok in ("0", "1", "0x0", "0x1") or prev in BITWISE or nxt in BITWISE:
                    out.append(tok)
                else:
                    out.append(names.setdefault(tok, f"N{sum(1 for v in names.values() if v[0] == 'N')}"))
            else:
                out.append(tok)
        return tuple(out)
    return side(before), side(after)


@dataclass(frozen=True)
class Edit:
    """One edge's source change as abstracted hunks, direction-free."""
    hunks: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]

    @property
    def key(self) -> str:
        return hashlib.sha256(json.dumps(self.hunks).encode()).hexdigest()[:16]

    def render(self) -> str:
        """Each side's hunks joined by `...`: `( ( ... ) + N0 )  <=>  ( N0 + ( ... ) )`."""
        left = " ... ".join(" ".join(a) for a, _ in self.hunks)
        right = " ... ".join(" ".join(b) for _, b in self.hunks)
        return f"{left}  <=>  {right}"


def edit_of(parent: str, child: str) -> Edit | None:
    """The abstracted local edit from parent to child, or None when it is not local
    (too many or too large hunks) or there is no change at the token level."""
    pairs = directed_hunks(parent, child)
    if pairs is None:
        return None
    # Direction-free: A->B and B->A are one equivalence claim.
    forward = tuple(pairs)
    backward = tuple((y, x) for x, y in pairs)
    return Edit(min(forward, backward))


def directed_hunks(parent: str, child: str):
    """Abstracted hunks in the parent -> child direction, or None when the edit is not local.

    `edit_of` pools both directions for equivalence claims; a repair template (solver.rule_miner, Engine B) needs to
    know which side was the improvement."""
    a, b = tokens(parent), tokens(child)
    # Trim the common prefix/suffix first: exact for a local edit, and it keeps SequenceMatcher
    # (quadratic worst case) off whole-function token lists across ~250k edges.
    limit = min(len(a), len(b))
    pre = 0
    while pre < limit and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < limit - pre and a[len(a) - 1 - suf] == b[len(b) - 1 - suf]:
        suf += 1
    matcher = difflib.SequenceMatcher(None, a[pre:len(a) - suf], b[pre:len(b) - suf],
                                      autojunk=False)
    # One spelling change fragments into several token opcodes (`arr[i]` -> `*(arr + i)` is an
    # insert and two replaces); changes separated by at most MERGE_GAP equal tokens are one hunk.
    spans: list[list[int]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        i1, i2, j1, j2 = i1 + pre, i2 + pre, j1 + pre, j2 + pre
        if spans and i1 - spans[-1][1] <= MERGE_GAP and j1 - spans[-1][3] <= MERGE_GAP:
            spans[-1][1], spans[-1][3] = i2, j2
        else:
            spans.append([i1, i2, j1, j2])
    hunks = []
    for i1, i2, j1, j2 in spans:
        if len(hunks) == MAX_HUNKS or max(i2 - i1, j2 - j1) > MAX_HUNK_TOKENS:
            return None
        lo_a, hi_a = max(0, i1 - CONTEXT_TOKENS), min(len(a), i2 + CONTEXT_TOKENS)
        lo_b, hi_b = max(0, j1 - CONTEXT_TOKENS), min(len(b), j2 + CONTEXT_TOKENS)
        hunks.append((tuple(a[lo_a:hi_a]), tuple(b[lo_b:hi_b])))
    if not hunks:
        return None
    # Placeholders must be consistent across all hunks of one edit and across both sides.
    flat_a = [t for h in hunks for t in h[0] + ("\x00",)]
    flat_b = [t for h in hunks for t in h[1] + ("\x00",)]
    abs_a, abs_b = _abstract(flat_a, flat_b)

    def split(seq):
        out, cur = [], []
        for t in seq:
            if t == "\x00":
                out.append(tuple(cur))
                cur = []
            else:
                cur.append(t)
        return out
    return list(zip(split(abs_a), split(abs_b)))


@dataclass
class Evidence:
    noop: int = 0
    changed: int = 0
    functions: set = field(default_factory=set)
    changed_functions: set = field(default_factory=set)
    examples: list = field(default_factory=list)          # edge ids, for citation
    counterexamples: list = field(default_factory=list)


def mine(rows, *, split_of=None, keep_split: str | None = None) -> dict[str, dict]:
    """``rows``: iterable of (edge_id, function, tu, parent_src, child_src, same_object: bool).

    With ``split_of``/``keep_split`` only rows whose TU falls in that split are counted, so rules
    can be mined on one split and evaluated on another."""
    table: dict[str, Evidence] = collections.defaultdict(Evidence)
    edits: dict[str, Edit] = {}
    for edge_id, function, tu, parent, child, same in rows:
        if split_of is not None and split_of(tu, function) != keep_split:
            continue
        edit = edit_of(parent, child)
        if edit is None:
            continue
        edits[edit.key] = edit
        _count(table[edit.key], edge_id, function, same)
    return {key: {"edit": edits[key], "evidence": ev} for key, ev in table.items()}


FAMILY_CHANGE_LIMIT = 0.05
UNSOUND_FAMILIES = frozenset({"identifier-swap"})   # see family(); never confirmed


def family(edit: Edit) -> str | None:
    """Families of edits whose members share a compiler mechanism, so one member's safety is not
    independent of the others'.

    decl-reorder     a pure permutation of local declarations: the `decl_order` register lever.
    compound-reorder a pure permutation spanning two hunks, i.e. operands that are themselves
                     subexpressions: evaluation order, which feeds register allocation.
    Found by the held-out evaluation: every remaining wrong skip at the strictest threshold was a
    member of one of these two, specialisations that looked safe only because they were rare."""
    left = sorted(t for a, _ in edit.hunks for t in a)
    right = sorted(t for _, b in edit.hunks for t in b)
    if left != right:
        return None
    # Swapping two identifiers is a local RENAMING: inert in a prototype (where every observation
    # of `s32 I0, s32 I1` <=> `s32 I1, s32 I0` came from), a different program in a definition
    # whose body still uses the old names. Unsound as a local rule however clean its statistics.
    moved = [(x, y) for a, b in edit.hunks for x, y in zip(a, b) if x != y]
    if moved and all(re.fullmatch(r"I\d+", x) and re.fullmatch(r"I\d+", y) for x, y in moved):
        return "identifier-swap"
    if any(t in KEEP_WORDS and t not in ("sizeof", "return", "if", "else", "for", "while", "do")
           for t in left) and ";" in left:
        return "decl-reorder"
    if len(edit.hunks) > 1:
        # Only reordering the operands of a COMMUTATIVE operator is a candidate equivalence.
        # Swapped subscripts, arguments or parenthesised constants are permutations of tokens but
        # different programs; lumping them in made the constant-operand family change code 14% of
        # the time and excluded its safe members. They get their own family.
        # Operators from the CHANGED tokens only: each hunk carries CONTEXT_TOKENS of context per
        # side, and an `=` just outside the edit is not the operator being commuted.
        inner = [t for a, _ in edit.hunks
                 for t in a[CONTEXT_TOKENS:len(a) - CONTEXT_TOKENS]]
        operators = {t for t in inner
                     if not re.fullmatch(r"[IN]\d+|0|1|0x[01]|[()\[\],;.]|\.\.\.", t)}
        if not operators or not operators <= COMMUTATIVE:
            return "swap"
        # Split by what moves. A constant operand folds into an immediate and needs no register;
        # a variable operand's evaluation order is what moves register allocation. One family for
        # both excluded the largest safe rule (`((...) + N0)` <=> `(N0 + (...))`, 3,507 no-ops in
        # 135 functions) because `((...) + I0)` members change code.
        atoms = {t for a, _ in edit.hunks for t in a if re.fullmatch(r"[IN]\d+|0|1|0x[01]", t)}
        return ("compound-commute:var" if any(t.startswith("I") for t in atoms)
                else "compound-commute:const")
    return None


def classify(ev: Evidence, *, min_noop: int, min_functions: int) -> str:
    if ev.noop and ev.changed:
        return "context-dependent"
    if ev.noop >= min_noop and len(ev.functions) >= min_functions:
        return "equivalence"
    if ev.noop:
        return "equivalence-candidate"      # too little support to act on
    return "code-changing"


def rules(mined: dict[str, dict], *, min_noop: int = 5, min_functions: int = 3) -> list[dict]:
    families: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for entry in mined.values():
        fam = family(entry["edit"])
        if fam:
            families[fam][0] += entry["evidence"].noop
            families[fam][1] += entry["evidence"].changed
    unsafe = {fam for fam, (noop, changed) in families.items()
              if changed > FAMILY_CHANGE_LIMIT * (noop + changed)} | UNSOUND_FAMILIES
    out = []
    for key, entry in mined.items():
        ev, edit = entry["evidence"], entry["edit"]
        kind = classify(ev, min_noop=min_noop, min_functions=min_functions)
        fam = family(edit)
        if kind == "equivalence" and fam in unsafe:
            kind = "family-context-dependent"
        out.append({"key": key, "pattern": edit.render(), "hunks": edit.hunks, "family": fam,
                    "family_evidence": families.get(fam) if fam else None,
                    "class": kind,
                    "noop": ev.noop, "changed": ev.changed, "functions": len(ev.functions),
                    "changed_functions": len(ev.changed_functions),
                    "examples": ev.examples, "counterexamples": ev.counterexamples})
    return sorted(out, key=lambda r: (r["class"] != "equivalence", -r["noop"], r["key"]))


class Index:
    """Empirical local spelling predictions, suitable for advice, not hard skips."""

    def __init__(self, confirmed: list[dict]):
        self.rules = {r["key"]: r for r in confirmed if r["class"] == "equivalence"}

    @classmethod
    def load(cls, path: Path) -> "Index":
        data = json.loads(Path(path).read_text())
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"equivalence file schema {data.get('schema_version')} != {SCHEMA_VERSION}")
        return cls(data["rules"])

    def predicts_noop(self, parent: str, child: str) -> dict | None:
        """A matching statistical rule, not proof of equal objects in this context."""
        edit = edit_of(parent, child)
        return self.rules.get(edit.key) if edit is not None else None

    def prompt_block(self, limit: int = 15) -> str:
        top = sorted(self.rules.values(), key=lambda r: -r["noop"])[:limit]
        if not top:
            return ""
        lines = ["\nOBSERVED SPELLING PATTERNS (I0/N0 are placeholders). "
                 "These often left the normalized listing unchanged in sampled contexts. "
                 "Types and surrounding code can change the result; these are hypotheses, "
                 "not equivalence proofs. Compile proposed alternatives:"]
        lines += [f"- {r['pattern']}   ({r['noop']} no-op edges, {r['functions']} functions)"
                  for r in top]
        return "\n".join(lines) + "\n"


def evaluate(train_rules: list[dict], held_out_rows) -> dict:
    """Precision and recall of the confirmed-equivalence filter on held-out edges.

    precision: of held-out edges a rule would SKIP, the share that really were no-ops (a miss here
    is a real move hidden). recall: of held-out no-ops, the share a rule would have skipped (the
    compiles saved)."""
    index = Index(train_rules)
    skipped = skipped_noop = noop_total = edges = 0
    wrong = []
    for edge_id, function, tu, parent, child, same in held_out_rows:
        edges += 1
        noop_total += same
        rule = index.predicts_noop(parent, child)
        if rule is None:
            continue
        skipped += 1
        skipped_noop += same
        if not same and len(wrong) < 20:
            wrong.append({"edge": edge_id, "function": function, "rule": rule["pattern"]})
    return {"held_out_edges": edges, "held_out_noops": noop_total, "would_skip": skipped,
            "precision": (skipped_noop / skipped) if skipped else None,
            "recall": (skipped_noop / noop_total) if noop_total else None,
            "wrong_skips": wrong}


# --- mining the campaign ---------------------------------------------------------------------

def campaign_rows(db_path: Path):
    """Stream (edge_id, function, tu, parent_src, child_src, same_object) for same-function edges
    whose endpoints both compiled. Streams: the sources of ~250k edges do not fit comfortably."""
    import sqlite3
    from solver.branch_points import diff_body
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    sql = """
        select e.rowid, f.name, t.name, p.source_code, c.source_code, p.diff_summary, c.diff_summary
        from attempt_edges e
        join attempts p on p.id = e.parent_attempt_id
        join attempts c on c.id = e.child_attempt_id
        join functions f on f.addr = c.func_addr
        left join tus t on t.id = f.tu_id
        where p.func_addr = c.func_addr and coalesce(p.compiled,0)=1 and coalesce(c.compiled,0)=1
          and coalesce(p.exact,0)=0 and coalesce(c.exact,0)=0
          and p.source_code is not null and c.source_code is not null"""
    for eid, name, tu, psrc, csrc, pdiff, cdiff in conn.execute(sql):
        if psrc == csrc:
            continue          # a recompile of the same source is not an edit (reverify rows)
        yield eid, name, tu, psrc, csrc, diff_body(pdiff) == diff_body(cdiff)


def main(argv=None) -> int:
    import argparse
    import time
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-noop", type=int, default=5)
    parser.add_argument("--min-functions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260920)
    args = parser.parse_args(argv)
    from eval.repair_dataset import split_for
    started = time.time()
    split = lambda tu, function: "test" if split_for(tu, function, args.seed) == "test" else "train"
    # One pass: mine the train split, keep test rows' edits for evaluation, and mine everything.
    train_rows, test_rows = [], []
    everything = collections.defaultdict(Evidence)
    edits: dict[str, Edit] = {}
    counts = collections.Counter()
    for row in campaign_rows(args.db):
        counts["edges"] += 1
        counts["noop"] += row[5]
        edit = edit_of(row[3], row[4])
        if edit is None:
            counts["not-local"] += 1
            continue
        slim = (row[0], row[1], row[2], edit, row[5])
        (test_rows if split(row[2], row[1]) == "test" else train_rows).append(slim)
        edits[edit.key] = edit
        ev = everything[edit.key]
        _count(ev, row[0], row[1], row[5])
    def rules_from(rows):
        table = collections.defaultdict(Evidence)
        for eid, name, _tu, edit, same in rows:
            _count(table[edit.key], eid, name, same)
        return rules({k: {"edit": edits[k], "evidence": v} for k, v in table.items()},
                     min_noop=args.min_noop, min_functions=args.min_functions)
    train_rules = rules_from(train_rows)
    confirmed = {r["key"] for r in train_rules if r["class"] == "equivalence"}
    skipped = skipped_noop = held_noop = 0
    wrong = []
    for eid, name, _tu, edit, same in test_rows:
        held_noop += same
        if edit.key in confirmed:
            skipped += 1
            skipped_noop += same
            if not same and len(wrong) < 20:
                wrong.append({"edge": eid, "function": name, "rule": edit.render()})
    evaluation = {"mined_on": "train+dev TUs", "evaluated_on": "test TUs",
                  "held_out_local_edges": len(test_rows), "held_out_noops": held_noop,
                  "confirmed_rules_from_train": len(confirmed), "would_skip": skipped,
                  "precision": skipped_noop / skipped if skipped else None,
                  "recall": skipped_noop / held_noop if held_noop else None,
                  "wrong_skips": wrong}
    final = rules({k: {"edit": edits[k], "evidence": v} for k, v in everything.items()},
                  min_noop=args.min_noop, min_functions=args.min_functions)
    by_class = collections.Counter(r["class"] for r in final)
    payload = {"schema_version": SCHEMA_VERSION, "db": str(args.db),
               "thresholds": {"min_noop": args.min_noop, "min_functions": args.min_functions},
               "counts": dict(counts), "rules_by_class": dict(by_class),
               "held_out_evaluation": evaluation, "seconds": round(time.time() - started, 1),
               "rules": [r for r in final if r["class"] in ("equivalence", "context-dependent",
                                                           "family-context-dependent")]
               + [r for r in final if r["class"] == "code-changing"][:200]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, default=list) + "\n")
    print(json.dumps({k: payload[k] for k in ("counts", "rules_by_class", "seconds")}
                     | {"held_out": {k: evaluation[k] for k in evaluation if k != "wrong_skips"}},
                     indent=2))
    return 0


def _count(ev: Evidence, edge_id, function, same) -> None:
    if same:
        ev.noop += 1
        ev.functions.add(function)
        if len(ev.examples) < 8:
            ev.examples.append(edge_id)
    else:
        ev.changed += 1
        ev.changed_functions.add(function)
        if len(ev.counterexamples) < 8:
            ev.counterexamples.append(edge_id)


if __name__ == "__main__":
    raise SystemExit(main())
