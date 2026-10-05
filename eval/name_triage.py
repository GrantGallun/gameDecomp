"""WHY is this name unresolved? Six answers, six different repairs.

The loop kept reporting the same class — `undeclared-identifier`, `unknown-type-name` — and the class is not
a repair instruction. "m2c used a name nobody declared" covers at least six situations with six different
fixes, and treating them as one is why the same class survives round after round while the actions aimed at
it convert almost nothing.

    1. header-exists-not-included   the declaration is in `include/**` but not in this candidate's include
                                    closure -> resolve the dependency, change no declarations
    2. tag-without-alias            `struct X` is known and `X` is not a type name -> add the alias
    3. opaque-is-enough             used only through a pointer, never dereferenced -> declare the tag and
                                    stop; no layout is needed or wanted
    4. needs-layout                 dereferenced (`p->m`, `*p`) -> recover offsets and widths from the
                                    binary; this is the only type bucket that needs inference
    5. known-function               the target has a function with this name -> an extern prototype
    6. unresolved-global            a translated global, or a name that IS its own address -> an extern
                                    declaration typed from the target's accesses (`globals_variant`)
    7. m2c-dialect                  `bitwise`, `unaligned`, `sp` -> a REWRITE, and no header can ever help
    8. undeclared-local             `var_v0`, `temp_v1` -> m2c used its own temporary without declaring it
    9. unknown-identifier           value position, nothing known about it -> abstain

WHAT THIS DOES NOT CLAIM. It does not say a repair will work, and it does not rank by count alone: a bucket
of 40 `opaque-is-enough` names is a small change, and a bucket of 5 `needs-layout` names can be more work
than all of them. The count is a measurement of the RESIDUAL, and the earlier rounds' lesson is that a
residual count is never a distance.

Read-only: it compiles nothing and writes only its receipt. It recovers each state's candidate from the
attempt log by the hash the frame recorded, so it triages the bytes that were actually measured.

  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.name_triage --frame <receipt> --out <receipt>
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1

UNKNOWN_TYPE = re.compile(r"unknown type name '([^']+)'")
UNDECLARED = re.compile(r"use of undeclared identifier '([^']+)'")

REASONS = ("header-exists-not-included", "tag-without-alias", "opaque-is-enough", "needs-layout",
           "known-function", "unresolved-global", "m2c-dialect", "undeclared-local",
           "unknown-identifier", "unknown")

# WHAT AN UNDECLARED NAME IN VALUE POSITION ACTUALLY IS. The first pass called all 314 of them
# "m2c-spelling", which is a label rather than a repair: read apart, they are four kinds with four
# different fixes and four different owners. Each is decidable from the name and the source, and anything
# matching none of them stays `unknown-identifier` instead of being guessed into a bucket.
ADDRESS_NAMED = re.compile(r"^D_[0-9A-Fa-f]{8}$")          # the name IS the address
GLOBAL_NAMED = re.compile(r"^[gs][A-Z]\w*$")               # m2c's naming for a translated global
M2C_TEMP = re.compile(r"^(?:var|temp)_[a-z0-9]+$")         # m2c's own temporaries
# m2c dialect keywords: spellings m2c emits that the build's C89 + IDO dialect does not have. `bitwise` is
# the reinterpretation operator (`solver/m2c_context.lower_bitcasts` rewrites it); `unaligned` marks an
# unaligned access; `sp` is the stack pointer m2c names without declaring. None of them can ever be
# supplied by a header, so none of them belongs in a declaration bucket.
M2C_DIALECT = frozenset({"bitwise", "unaligned", "sp"})


def sha256_text(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# THE TWO DIAGNOSTICS ARE NOT THE SAME FAULT, and the first version of this module merged them:
#   `unknown type name 'X'`        X is used in TYPE position  -> a declaration, an alias, or a layout
#   `use of undeclared identifier 'X'`  X is used as a VALUE   -> a symbol, a macro, or an m2c spelling
# Merging them put `bitwise` -- m2c's reinterpretation spelling, not a type at all -- into
# `opaque-is-enough`, and would have produced a "declare an opaque struct" repair for a value. The kind is
# recorded per name now and it decides which questions get asked.
TYPE_POSITION = "type-position"
VALUE_POSITION = "value-position"


def undefined_names(errors: list) -> dict:
    """`name -> {"kind", "messages"}` from the frontend's own wording, never from a guess."""
    found: dict = {}
    for error in errors or []:
        what = error.get("what") or ""
        for pattern, kind in ((UNKNOWN_TYPE, TYPE_POSITION), (UNDECLARED, VALUE_POSITION)):
            match = pattern.search(what)
            if not match:
                continue
            entry = found.setdefault(match.group(1), {"kind": kind, "messages": []})
            # A name seen in both positions is a TYPE: the stronger requirement is the one to plan for.
            if kind == TYPE_POSITION:
                entry["kind"] = TYPE_POSITION
            if len(entry["messages"]) < 2:
                entry["messages"].append(what[:160])
    return found


