"""Follow-up (RESULT.md "What this says to do"): replace the planted site with the project's own localizer.

For every planted case, compile with the compiler's line records (`solver.source_attribution`), then measure
  1. the localizer   `solver.edit_locality.residual_lines` -- does it name the planted line, and how many lines?
  2. the tool        `solver.evidence_site.variants`       -- deterministic, no model: exact?
  3. the model at four levels that use only what a real run would have:
       R3  target asm + diff + "mismatch attributed to lines X"          (L3 with a real localizer)
       RA  target asm + diff annotated with C lines (solver.diff_annotate) (no separate hint)
       RS  lines X only, no asm                                           (S without the class)
       RE  as RS, but the answer is line edits, not a whole function      (output-format lever)

    python3 localize.py prepare      -> E/localized.jsonl
    python3 localize.py tool         -> E/tool.jsonl
    python3 localize.py solve [--jobs 3]  -> E/attempts_localized.jsonl
    python3 localize.py report
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import re
import subprocess
import threading

import run
from run import E, mine, llm, n64_corpus

from solver import diff_annotate, edit_locality, evidence_site, source_attribution

LEVELS = ("R3", "RA", "RS", "RE")


def build_attr(name: str, stem: str, code: str) -> dict:
    """pairs.build plus the source-attribution capture that workspace.score performs."""
    ws = run.pairs.workspace(run.mirror(), name)
    (ws / f"{stem}.c").write_text(code)
    recipes = sorted(ws.glob(".compiler-*.sh"))
    script = ws / (recipes[0].name if len(recipes) == 1 else "build.sh")
    script = source_attribution.prepare(ws, stem, script)
    r = subprocess.run(["bash", script.name, f"{stem}.c"], cwd=ws, capture_output=True, text=True, timeout=300)
    log = r.stdout + r.stderr
    m = re.search(r"Score: ([\d.]+)%", log)
    dump = ws / f"{stem}_object_dump_normalized.s"
    if not (r.returncode == 0 and dump.exists() and m):
        return {"compiled": False, "error": log[-800:]}
    diff = (ws / f"{stem}_diff").read_text(errors="replace") if (ws / f"{stem}_diff").exists() else ""
    attr = source_attribution.collect(ws, stem, code, code, diff)
    # The masked-listing comparison callers make (mine.mask) hides relocation symbols: `return ga;` and `return gb;`
    # pass it (audit 2026-10-03). The certificate against the ROM-extracted target.o is the verdict that counts.
    from solver import byte_certificate
    cert = byte_certificate.certify(ws / "target.o", ws / f"{stem}.o", source=code)
    return {"compiled": True, "score": float(m.group(1)), "dump": dump.read_text(), "diff": diff, "attr": attr,
            "certified": bool(cert.get("exact"))}


def def_line(case, tu_line: int) -> int | None:
    """1-based TU line -> 1-based line of the function definition as numbered in prompts."""
    n = tu_line - case["head"].count("\n")
    return n if 1 <= n <= len(case["perturbed_def"].split("\n")) else None


def prepare():
    cases = [json.loads(l) for l in open(E / "cases.jsonl")]
    lock = threading.Lock()
    out = []

    def one(c):
        code = c["head"] + c["perturbed_def"] + c["tail"]
        b = build_attr(c["function"], f"ec_loc_{c['class']}", code)
        rec = {"id": c["id"], "class": c["class"]}
        if not b["compiled"]:
            return rec | {"status": "not-compiled", "error": b["error"]}
        if mine.mask(b["dump"]) == c["target"]:
            return rec | {"status": "exact-already"}
        attr = b["attr"]
        lines = edit_locality.residual_lines(code, b["diff"], attr)
        local = sorted({x for x in (def_line(c, l) for l in (lines or ())) if x})
        site = c["site_line"] + 1
        near = any(abs(x - site) <= 1 for x in local)
        annotated = diff_annotate.annotate(b["diff"], attr)
        # Renumber `L<tu line>` to the definition's own numbering, which is what the prompt shows.
        annotated = re.sub(r" L(\d+)$", lambda m: f" L{def_line(c, int(m.group(1))) or '?'}", annotated, flags=re.M)
        return rec | {"status": "ok", "attr_status": attr.get("status"), "attr_reason": attr.get("reason"),
                      "lines": local, "hit": site in local, "near": near, "site": site,
                      "diff_raw": b["diff"], "diff_annotated": annotated, "code": code}

    with concurrent.futures.ThreadPoolExecutor(3) as ex:
        for rec in ex.map(one, cases):
            with lock:
                out.append(rec)
                print(rec["id"], rec["status"], rec.get("attr_status"), rec.get("lines"), "site", rec.get("site"),
                      "HIT" if rec.get("hit") else ("near" if rec.get("near") else "miss"), flush=True)
    with open(E / "localized.jsonl", "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")


def tool():
    cases = {c["id"]: c for c in map(json.loads, open(E / "cases.jsonl"))}
    loc = [json.loads(l) for l in open(E / "localized.jsonl")]

    def one(r):
        c = cases[r["id"]]
        res = {"id": r["id"], "class": r["class"], "variants": 0, "exact": False}
        if r["status"] != "ok":
            return res | {"skipped": r["status"]}
        attr = build_attr(c["function"], f"ec_tool_{c['class']}", r["code"])["attr"]   # fresh, same source
        for label, cand in evidence_site.variants(r["code"], c["function"], r["diff_raw"], attr):
            res["variants"] += 1
            b = run.build(c["function"], f"ec_toolv_{c['class']}", cand)
            if b["compiled"] and mine.mask(b["dump"]) == c["target"]:
                return res | {"exact": True, "label": label}
        return res

    with concurrent.futures.ThreadPoolExecutor(3) as ex, open(E / "tool.jsonl", "w") as f:
        for res in ex.map(one, loc):
            f.write(json.dumps(res) + "\n")
            print(res["id"], res["variants"], "EXACT " + res.get("label", "") if res["exact"] else "-", flush=True)


EDIT_RE = re.compile(r"^(LINE|INSERT AFTER) (\d+):(?: (.*))?$")


def apply_edits(definition: str, text: str) -> str | None:
    lines = definition.split("\n")
    repl, ins = {}, collections.defaultdict(list)
    found = False
    for raw in text.splitlines():
        m = EDIT_RE.match(raw.strip().strip("`"))
        if not m:
            continue
        found = True
        n = int(m.group(2))
        if not 1 <= n <= len(lines):
            return None
        (repl.__setitem__(n, m.group(3) or "") if m.group(1) == "LINE" else ins[n].append(m.group(3) or ""))
    if not found:
        return None
    out = []
    for i, l in enumerate(lines, 1):
        if i in repl:
            if repl[i].strip():
                out.append(l[:len(l) - len(l.lstrip())] + repl[i].strip())
        else:
            out.append(l)
        indent = l[:len(l) - len(l.lstrip())] or "    "
        out.extend(indent + x.strip() for x in ins.get(i, []))
    return "\n".join(out)


def prompt(c, r, level):
    decl = c["head"].strip()[-12000:]
    lines = ", ".join(map(str, r["lines"])) if r["lines"] else None
    p = ["You are matching a decompiled N64 function (IDO 5.3, -O2, MIPS). The C below compiles but does NOT",
         "produce the same machine code as the original. Make it compile to exactly the target.",
         "", "Declarations in scope (do not repeat them):", "```c", decl, "```", "",
         "Current function (line numbers for reference only):", "```c", run.numbered(c["perturbed_def"]), "```"]
    if level in ("R3", "RA"):
        p += ["", "Target assembly (normalized object dump, relocations masked):", "```", "\n".join(c["target"]), "```"]
    if level == "R3":
        p += ["", "Unified diff, target (-) versus what the current C compiles to (+):", "```", c["diff"].strip(), "```"]
    if level == "RA":
        p += ["", "Unified diff, target (-) versus current (+). `; L<n>` names the C line (numbered as above) "
              "that the compiler attributes a + row to:", "```", r["diff_annotated"].strip(), "```"]
    if level in ("R3", "RS", "RE", "RX") and lines:
        p += ["", f"The compiler attributes the mismatching instructions to line(s) {lines}."]
    if level == "RX":
        from patterns.catalog import CATALOG
        from solver import principles
        rules = principles.residual_rules(c["diff"])
        if rules:
            p += ["", "Diagnosis from tested IDO compiler rules (derived from the assembly difference):"]
            for pid, reason in rules:
                pat = CATALOG[pid]
                p += [f"- {pat.name}. Observed: {reason}. What it means: {pat.means}"]
    if level == "RE":
        p += ["", "Answer ONLY with line edits, one per row, no code block:",
              "  LINE <n>: <new text for line n>      (empty text deletes the line)",
              "  INSERT AFTER <n>: <new line>",
              "Change as few lines as possible."]
    else:
        p += ["", "Return the complete corrected function definition in one ```c block. Change as little as needed."]
    return "\n".join(p)


def solve_one(c, r, level, endpoint):
    pr = prompt(c, r, level)
    text, meta = llm.generate(endpoint, run.MODEL, pr, timeout=600, num_predict=6000, think="low", temperature=0.2,
                              seed=1, cache_dir=str(E / "llm-cache"), cache_namespace="edit-capability-loc-v1")
    out = {"id": c["id"], "class": c["class"], "kind": c["kind"], "level": level, "lines": r["lines"],
           "hit": r["hit"], "prompt": pr, "response": text, "eval_count": meta.get("eval_count")}
    if level == "RE":
        new_def = apply_edits(c["perturbed_def"], text)
        if new_def is None:
            return out | {"status": "no-definition"}
    else:
        defs = [d for d in n64_corpus.extract_functions(llm.extract_c(text) or "") if d["name"] == c["function"]]
        if len(defs) != 1:
            return out | {"status": "no-definition"}
        new_def = str(defs[0]["definition"])
    b = run.build(c["function"], f"ec_r_{c['class']}_{level}", c["head"] + new_def + c["tail"])
    out["def"] = new_def
    if not b["compiled"]:
        return out | {"status": "not-compiled", "error": (b.get("error") or "")[-300:]}
    exact = mine.mask(b["dump"]) == c["target"]
    return out | {"status": "exact" if exact else "compiled", "score": b["score"], "base_score": c["score"]}


def solve(jobs, levels=LEVELS):
    cases = {c["id"]: c for c in map(json.loads, open(E / "cases.jsonl"))}
    loc = {r["id"]: r for r in map(json.loads, open(E / "localized.jsonl")) if r["status"] == "ok"}
    path = E / "attempts_localized.jsonl"
    done = {(x["id"], x["level"]) for x in map(json.loads, open(path))} if path.exists() else set()
    if "RX" in levels:                     # only where a rule is diagnosed; elsewhere RX would equal RS
        from solver import principles
        ruled = {i for i in loc if principles.residual_rules(cases[i]["diff"])}
    todo = [(cases[i], loc[i], lv) for lv in levels for i in loc
            if (i, lv) not in done and (lv != "RX" or i in ruled)]
    print(f"{len(todo)} attempts to run ({len(done)} done)", flush=True)
    endpoint, lock = llm.host(), threading.Lock()

    def go(item):
        c, r, lv = item
        try:
            x = solve_one(c, r, lv, endpoint)
        except Exception as exc:
            x = {"id": c["id"], "class": c["class"], "kind": c["kind"], "level": lv, "status": "error",
                 "error": repr(exc)[-300:]}
        with lock, open(path, "a") as f:
            f.write(json.dumps(x) + "\n")
            print(lv, c["id"], x["status"], x.get("score"), flush=True)

    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        list(ex.map(go, todo))


def report():
    loc = [json.loads(l) for l in open(E / "localized.jsonl")]
    ok = [r for r in loc if r["status"] == "ok"]
    print(f"localizer: {len(ok)}/{len(loc)} usable; attribution status "
          f"{dict(collections.Counter(r.get('attr_status') for r in ok))}")
    print(f"  names the planted line: {sum(r['hit'] for r in ok)}; within 1 line: {sum(r['near'] for r in ok)}; "
          f"no lines: {sum(not r['lines'] for r in ok)}; median lines named: "
          f"{sorted(len(r['lines']) for r in ok)[len(ok) // 2] if ok else '-'}")
    by = collections.defaultdict(lambda: collections.Counter())
    for r in ok:
        by[r["class"]]["n"] += 1
        by[r["class"]]["hit"] += r["hit"]
    tool_rows = [json.loads(l) for l in open(E / "tool.jsonl")] if (E / "tool.jsonl").exists() else []
    for t in tool_rows:
        by[t["class"]]["tool"] += t["exact"]
    att = [json.loads(l) for l in open(E / "attempts_localized.jsonl")] if (E / "attempts_localized.jsonl").exists() else []
    for a in att:
        by[a["class"]][a["level"]] += a["status"] == "exact"
        by[a["class"]][a["level"] + "_n"] += 1
    print(f"\n{'class':12} {'loc hit':>8} {'tool':>5} " + " ".join(f"{lv:>6}" for lv in LEVELS))
    tot = collections.Counter()
    for cls in run.PERTURB:
        b = by.get(cls)
        if not b:
            continue
        tot.update(b)
        print(f"{cls:12} {b['hit']:>3}/{b['n']:<4} {b['tool']:>5} " +
              " ".join(f"{b[lv]}/{b[lv + '_n']}".rjust(6) for lv in LEVELS))
    print(f"{'all':12} {tot['hit']:>3}/{tot['n']:<4} {tot['tool']:>5} " +
          " ".join(f"{tot[lv]}/{tot[lv + '_n']}".rjust(6) for lv in LEVELS))
    st = collections.Counter((a["level"], a["status"]) for a in att)
    for lv in LEVELS:
        print(lv, {s: st[(lv, s)] for s in ("exact", "compiled", "not-compiled", "no-definition", "error") if st[(lv, s)]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("prepare", "tool", "solve", "report"))
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--levels", default=",".join(LEVELS))
    a = ap.parse_args()
    {"prepare": prepare, "tool": tool, "report": report}.get(a.cmd, lambda: solve(a.jobs, a.levels.split(",")))()


if __name__ == "__main__":
    main()
