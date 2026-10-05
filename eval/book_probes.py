"""Principles from reverse-engineering and compiler literature, turned into IDO 5.3 measurements.

    python3 -m eval.book_probes [--out eval/results/book-probes-20261005/probes.json] [--families a,b]

Books teach how compilers USUALLY lower C. Whether IDO 5.3 does is a hypothesis (CLAUDE.md, "Probe, don't assume"),
so each principle becomes minimal C spellings compiled with the game's own -O1 and -O2 recipes (eval.rule_probes),
and every variant is measured `same` or `differ` against the first. Nothing here asserts the book is right: a
principle IDO contradicts is a finding too. Principles are paraphrased and cited by work and topic; no text is
copied.

What a verdict is for:
  same    the spellings are one program to IDO: a search must not spend attempts on the difference, and a model
          must not be taught that it matters (patterns/ido_equivalences.json is that list).
  differ  the spelling is visible in the code: the listing pair is a training example and a candidate solver
          rule ("this instruction shape means that C"), to be catalogued with provenance.

Sources (topics, not quotations):
  RE4B   D. Yurichev, "Reverse Engineering for Beginners" (switch, conditional jumps, loops, division by
         multiplication, FPU, bit fields).
  EILAM  E. Eilam, "Reversing: Secrets of Reverse Engineering" (2005), appendix on deciphering code structures
         (loops, switch, logical operators, conditional expressions).
  KASP   K. Kaspersky, "Hacker Disassembling Uncovered" (identifying high-level constructs: branches, switch,
         loops, arithmetic operators, constants).
  HD     H. Warren, "Hacker's Delight" (multiplication by constants, range checks).
  CIF    C. Cifuentes, "Reverse Compilation Techniques" (PhD thesis, 1994) (control-structure recovery,
         short-circuit evaluation).
  SAILR  Basque et al., "Ahoy SAILR!" (USENIX Security 2024) (goto-inducing compiler transformations).
  C89    ANSI X3.159-1989 (default argument promotions, usual arithmetic conversions).
  MIPS   D. Sweetman, "See MIPS Run" (big-endian bit-field allocation, o32 calling convention).
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from eval import rule_probes as rp

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = rp.PRELUDE + """typedef float f32; typedef double f64;
extern f32 gF; extern s32 gArr[16]; extern Obj *gObj;
typedef struct { u32 a : 4; u32 b : 12; u32 c : 16; } Bits;
extern Bits gBits;
"""
SIG = rp.SIG

# family -> {"source", "principle", "contexts": [(context, {variant: body})]}; variants compared to the FIRST.
FAMILIES: dict[str, dict] = {
    "switch_vs_if_chain": {
        "source": "RE4B switch; EILAM switch blocks; KASP switch",
        "principle": "A dense switch becomes a jump table and a sparse or small one a compare chain, so a switch and "
                     "the equivalent if-else chain compile identically only below the table threshold.",
        "contexts": [
            ("3 dense cases", {
                "switch": "{ switch (p) { case 0: k(); break; case 1: g(1); break; case 2: g(2); break; } return 0; }",
                "if_chain": "{ if (p == 0) { k(); } else if (p == 1) { g(1); } else if (p == 2) { g(2); } return 0; }"}),
            ("5 dense cases", {
                "switch": "{ switch (p) { case 0: k(); break; case 1: g(1); break; case 2: g(2); break; "
                          "case 3: g(3); break; case 4: g(4); break; } return 0; }",
                "if_chain": "{ if (p == 0) { k(); } else if (p == 1) { g(1); } else if (p == 2) { g(2); } "
                            "else if (p == 3) { g(3); } else if (p == 4) { g(4); } return 0; }"}),
            ("8 dense cases", {
                "switch": "{ switch (p) { case 0: k(); break; case 1: g(1); break; case 2: g(2); break; "
                          "case 3: g(3); break; case 4: g(4); break; case 5: g(5); break; case 6: g(6); break; "
                          "case 7: g(7); break; } return 0; }",
                "if_chain": "{ if (p == 0) { k(); } else if (p == 1) { g(1); } else if (p == 2) { g(2); } "
                            "else if (p == 3) { g(3); } else if (p == 4) { g(4); } else if (p == 5) { g(5); } "
                            "else if (p == 6) { g(6); } else if (p == 7) { g(7); } return 0; }"}),
            ("4 sparse cases", {
                "switch": "{ switch (p) { case 1: k(); break; case 10: g(1); break; case 100: g(2); break; "
                          "case 1000: g(3); break; } return 0; }",
                "if_chain": "{ if (p == 1) { k(); } else if (p == 10) { g(1); } else if (p == 100) { g(2); } "
                            "else if (p == 1000) { g(3); } return 0; }"}),
        ]},
    "switch_case_order": {
        "source": "RE4B switch; EILAM switch blocks",
        "principle": "Case labels are reordered by value when lowered, so the source order of cases is invisible.",
        "contexts": [
            ("3 dense cases", {
                "ascending": "{ switch (p) { case 0: k(); break; case 1: g(1); break; case 2: g(2); break; } return 0; }",
                "shuffled": "{ switch (p) { case 2: g(2); break; case 0: k(); break; case 1: g(1); break; } return 0; }"}),
            ("8 dense cases", {
                "ascending": "{ switch (p) { case 0: k(); break; case 1: g(1); break; case 2: g(2); break; "
                             "case 3: g(3); break; case 4: g(4); break; case 5: g(5); break; case 6: g(6); break; "
                             "case 7: g(7); break; } return 0; }",
                "shuffled": "{ switch (p) { case 5: g(5); break; case 1: g(1); break; case 7: g(7); break; "
                            "case 0: k(); break; case 3: g(3); break; case 6: g(6); break; case 2: g(2); break; "
                            "case 4: g(4); break; } return 0; }"}),
        ]},
    "multiply_by_constant": {
        "source": "HD multiplication by constants; KASP arithmetic operators; RE4B multiplication",
        "principle": "Multiplication by a small constant is strength-reduced to shifts and adds, so `x * 10` and "
                     "`(x << 3) + (x << 1)` compile identically.",
        "contexts": [
            ("x * 10", {"mul": "{ return p * 10; }", "shifts": "{ return (p << 3) + (p << 1); }"}),
            ("x * 9", {"mul": "{ return p * 9; }", "shifts": "{ return (p << 3) + p; }"}),
            ("x * 7", {"mul": "{ return p * 7; }", "shifts": "{ return (p << 3) - p; }"}),
            ("x * 12", {"mul": "{ return p * 12; }", "shifts": "{ return (p << 3) + (p << 2); }"}),
        ]},
    "divide_by_constant": {
        "source": "RE4B division by multiplication; HD integer division by constants",
        "principle": "Division by a non-power-of-two constant becomes a multiply-high by a magic number, and signed "
                     "and unsigned division by the same constant compile differently.",
        "contexts": [
            ("signed vs unsigned / 3", {"signed": "{ return p / 3; }", "unsigned": "{ return (u32) p / 3; }"}),
            ("signed vs unsigned / 10", {"signed": "{ return p / 10; }", "unsigned": "{ return (u32) p / 10; }"}),
            ("signed vs unsigned / 8", {"signed": "{ return p / 8; }", "unsigned": "{ return (u32) p / 8; }"}),
        ]},
    "boolean_materialization": {
        "source": "RE4B conditional jumps / set-on-condition; EILAM conditional expressions",
        "principle": "A comparison whose value is used is materialised branch-free (slt/xor/sltiu), so returning the "
                     "comparison, a 1:0 conditional and an if/return pair compile identically.",
        "contexts": [
            ("== returned", {"expr": "{ return p == q; }", "ternary": "{ return (p == q) ? 1 : 0; }",
                             "if_return": "{ if (p == q) { return 1; } return 0; }"}),
            ("< returned", {"expr": "{ return p < q; }", "ternary": "{ return (p < q) ? 1 : 0; }",
                            "if_return": "{ if (p < q) { return 1; } return 0; }"}),
            ("!= stored", {"expr": "{ o->w = p != 0; return 0; }", "ternary": "{ o->w = (p != 0) ? 1 : 0; return 0; }",
                           "if_else": "{ if (p != 0) { o->w = 1; } else { o->w = 0; } return 0; }"}),
        ]},
    "short_circuit": {
        "source": "CIF short-circuit evaluation; EILAM logical operators",
        "principle": "`a && b` is lowered to the same branch chain as nested ifs, and `a || b` to the same chain as "
                     "two tests sharing one body.",
        "contexts": [
            ("&& vs nested if", {"and": "{ if (p != 0 && q != 0) { k(); } return 0; }",
                                 "nested": "{ if (p != 0) { if (q != 0) { k(); } } return 0; }"}),
            ("&& with field tests", {"and": "{ if (o->w > 3 && o->flag == 0) { k(); } return 0; }",
                                     "nested": "{ if (o->w > 3) { if (o->flag == 0) { k(); } } return 0; }"}),
            ("|| vs else-if same body", {"or": "{ if (p == 0 || q == 0) { k(); } return 0; }",
                                         "else_if": "{ if (p == 0) { k(); } else if (q == 0) { k(); } return 0; }"}),
        ]},
    "ternary_vs_if": {
        "source": "EILAM conditional expressions; RE4B conditional operator",
        "principle": "`r = c ? x : y` and the if/else assignment produce the same code.",
        "contexts": [
            ("param select", {"ternary": "{ s32 r; r = (p > 0) ? q : 5; return r; }",
                              "if_else": "{ s32 r; if (p > 0) { r = q; } else { r = 5; } return r; }"}),
            ("store select", {"ternary": "{ o->w = (p > 0) ? q : 5; return 0; }",
                              "if_else": "{ if (p > 0) { o->w = q; } else { o->w = 5; } return 0; }"}),
            ("abs", {"ternary": "{ return (p < 0) ? -p : p; }", "if": "{ if (p < 0) { p = -p; } return p; }"}),
            ("min", {"ternary": "{ return (p < q) ? p : q; }", "if": "{ if (q < p) { p = q; } return p; }"}),
        ]},
    "loop_forms": {
        "source": "EILAM loops; CIF loop structuring; KASP loops",
        "principle": "Compilers rotate a pre-tested loop into a guarded do-while, so `for`, `while` and a guarded "
                     "`do`-`while` over the same iteration compile identically.",
        "contexts": [
            ("sum to p", {
                # Same initialisation order in every spelling: the first version differed only in that order.
                "for": "{ s32 i; s32 s; s = 0; for (i = 0; i < p; i++) { s += gArr[i]; } return s; }",
                "while": "{ s32 i; s32 s; s = 0; i = 0; while (i < p) { s += gArr[i]; i++; } return s; }",
                "guarded_do": "{ s32 i; s32 s; s = 0; i = 0; if (i < p) { do { s += gArr[i]; i++; } while (i < p); } "
                              "return s; }"}),
            ("call loop", {
                "for": "{ s32 i; for (i = 0; i < p; i++) { g(i); } return 0; }",
                "while": "{ s32 i; i = 0; while (i < p) { g(i); i++; } return 0; }"}),
            ("count down", {
                "for": "{ s32 i; for (i = p; i > 0; i--) { g(i); } return 0; }",
                "while": "{ s32 i; i = p; while (i > 0) { g(i); i--; } return 0; }"}),
        ]},
    "index_vs_pointer_walk": {
        "source": "KASP loops and arrays; RE4B arrays",
        "principle": "An indexed array loop is strength-reduced to a pointer walk, so the two spellings compile "
                     "identically.",
        "contexts": [
            ("sum 16", {"index": "{ s32 i; s32 s = 0; for (i = 0; i < 16; i++) { s += gArr[i]; } return s; }",
                        "pointer": "{ s32 *a; s32 s = 0; for (a = gArr; a < gArr + 16; a++) { s += *a; } return s; }"}),
        ]},
    "early_return_vs_single_exit": {
        "source": "SAILR (single-exit vs multiple-return structuring); CIF",
        "principle": "Early returns and a single exit through a result variable are structurally different and "
                     "may compile differently (duplicated epilogues vs one join).",
        "contexts": [
            ("guard then call", {"early": "{ if (p < 0) { return 0; } return g(p); }",
                                 "single": "{ s32 r = 0; if (p >= 0) { r = g(p); } return r; }"}),
            ("two guards", {"early": "{ if (p < 0) { return 0; } if (q < 0) { return 1; } return h(p, q); }",
                            "single": "{ s32 r; if (p < 0) { r = 0; } else if (q < 0) { r = 1; } else "
                                      "{ r = h(p, q); } return r; }"}),
        ]},
    "goto_vs_structured": {
        "source": "SAILR goto-inducing transformations",
        "principle": "A forward goto to a common exit compiles like the structured equivalent.",
        "contexts": [
            ("skip a call", {"structured": "{ if (p == 0) { k(); } return 0; }",
                             "goto": "{ if (p != 0) goto end; k(); end: return 0; }"}),
            ("shared cleanup", {"structured": "{ if (p == 0) { g(1); } else { g(2); } k(); return 0; }",
                                "goto": "{ if (p != 0) goto two; g(1); goto done; two: g(2); done: k(); return 0; }"}),
        ]},
    "common_subexpression": {
        "source": "KASP / RE4B optimisation (common subexpression elimination)",
        "principle": "A repeated load is eliminated, so naming it in a temporary changes nothing; but a store "
                     "through a pointer may alias a global and force a reload.",
        "contexts": [
            ("global read twice", {"repeat": "{ o->w = gWord; o->v = gWord; return 0; }",
                                   "temp": "{ s32 t = gWord; o->w = t; o->v = t; return 0; }"}),
            ("global pointer, two stores", {"repeat": "{ gObj->w = 1; gObj->v = 2; return 0; }",
                                            "temp": "{ Obj *t = gObj; t->w = 1; t->v = 2; return 0; }"}),
            ("field read twice", {"repeat": "{ return g(o->w) + h(o->w, 0); }",
                                  "temp": "{ s32 t = o->w; return g(t) + h(t, 0); }"}),
        ]},
    "float_constant_promotion": {
        "source": "C89 usual arithmetic conversions; RE4B FPU",
        "principle": "An unsuffixed floating constant is double, so `f * 0.5` computes in double (cvt.d.s ... "
                     "cvt.s.d) where `f * 0.5f` stays single.",
        "contexts": [
            ("multiply", {"double_const": "{ return (s32) (gF * 0.5); }",
                          "float_const": "{ return (s32) (gF * 0.5f); }"}),
            ("compare", {"double_const": "{ if (gF < 1.0) { k(); } return 0; }",
                         "float_const": "{ if (gF < 1.0f) { k(); } return 0; }"}),
            ("assign", {"double_const": "{ gF = gF + 2.0; return 0; }",
                        "float_const": "{ gF = gF + 2.0f; return 0; }"}),
        ]},
    "float_compare_inversion": {
        "source": "RE4B FPU comparisons (NaN makes !(a >= b) differ from a < b)",
        "principle": "Because of NaN, `!(a >= b)` is not `a < b` for floats, so the compiler cannot fold one into "
                     "the other and the spellings compile differently.",
        "contexts": [
            ("against zero", {"lt": "{ if (gF < 0.0f) { k(); } return 0; }",
                              "not_ge": "{ if (!(gF >= 0.0f)) { k(); } return 0; }"}),
        ]},
    "bitfield_layout": {
        "source": "MIPS big-endian bit-field allocation; RE4B bit fields",
        "principle": "On big-endian MIPS the first bit-field takes the most significant bits, so `s.b` (the 12 bits "
                     "after a 4-bit field) is `(word >> 16) & 0xFFF`.",
        "contexts": [
            ("read middle field", {"bitfield": "{ return gBits.b; }",
                                   "mask": "{ return (*(u32 *) &gBits >> 16) & 0xFFF; }"}),
            ("read first field", {"bitfield": "{ return gBits.a; }",
                                  "mask": "{ return *(u32 *) &gBits >> 28; }"}),
        ]},
    "range_check_fold": {
        "source": "HD range checks",
        "principle": "`lo <= x && x <= hi` can be folded into one unsigned compare `(unsigned)(x - lo) <= hi - lo`.",
        "contexts": [
            ("3..10", {"and": "{ if (p >= 3 && p <= 10) { k(); } return 0; }",
                       "unsigned": "{ if ((u32) (p - 3) <= 7) { k(); } return 0; }"}),
        ]},
    "negation_spellings": {
        "source": "KASP arithmetic operators",
        "principle": "`-x`, `0 - x` and `~x + 1` are one operation (negu).",
        "contexts": [
            ("param", {"neg": "{ return -p; }", "sub": "{ return 0 - p; }", "not_plus_one": "{ return ~p + 1; }"}),
        ]},
    "post_increment_in_index": {
        "source": "KASP / RE4B pointer and index arithmetic",
        "principle": "`a[i++]` and `a[i]; i++;` are the same program.",
        "contexts": [
            ("read then use i", {"post": "{ s32 x = gArr[p++]; return x + p; }",
                                 "split": "{ s32 x = gArr[p]; p++; return x + p; }"}),
        ]},
    "const_local_vs_literal": {
        "source": "KASP constants (constant propagation)",
        "principle": "A local initialised with a constant and never changed is propagated, so it compiles like the "
                     "literal.",
        "contexts": [
            ("multiply", {"literal": "{ return p * 7; }", "local": "{ s32 n = 7; return p * n; }",
                          "const_local": "{ const s32 n = 7; return p * n; }"}),
            ("call argument", {"literal": "{ return h(p, 0x40); }", "local": "{ s32 n = 0x40; return h(p, n); }"}),
        ]},
    "char_sign_compare": {
        "source": "C89 integral promotions; RE4B signed/unsigned types",
        "principle": "A signed char is promoted to int before comparison, so the explicit cast is redundant.",
        "contexts": [
            ("s8 field < 0", {"implicit": "{ if (o->count < 0) { k(); } return 0; }",
                              "cast": "{ if ((s32) o->count < 0) { k(); } return 0; }"}),
            ("u8 field == 0xFF", {"implicit": "{ if (o->flag == 0xFF) { k(); } return 0; }",
                                  "cast": "{ if ((s32) o->flag == 0xFF) { k(); } return 0; }"}),
        ]},
}


def run(only=None) -> dict:
    from eval import repair_dataset_synth as rds
    recipes = {opt: rds.resolve_recipe(rp.REPO, target) for opt, target in rp.RECIPES.items()}
    results = {"recipes": {opt: r["provenance"] for opt, r in recipes.items()}, "families": {}}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for family, spec in FAMILIES.items():
            if only and family not in only:
                continue
            rows = []
            for context, variants in spec["contexts"]:
                for opt, recipe in recipes.items():
                    listings, errors = {}, {}
                    for name, body in variants.items():
                        listings[name], errors[name] = rp.compile_listing_or_error(
                            recipe["resolved"], f"{PRELUDE}\n{SIG}\n{body}\n", work, f"b_{family}_{name}_{opt}")
                    names = list(variants)
                    first = listings[names[0]]
                    for other in names[1:]:
                        b = listings[other]
                        verdict = "untestable" if first is None or b is None else ("same" if first == b else "differ")
                        rows.append({"context": context, "opt": opt, "verdict": verdict, "pair": [names[0], other],
                                     "sources": {names[0]: variants[names[0]], other: variants[other]},
                                     "listings": ({names[0]: first, other: b} if verdict == "differ" else None),
                                     "errors": ({n: errors[n] for n in (names[0], other) if errors[n]}
                                                if verdict == "untestable" else None)})
                        print(f"{family:28} {opt} {verdict:10} {context}: {names[0]} vs {other}", flush=True)
            results["families"][family] = {"source": spec["source"], "principle": spec["principle"], "rows": rows}
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "eval/results/book-probes-20261005/probes.json"))
    ap.add_argument("--families", default="")
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