def dereferenced(source: str, name: str) -> bool:
    """Is this name USED as a value, or only ever pointed at?

    A pointer that is declared and passed on needs no body; one that is dereferenced does. The distinction
    is the difference between an opaque declaration and a layout recovery, so it is read from the source
    rather than assumed from the name.
    """
    escaped = re.escape(name)
    patterns = (rf"\b{escaped}\s*->",                      # p->m
                rf"\*\s*{escaped}\b",                      # *p
                rf"\b{escaped}\s*\[",                      # p[i]
                rf"\b{escaped}\s*\.",                      # p.m
                rf"\b{escaped}\s*[-+*/%&|^]\s*[A-Za-z0-9_(]")   # arithmetic
    return any(re.search(pattern, source) for pattern in patterns)


def header_closure(repo: Path, source: str) -> str:
    """The text of every header this candidate already includes, transitively."""
    from solver import buildtypes

    text = ""
    for include in re.findall(r'(?m)^\s*#\s*include\s*[<"]([^>"\n]+)[>"]', source):
        try:
            for path in sorted(buildtypes.closure(Path(repo), "include/" + include)):
                text += path.read_text(errors="replace") + "\n"
        except Exception:                                       # noqa: BLE001
            continue
    return text


def classify(name: str, kind: str, source: str, included: str, everything: str, symbols: set) -> dict:
    """The buckets, in order of how little repair each one needs.

    Which buckets are even possible depends on the KIND of diagnostic: a name in value position cannot be
    repaired by declaring a struct, however the usage looks.
    """
    from solver import typedecl

    types = set(typedecl.typedefs(included))
    tags = set(re.findall(r"\b(?:struct|union|enum)\s+(\w+)", included))
    all_types = set(typedecl.typedefs(everything))
    all_tags = set(re.findall(r"\b(?:struct|union|enum)\s+(\w+)", everything))
    used = dereferenced(source, name)

    if kind == VALUE_POSITION:
        if name in M2C_DIALECT:
            return {"name": name, "kind": kind, "used_as_a_value": True, "reason": "m2c-dialect",
                    "detail": "an m2c dialect spelling the build's C does not have: a REWRITE, not a "
                              "declaration, and no header can supply it"}
        if name in symbols:
            return {"name": name, "kind": kind, "used_as_a_value": True, "reason": "known-function",
                    "detail": "used as a value and the target has a function with this name: an extern "
                              "prototype, from symbol evidence"}
        if ADDRESS_NAMED.match(name) or GLOBAL_NAMED.match(name):
            return {"name": name, "kind": kind, "used_as_a_value": True, "reason": "unresolved-global",
                    "detail": ("the name encodes its own address" if ADDRESS_NAMED.match(name)
                               else "m2c's naming for a translated global") +
                              ": an extern declaration whose type has to come from the target's own "
                              "accesses, which is what `globals_variant` reads"}
        if M2C_TEMP.match(name):
            return {"name": name, "kind": kind, "used_as_a_value": True, "reason": "undeclared-local",
                    "detail": "one of m2c's own temporaries, used without being declared: a draft defect "
                              "in the source, not a missing declaration from a header"}
        if name in all_types or name in all_tags:
            return {"name": name, "kind": kind, "used_as_a_value": True,
                    "reason": "header-exists-not-included",
                    "detail": "a type in include/** with this name; the value use may be a cast or a sizeof"}
        return {"name": name, "kind": kind, "used_as_a_value": True, "reason": "unknown-identifier",
                "detail": "used as a value, no symbol, no type and no known m2c spelling: needs a person "
                          "or a rule before it gets a repair"}
    if name in all_types:
        reason, detail = ("header-exists-not-included",
                          "declared in include/** but not reached through this candidate's includes")
    elif name in tags or name in all_tags:
        reason, detail = ("tag-without-alias", f"`struct {name}` is known; `{name}` is not a type name")
    elif name in symbols:
        reason, detail = ("known-function",
                          "the target has a function with this name: an extern prototype, from symbol "
                          "evidence")
    elif used:
        reason, detail = ("needs-layout",
                          "dereferenced or used as a value, so a body or a proper type is required")
    else:
        reason, detail = ("opaque-is-enough",
                          "only ever pointed at or passed on; a tag declaration is sufficient")
    return {"name": name, "kind": kind, "used_as_a_value": used, "reason": reason, "detail": detail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    from solver import frontend_diagnostics as frontend

    frame = json.loads(args.frame.read_text(encoding="utf-8"))
    rows = [row for row in frame["rows"] if not row["sequence"].get("frontend_passed")]
    if args.limit:
        rows = rows[: args.limit]

    everything = header_closure(args.repo, '#include "common.h"\n')
    for path in sorted((args.repo / "include").rglob("*.h")) if (args.repo / "include").is_dir() else []:
        try:
            everything += path.read_text(errors="replace") + "\n"
        except OSError:
            continue
    conn = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
    try:
        symbols = {name for (name,) in conn.execute("select name from functions") if name}
        results, reasons = [], Counter()
        for row in rows:
            name, digest = row["function"], row["sequence"].get("final_sha256")
            found = conn.execute("select source_code from attempts where source_sha256 = ? "
                                 "and source_code is not null limit 1", (digest,)).fetchone()
            if not found or sha256_text(found[0]) != digest:
                results.append({"function": name, "status": "candidate-not-recoverable"})
                continue
            source = found[0]
            target = None
            try:
                from eval.tool_agent_run import build_context
                context, _why = build_context(args.repo, name)
                target = str(context.target) if context is not None else None
            except Exception:                                   # noqa: BLE001
                target = None
            report = frontend.analyse(source, repo=args.repo, target=target or "")
            if report["status"] == "unavailable":
                results.append({"function": name, "status": "frontend-unavailable",
                                "reason": report.get("reason")})
                continue
            included = header_closure(args.repo, source)
            names = undefined_names(report["errors"])
            entries = []
            for symbol, info in sorted(names.items()):
                entry = classify(symbol, info["kind"], source, included, everything, symbols)
                entry["messages"] = info["messages"]
                reasons[entry["reason"]] += 1
                entries.append(entry)
            results.append({"function": name, "status": "triaged",
                            "unresolved_names": len(entries), "entries": entries})
    finally:
        conn.close()

    payload = {"schema_version": SCHEMA_VERSION, "kind": "name-triage", "frame": str(args.frame),
               "states_triaged": sum(1 for row in results if row.get("status") == "triaged"),
               "unresolved_names": sum(reasons.values()),
               "by_reason": {reason: reasons.get(reason, 0) for reason in REASONS},
               "note": ("a count is a measurement of the RESIDUAL, not a distance and not a difficulty "
                        "ranking. `opaque-is-enough` is a declaration; `needs-layout` is inference."),
               "states": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "states"}, indent=2))
    print("written", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
