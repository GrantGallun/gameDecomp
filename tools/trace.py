"""Trace one function through the ENTIRE pipeline, stage by stage.

Every match gained today came from debugging, not from an experiment: a
cost-read-as-score inversion that hid four finished matches, a 99.999 displayed
as "100.00" that invented a relocation diagnosis, a padding byte, an extraction
fallback that handed the compiler its own opening fence. In each case the
pipeline was lying quietly and no experiment could have found it, because
experiments only see the number at the end.

So this shows every boundary:

    target asm -> context -> prompt -> generation -> extraction
               -> compile -> score -> diagnose -> route

and CHECKS each one. A stage that produces something impossible (empty context,
a candidate that is really assembly, a score of 100 that is not exact) is
flagged where it happens instead of surfacing as a bad number three stages
later.

    python3 -m tools.trace <function>              # no GPU: replay stored best
    python3 -m tools.trace <function> --generate   # include a live generation

Read it top to bottom; the first FLAG is usually the bug.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import context as kb_context
from solver import diagnose, llm, pipeline, workspace

FLAGS: list[str] = []


def hdr(n: int, title: str) -> None:
    print(f"\n{'=' * 74}\n[{n}] {title}\n{'=' * 74}")


def flag(msg: str) -> None:
    FLAGS.append(msg)
    print(f"    !! FLAG: {msg}")


def ok(msg: str) -> None:
    print(f"    ok  {msg}")


def show(label: str, value, width: int = 58) -> None:
    s = str(value).replace("\n", " ")
    print(f"    {label:<26} {s[:width]}")


def trace(func: str, generate: bool, model: str) -> int:
    repo = Path.home() / "decomp/sbk1"
    conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")

    # ---------------------------------------------------------------- input
    hdr(1, f"INPUT  {func}")
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    # Count INSTRUCTIONS, not lines: glabel and branch labels are neither.
    # Counting lines made this flag fire on a perfectly healthy function
    # (52 instructions, 61 lines), and a checker that cries wolf teaches you
    # to ignore it -- which is worse than having no checker.
    n_ins = 0
    for line in asm.splitlines():
        s = re.sub(r"/\*.*?\*/", "", line).strip()
        if not s or s.endswith(":") or s.startswith(("glabel", ".", "/*")):
            continue
        n_ins += 1
    show("workspace", ws)
    show("asm lines", n_ins)
    show("asm chars", len(asm))
    if n_ins == 0:
        flag("target assembly is EMPTY -- nothing downstream can be right")
    row = conn.execute("select addr, insn_count, is_leaf from functions"
                       " where name=?", (func,)).fetchone()
    if not row:
        flag("function is NOT in the functions table -- attempts cannot log")
    else:
        show("addr / insns / leaf", f"{row[0]:#x} / {row[1]} / {row[2]}")
        if row[1] and abs(row[1] - n_ins) > max(4, 0.1 * row[1]):
            flag(f"insn_count {row[1]} disagrees with disassembly {n_ins}")

    # -------------------------------------------------------------- context
    hdr(2, "CONTEXT the solver assembles")
    draft = workspace.m2c_draft(ws)
    kb = kb_context.for_function(conn, func)
    hints = hints_for_asm(asm)
    show("m2c draft chars", len(draft or ""))
    if not draft:
        flag("m2c produced NO draft -- the solver starts from raw asm")
    show("kb context chars", len(kb or ""))
    if not kb:
        flag("KB context is EMPTY -- no observed accesses reach the prompt")
    else:
        n_acc = len(re.findall(r"^\s+\S+\+?0x", kb or "", re.M))
        show("kb access lines", n_acc)
        show("kb names a type?", "yes" if re.search(r"\b(Gfx|struct|\*)", kb or "")
             else "NO -- addresses and widths only")
    show("hint chars", len(hints or ""))
    n_inf = conn.execute("select count(*) from inference").fetchone()[0]
    show("inference rows (global)", n_inf)
    if n_inf == 0:
        flag("inference tier is EMPTY -- no accumulated types exist to supply")

    # --------------------------------------------------------------- prompt
    hdr(3, "PROMPT actually sent")
    prompt = pipeline.build_prompt(repo, conn, func, asm, draft, "reshape",
                                   use_siblings=False)
    show("prompt chars", len(prompt))
    show("est. tokens", len(prompt) // 3)
    for needle, label in [("TARGET ASSEMBLY", "target asm block"),
                          ("M2C DRAFT", "m2c draft block"),
                          ("OBSERVED MEMORY", "kb evidence block")]:
        show(f"contains {label}", "yes" if needle in prompt else "NO")
    if "do-while" in prompt and "for (;;)" not in prompt:
        flag("prompt still forbids do-while without giving the for(;;) form")
    try:
        workspace.assert_uncontaminated(prompt, repo, func)
        ok("contamination check passed")
    except Exception as exc:
        flag(f"CONTAMINATED prompt: {exc}")

    # ----------------------------------------------------------- generation
    hdr(4, "GENERATION")
    raw = ""
    if generate:
        t0 = time.time()
        raw, meta = llm.generate(llm.host(), model, prompt, timeout=600,
                                 think="low", num_thread=12, temperature=0.7)
        show("wall seconds", f"{time.time() - t0:.0f}")
        show("eval_count", meta.get("eval_count"))
        show("done_reason", meta.get("done_reason"))
        if meta.get("done_reason") == "length":
            flag("TRUNCATED: budget exhausted, this is a config failure "
                 "and must not be scored as a model failure")
        if meta.get("_fell_back_to_thinking"):
            flag("empty response; fell back to the thinking field")
        show("raw chars", len(raw))
    else:
        print("    (skipped -- replaying the best stored candidate)")

    # ----------------------------------------------------------- extraction
    hdr(5, "EXTRACTION")
    if generate:
        code = llm.extract_c(raw)
        show("extracted chars", len(code))
        if code == "":
            flag("extract_c returned NOTHING -- an extraction failure, "
                 "not a model failure")
        if code.lstrip().startswith("```"):
            flag("fence leaked into the source")
        if code and llm._looks_like_asm(code):
            flag("extracted text is ASSEMBLY, not C")
    else:
        best = conn.execute(
            "select source_code, score from attempts where func_addr=("
            "select addr from functions where name=?) and compiled=1"
            " order by score desc limit 1", (func,)).fetchone()
        if not best or not best[0]:
            flag("no stored compiled candidate to replay")
            return 1
        code, stored = best[0], best[1]
        show("stored best score", f"{stored:.3f}")
        show("candidate chars", len(code))
        show("declares a struct", "yes" if "typedef struct" in code else "no")

    # ------------------------------------------------------ compile + score
    hdr(6, "COMPILE + SCORE")
    att = workspace.score(ws, repo, "trace", code, conn=conn, func=func,
                          strategy="trace")
    show("compiled", att.compiled)
    if not att.compiled:
        flag("did not compile")
        print("    " + (att.compiler_stderr or "")[:300].replace("\n", "\n    "))
    show("score", f"{att.score:.3f}")
    show("EXACT", att.exact)
    # the rounding trap that cost an hour today
    if not att.exact and f"{att.score:.2f}" == "100.00":
        flag(f"score displays as 100.00 but is {att.score:.5f} and NOT exact -- "
             "two-decimal formatting hides the gap")
    if att.compiled and att.score >= 100 and not att.exact:
        flag("score >= 100 yet not exact: instructions match, bytes do not "
             "(relocation or symbol naming)")

    # ------------------------------------------------------------- diagnose
    hdr(7, "DIAGNOSE")
    verdict = ""
    if att.compiled and not att.exact:
        d = diagnose.run(repo, ws / "target.o", ws / "trace.o", timeout=120)
        if d is None:
            flag("diagnose returned nothing -- verdict unavailable, so the "
                 "router will fall back to score alone")
        else:
            verdict = getattr(d, "verdict", "") or ""
            show("verdict", verdict or "(empty)")
            first = [l for l in (att.diff or "").splitlines()
                     if l.startswith(("-", "+")) and not l.startswith(("---", "+++"))]
            show("diff lines", len(first))
            for l in first[:6]:
                print(f"      {l[:70]}")
    else:
        print("    (nothing to diagnose)")

    # ---------------------------------------------------------------- route
    hdr(8, "ROUTE")
    route = pipeline.route_for(verdict, att.score, exact=att.exact)
    show("chosen route", route)
    if att.score >= 95 and route == "permute" and verdict and "reloc" not in verdict:
        flag("routed to the permuter on SCORE alone. Measured 2026-08-28: "
             "500s each closed 0 of 5, because the real causes were struct "
             "layout, a wrong base register, and a load-order swap -- none of "
             "which the permuter can reach")

    # -------------------------------------------------------------- summary
    print(f"\n{'=' * 74}")
    if FLAGS:
        print(f"{len(FLAGS)} FLAG(S) -- the first is usually the bug:")
        for i, f in enumerate(FLAGS, 1):
            print(f"  {i}. {f}")
    else:
        print("no flags: every stage produced something plausible")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("function")
    ap.add_argument("--generate", action="store_true",
                    help="run a live generation instead of replaying storage")
    ap.add_argument("--model", default="gpt-oss:20b")
    a = ap.parse_args()
    return trace(a.function, a.generate, a.model)


if __name__ == "__main__":
    raise SystemExit(main())
