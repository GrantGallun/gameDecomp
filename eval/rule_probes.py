"""Probe the IDO rules behind the edit generators: minimal C variant pairs, compiled with the game's own recipes.

A generator is a fix; the rule is the logic -- WHEN a spelling difference survives compilation. A rule inferred from
planted cases says only that it happened there. Probing fresh, minimal constructs in several contexts at -O1 and
-O2 measures the conditions (CLAUDE.md, "Probe, don't assume"). Nothing here asserts an expectation: each pair is
measured `same` or `differ` and written to a receipt that tests and catalog entries cite.

    python3 -m eval.rule_probes [--out eval/results/rule-probes-20261002/probes.json]

Recipes: -O2 = game code (`eval.repair_dataset_synth.DEFAULT_TARGET`), -O1 = libultra io (`sptaskyield.o`).
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = Path.home() / "decomp/sbk1"
RECIPES = {"O2": "build/src/race/ui/race_ui_effects.o", "O1": "build/src/ultra/io/sptaskyield.o"}

PRELUDE = """typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16;
typedef int s32; typedef unsigned int u32;
typedef struct { s16 a; s16 b; s16 c; s16 d; s32 w; s32 v; u8 flag; s8 count; } Obj;
extern s32 g(s32); extern s32 h(s32, s32); extern void k(void); extern void m(Obj *);
extern u8 gFlag; extern s32 gWord;
"""

# family -> list of (context, {variant: body}); every body defines `s32 probe(Obj *o, s32 p, s32 q)`.
SIG = "s32 probe(Obj *o, s32 p, s32 q)"
FAMILIES: dict[str, list[tuple[str, dict[str, str]]]] = {
    # m2c's result temporary: `t = E; return t;` against `return E;`
    "return_temp": [
        ("call value", {"direct": "{ return g(0x400); }",
                        "temp": "{ s32 t; t = g(0x400); return t; }"}),
        ("arithmetic value", {"direct": "{ return p & 0x1FFFFFFF; }",
                              "temp": "{ s32 t; t = p & 0x1FFFFFFF; return t; }"}),
        ("field load", {"direct": "{ return o->w; }",
                        "temp": "{ s32 t; t = o->w; return t; }"}),
        ("call then more code", {"direct": "{ s32 r = g(p); k(); return r; }",
                                 "temp": "{ s32 r; s32 t; t = g(p); r = t; k(); return r; }"}),
    ],
    # an empty arm: `if (!(c)) {} else {B}` against `if (c) {B}`
    "empty_then_arm": [
        ("global test, call body", {"plain": "{ if (gFlag == 0) { k(); } return 0; }",
                                    "empty": "{ if (!(gFlag == 0)) { } else { k(); } return 0; }"}),
        ("global test, store body", {"plain": "{ if (gFlag == 0) { o->w = 1; } return 0; }",
                                     "empty": "{ if (!(gFlag == 0)) { } else { o->w = 1; } return 0; }"}),
        ("param test, call body", {"plain": "{ if (p == 0) { k(); } return 0; }",
                                   "empty": "{ if (!(p == 0)) { } else { k(); } return 0; }"}),
        ("body ends in return", {"plain": "{ if (gFlag == 0) { k(); return 1; } return 0; }",
                                 "empty": "{ if (!(gFlag == 0)) { } else { k(); return 1; } return 0; }"}),
        ("nested if in body", {"plain": "{ if (gFlag == 0) { o->count++; if (o->count == 12) { k(); } } return 0; }",
                               "empty": "{ if (!(gFlag == 0)) { } else { o->count++; if (o->count == 12) { k(); } } return 0; }"}),
        ("code after the if", {"plain": "{ if (gFlag == 0) { k(); } m(o); return 0; }",
                               "empty": "{ if (!(gFlag == 0)) { } else { k(); } m(o); return 0; }"}),
        # The two planted cases where the empty arm was VISIBLE combined these; the contexts separate them.
        ("&& test, body returns", {"plain": "{ if (p >= 5 && p < 9) { return p & 0xFF; } return g(p); }",
                                   "empty": "{ if (!(p >= 5 && p < 9)) { } else { return p & 0xFF; } return g(p); }"}),
        ("&& test, call body", {"plain": "{ if (p >= 5 && p < 9) { k(); } return 0; }",
                                "empty": "{ if (!(p >= 5 && p < 9)) { } else { k(); } return 0; }"}),
        ("simple test, body returns, code after", {
            "plain": "{ if (p == 0) { return p & 0xFF; } return g(p); }",
            "empty": "{ if (!(p == 0)) { } else { return p & 0xFF; } return g(p); }"}),
        ("return inside nested if, code after", {
            "plain": "{ if (gFlag == 0) { o->count += 1; if (o->count == 12) { return g(p); } } "
                     "if (o->count < 0) { o->count = 0; } return h(p, q); }",
            "empty": "{ if (!(gFlag == 0)) { } else { o->count += 1; if (o->count == 12) { return g(p); } } "
                     "if (o->count < 0) { o->count = 0; } return h(p, q); }"}),
        ("nested if without return, code after", {
            "plain": "{ if (gFlag == 0) { o->count += 1; if (o->count == 12) { k(); } } "
                     "if (o->count < 0) { o->count = 0; } return h(p, q); }",
            "empty": "{ if (!(gFlag == 0)) { } else { o->count += 1; if (o->count == 12) { k(); } } "
                     "if (o->count < 0) { o->count = 0; } return h(p, q); }"}),
    ],
    "empty_else_arm": [
        ("global test, call body", {"plain": "{ if (gFlag == 0) { k(); } return 0; }",
                                    "empty": "{ if (gFlag == 0) { k(); } else { } return 0; }"}),
    ],
    # operand order of a commutative operator
    "commute_operands": [
        ("two field loads, s16", {"ab": "{ return h((s16) (o->a + o->c), 0); }",
                                  "ba": "{ return h((s16) (o->c + o->a), 0); }"}),
        ("two field loads in one call, two sums", {"ab": "{ return h((s16) (o->a + o->c), (s16) (o->b + o->d)); }",
                                                   "ba": "{ return h((s16) (o->a + o->c), (s16) (o->d + o->b)); }"}),
        ("two field loads, s32", {"ab": "{ return o->w + o->v; }", "ba": "{ return o->v + o->w; }"}),
        ("field + param", {"ab": "{ return o->w + p; }", "ba": "{ return p + o->w; }"}),
        ("two params", {"ab": "{ return p + q; }", "ba": "{ return q + p; }"}),
        ("field * field", {"ab": "{ return o->w * o->v; }", "ba": "{ return o->v * o->w; }"}),
        ("field & constant", {"ab": "{ return o->w & 0xFF; }", "ba": "{ return 0xFF & o->w; }"}),
    ],
    # a comparison written mirrored
    "mirror_comparison": [
        ("field vs constant", {"ab": "{ if (o->w < 5) { k(); } return 0; }",
                               "ba": "{ if (5 > o->w) { k(); } return 0; }"}),
        ("field vs field", {"ab": "{ if (o->w < o->v) { k(); } return 0; }",
                            "ba": "{ if (o->v > o->w) { k(); } return 0; }"}),
        ("param vs param", {"ab": "{ if (p <= q) { k(); } return 0; }",
                            "ba": "{ if (q >= p) { k(); } return 0; }"}),
    ],
    # semantically equal spellings of one operation: which does IDO collapse?
    "operator_spellings": [
        ("< N vs <= N-1, field", {"a": "{ if (o->w < 0x21) { k(); } return 0; }",
                                  "b": "{ if (o->w <= 0x20) { k(); } return 0; }"}),
        ("> N vs >= N+1, param", {"a": "{ if (p > 9) { k(); } return 0; }",
                                  "b": "{ if (p >= 10) { k(); } return 0; }"}),
        ("x - 3 vs x + -3", {"a": "{ return p - 3; }", "b": "{ return p + -3; }"}),
        ("x * 2 vs x << 1", {"a": "{ return p * 2; }", "b": "{ return p << 1; }"}),
        ("x * 2 vs x + x", {"a": "{ return p * 2; }", "b": "{ return p + p; }"}),
        ("signed x / 2 vs x >> 1", {"a": "{ return p / 2; }", "b": "{ return p >> 1; }"}),
        ("x != 0 vs truth test", {"a": "{ if (p != 0) { k(); } return 0; }",
                                  "b": "{ if (p) { k(); } return 0; }"}),
        ("!(x == 0) vs x != 0", {"a": "{ if (!(p == 0)) { k(); } return 0; }",
                                 "b": "{ if (p != 0) { k(); } return 0; }"}),
        ("x = x + 1 vs x += 1, field", {"a": "{ o->w = o->w + 1; return 0; }", "b": "{ o->w += 1; return 0; }"}),
        ("x & 0xFF vs (u8) x", {"a": "{ return p & 0xFF; }", "b": "{ return (u8) p; }"}),
    ],
    # order of two adjacent stores to different fields
    "store_order": [
        ("two constants", {"ab": "{ o->a = -0x24; o->b = -0x38; return 0; }",
                           "ba": "{ o->b = -0x38; o->a = -0x24; return 0; }"}),
        ("zero stores", {"ab": "{ o->c = 0; o->d = 0; return 0; }", "ba": "{ o->d = 0; o->c = 0; return 0; }"}),
        ("read-modify-write", {"ab": "{ o->a -= 0x30; o->b += 3; return 0; }",
                               "ba": "{ o->b += 3; o->a -= 0x30; return 0; }"}),
        ("stores before a call", {"ab": "{ o->a = 1; o->b = 2; m(o); return 0; }",
                                  "ba": "{ o->b = 2; o->a = 1; m(o); return 0; }"}),
    ],
}


def compile_listing(resolved: dict, source: str, work: Path, tag: str) -> list[str] | None:
    return compile_listing_or_error(resolved, source, work, tag)[0]


def compile_listing_or_error(resolved: dict, source: str, work: Path, tag: str) -> tuple[list[str] | None, str]:
    """(listing, "") or (None, the compiler's own error text)."""
    from eval import repair_dataset_synth as rds
    unit = rds.compile_unit(REPO, resolved, tag, source, work)
    if not unit["compiled"]:
        return None, (unit.get("stderr") or "")[-600:]
    return _listing(unit, work, tag), ""


def _listing(unit: dict, work: Path, tag: str) -> list[str]:
    from eval import repair_dataset_synth as rds
    obj = work / f"{tag}.o"
    obj.write_bytes(unit["object"])
    listing, _n = rds.disassemble(obj, "probe")
    # Keep mnemonic + operands only, so relocation addresses never decide `same`.
    return [line.split(":", 1)[-1].strip() for line in listing.splitlines() if line.strip()]


def run(only=None) -> dict:
    from eval import repair_dataset_synth as rds
    recipes = {opt: rds.resolve_recipe(REPO, target) for opt, target in RECIPES.items()}
    results = {"recipes": {opt: r["provenance"] for opt, r in recipes.items()}, "families": {}}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for family, contexts in FAMILIES.items():
            if only and family not in only:
                continue
            rows = []
            for context, variants in contexts:
                for opt, recipe in recipes.items():
                    listings = {}
                    for name, body in variants.items():
                        listings[name] = compile_listing(recipe["resolved"], f"{PRELUDE}\n{SIG}\n{body}\n", work,
                                                         f"p_{family}_{name}_{opt}")
                    names = list(variants)
                    a, b = listings[names[0]], listings[names[1]]
                    verdict = "untestable" if a is None or b is None else ("same" if a == b else "differ")
                    rows.append({"context": context, "opt": opt, "verdict": verdict,
                                 "pair": names, "sources": variants,
                                 "listings": listings if verdict == "differ" else None})
                    print(f"{family:18} {opt} {verdict:10} {context}", flush=True)
            results["families"][family] = rows
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "eval/results/rule-probes-20261002/probes.json"))
    ap.add_argument("--families", default="", help="comma-separated subset; merged into an existing receipt")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    only = set(a.families.split(",")) if a.families else None
    fresh = run(only)
    if only and out.exists():
        merged = json.loads(out.read_text())
        merged["families"].update(fresh["families"])
        fresh = merged
    out.write_text(json.dumps(fresh, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
