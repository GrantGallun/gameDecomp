"""Does a fabricated training sample reproduce the fault a real function actually has?

The self-improvement loop lets a model find a weakness and fabricate data to patch it. The failure
that loop invites is data that is about the weakness in NAME only: the model says "I am weak at
jump tables", writes switches, gets good at them, and the real residual was a jump table inside a
loop with a spilled index. Training looks like progress and nothing real improves.

This gate ties fabricated data to the real failure. From an oracle diff it takes the TARGET
instructions around every mismatch, abstracts them to shapes (registers, immediates and branch
targets erased; `sp`, `ra`, `at`, `zero`, trap codes kept), and forms k-grams. A sample reproduces
the residual only if its own assembly contains those k-grams -- and at least one of the shared
k-grams must be INFORMATIVE, i.e. rare in a background corpus, so `nop; jr ra` never counts.

Deterministic, model-free. It is a gate, not a judge: passing says the sample exercises the shape,
not that training on it will help. Whether it helped is measured on real validation afterwards.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

K = 3
MIN_COVERAGE = 0.25       # fraction of the residual's k-grams the sample must contain
MAX_DOCUMENT_FREQUENCY = 0.05   # a shared k-gram counts as informative below this background rate

KEEP_REGISTERS = {"sp", "ra", "at", "zero"}
REGISTERS = {"zero", "at", "v0", "v1", "a0", "a1", "a2", "a3", "gp", "sp", "fp", "ra", "k0", "k1",
             *(f"t{i}" for i in range(10)), *(f"s{i}" for i in range(9))}
BRANCHES = {"b", "beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz", "beql", "bnel",
            "beqzl", "bnezl", "bgezl", "blezl", "bgtzl", "bltzl", "bc1t", "bc1f", "bc1tl", "bc1fl",
            "j", "bgezal", "bltzal"}
NUMBER = re.compile(r"^-?(?:0x[0-9a-fA-F]+|\d+)$")
MEMORY = re.compile(r"^(-?(?:0x[0-9a-fA-F]+|\d+)|%\w+\([^)]*\))\((\$?\w+)\)$")
OBJDUMP_INSN = re.compile(r"^\s*[0-9a-f]+:\s+([a-z][a-z0-9.]*)\s*(.*)$")


def _register(token: str) -> str:
    name = token.lstrip("$")
    return name if name in KEEP_REGISTERS else "R"


def shape(op: str, operands: str) -> str:
    """One instruction with everything allocation- or address-specific erased."""
    op = op.strip()
    if op == "break":                       # trap codes distinguish 0x7 (div by 0) from 0x6
        return f"break {operands.strip()}"
    # `ra` is structural only as the return address: `jr ra` and its sp-relative save/restore.
    # In a leaf IDO allocates it as scratch, so `addu ra,t6,t7` is a register choice. Kept literal,
    # drawMenuTextureByAssetId's allocation residual showed up as five "rare" shape faults.
    if not (op == "jr" or (op in ("sw", "lw") and "(sp)" in operands)):
        operands = re.sub(r"(?<![\w$])\$?ra\b", "t9", operands)
    parts = [p.strip() for p in operands.split(",") if p.strip()] if operands.strip() else []
    if op in BRANCHES and parts:
        parts[-1] = "L"
    if op == "jal" and parts:
        return "jal F"
    out = []
    for part in parts:
        part = re.sub(r"\s*<[^>]*>$", "", part)      # objdump's `68 <fn+0x68>` target label
        mem = MEMORY.match(part)
        if part == "L":
            out.append("L")
        elif mem:
            out.append(f"#({_register(mem.group(2))})")
        elif part.lstrip("$") in REGISTERS:
            out.append(_register(part))
        elif re.fullmatch(r"\$?f\d+", part):
            out.append("F")                 # FPU register; allocation-specific like the rest
        elif NUMBER.match(part) or part.startswith("%"):
            out.append("#")
        else:
            out.append("?")
    return f"{op} {','.join(out)}".strip()


def _split(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text == "...":
        return None
    fields = text.split(None, 1)
    return fields[0], fields[1] if len(fields) > 1 else ""


def listing_shapes(listing: Iterable[str]) -> list[str]:
    """Shapes of an `objdump -d` function listing, relocation and elision lines skipped."""
    shapes = []
    for line in listing:
        m = OBJDUMP_INSN.match(line)
        if m:
            shapes.append(shape(m.group(1), m.group(2)))
    return shapes


def _hunks(diff: str) -> list[tuple[list[str], list[str], list[bool]]]:
    """(target shapes, candidate shapes, target-line-was-`-`) per hunk. Context is on both sides."""
    hunks: list[tuple[list[str], list[str], list[bool]]] = []
    for line in (diff or "").splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@") or not hunks:
            hunks.append(([], [], []))
            if line.startswith("@@"):
                continue
        marker, body = (line[0], line[1:]) if line[:1] in ("-", "+", " ") else (" ", line)
        split = _split(body)
        if split is None:
            continue
        s = shape(*split)
        if marker in ("-", " "):
            hunks[-1][0].append(s)
            hunks[-1][2].append(marker == "-")
        if marker in ("+", " "):
            hunks[-1][1].append(s)
    return [h for h in hunks if h[0] or h[1]]


def residual_shapes(diff: str) -> tuple[list[str], set[int], set[int], int]:
    """(target shapes, SHAPE-mismatched target indices, hunk starts, register/immediate-only lines).

    A target line is mismatched only where the aligned target and candidate SHAPES differ. A
    `-`/`+` pair differing only in register or immediate has one shape on both sides and is not a
    shape fault. Counting every `-` line got calculateRaceTimerDelta (attempt 2926) wrong twice:
    its division-trap sequence is present on BOTH sides in different registers, yet it was read as
    three structural components that a division probe then "reproduced". Such lines are counted
    and returned instead, because a shape gate cannot validate data for them and must say so.
    Alignment, not a multiset, so an instruction that moved across a delay slot still counts.
    """
    import difflib
    shapes: list[str] = []
    mismatched: set[int] = set()
    boundaries: set[int] = set()
    register_only = 0
    for target, candidate, was_minus in _hunks(diff):
        base = len(shapes)
        boundaries.add(base)
        matcher = difflib.SequenceMatcher(None, target, candidate, autojunk=False)
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag != "equal":
                mismatched.update(range(base + i1, base + i2))
            else:
                register_only += sum(was_minus[i1:i2])
        shapes.extend(target)
    return shapes, mismatched, boundaries, register_only


def _grams(shapes: list[str], k: int, boundaries: set[int] = frozenset()) -> list[tuple[str, ...]]:
    out = []
    for i in range(len(shapes) - k + 1):
        if any(b in boundaries for b in range(i + 1, i + k)):
            continue
        out.append(tuple(shapes[i:i + k]))
    return out


def residual_grams(diff: str, k: int = K) -> set[tuple[str, ...]]:
    """k-grams of target shapes that contain at least one mismatched instruction."""
    shapes, mismatched, boundaries, _register_only = residual_shapes(diff)
    grams = set()
    for i in range(len(shapes) - k + 1):
        if any(b in boundaries for b in range(i + 1, i + k)):
            continue
        if any(j in mismatched for j in range(i, i + k)):
            grams.add(tuple(shapes[i:i + k]))
    return grams


def normalized_shapes(lines: Iterable[str]) -> list[str]:
    """Shapes of a workspace `*_object_dump_normalized.s` (`op    operands` per line)."""
    return [shape(*split) for split in map(_split, lines) if split is not None]


@dataclass(frozen=True)
class Background:
    """How common each instruction shape and k-gram is, over whole functions."""
    shapes: Counter
    grams: Counter
    functions: int

    def shape_rate(self, s: str) -> float:
        return self.shapes[s] / self.functions if self.functions else 0.0


def document_frequency(shape_lists: Iterable[list[str]], k: int = K) -> Background:
    shapes: Counter = Counter()
    grams: Counter = Counter()
    total = 0
    for seq in shape_lists:
        total += 1
        shapes.update(set(seq))
        grams.update(set(_grams(seq, k)))
    return Background(shapes, grams, total)


def game_background(repo, exclude: set[str] = frozenset(), k: int = K) -> Background:
    """Frequencies over the game's own TARGET assembly.

    Rarity must be judged against real game code. Judged against the synthetic corpus -- which
    never multiplies -- `mflo R` looked rare. Target dumps are derived from the binary (evidence
    tier), never from reference C. Pass the sealed held-out names in `exclude`.
    """
    from pathlib import Path
    dumps = sorted(Path(repo).glob("nonmatchings/*/target_object_dump_normalized.s"))
    return document_frequency(
        (normalized_shapes(p.read_text().splitlines()) for p in dumps if p.parent.name not in exclude), k)


def components(diff: str, background: Background, k: int = K,
               max_rate: float = MAX_DOCUMENT_FREQUENCY) -> list[dict]:
    """The residual split into independent faults, each with the k-grams that can witness it.

    One real residual is usually several faults, so a sample is judged per fault, never against
    the whole residual. Mismatched target instructions fewer than k apart, inside one hunk, form
    one component.

    RARITY BELONGS TO THE FAULT, NOT ITS CONTEXT. A component is shaped only if one of its own
    mismatched instructions is rare in game code, and only k-grams containing that rare instruction
    (`anchors`) can witness it. The first version accepted any rare k-gram overlapping the fault:
    `Flength`'s whole fault was one `addiu R,R,#`, rare only through its neighbours, and dense-switch
    samples "covered" 18 residuals of which 0 had a jump table.
    """
    shapes, mismatched, boundaries, _register_only = residual_shapes(diff)
    groups: list[list[int]] = []
    for i in sorted(mismatched):
        last = groups[-1][-1] if groups else None
        if last is not None and i - last < k and not any(b in boundaries for b in range(last + 1, i + 1)):
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    for group in groups:
        rare = {i for i in group if background.shape_rate(shapes[i]) <= max_rate}
        grams, anchors = set(), set()
        for start in range(max(0, group[0] - k + 1), min(len(shapes) - k, group[-1]) + 1):
            if any(b in boundaries for b in range(start + 1, start + k)):
                continue
            window = range(start, start + k)
            if any(j in group for j in window):
                gram = tuple(shapes[start:start + k])
                grams.add(gram)
                if any(j in rare for j in window):
                    anchors.add(gram)
        out.append({"span": (group[0], group[-1]), "shapes": shapes[group[0]:group[-1] + 1],
                    "rare_shapes": sorted({shapes[i] for i in rare}),
                    "grams": grams, "anchors": anchors})
    return out


@dataclass(frozen=True)
class ComponentVerdict:
    span: tuple[int, int]
    shapes: tuple[str, ...]
    status: str                  # reproduced | not-reproduced | unshaped
    coverage: float
    anchors_shared: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Verdict:
    reproduces: bool             # at least one shaped component is reproduced
    components: tuple[ComponentVerdict, ...]
    reason: str
    register_only_lines: int = 0  # residual a shape gate cannot validate data for

    @property
    def reproduced(self) -> tuple[ComponentVerdict, ...]:
        return tuple(c for c in self.components if c.status == "reproduced")


def sample_grams(listing: Iterable[str], k: int = K) -> frozenset:
    """A sample's k-grams, computed once so one sample can be gated against many residuals."""
    return frozenset(_grams(listing_shapes(listing), k))


def judge(part: dict, have: frozenset, min_coverage: float = MIN_COVERAGE) -> tuple[str, float, set]:
    """(status, coverage, shared anchors) for one component against one sample's k-grams."""
    if not part["anchors"]:
        return "unshaped", 0.0, set()
    shared = part["anchors"] & have
    coverage = len(part["grams"] & have) / len(part["grams"])
    return ("reproduced" if shared and coverage >= min_coverage else "not-reproduced"), coverage, shared


def check(diff: str, sample_listing: Iterable[str] | frozenset, background: Background,
          k: int = K, min_coverage: float = MIN_COVERAGE,
          max_rate: float = MAX_DOCUMENT_FREQUENCY) -> Verdict:
    """Gate one fabricated sample against each fault in one real residual.

    An `unshaped` component has no rare instruction of its own: a register, immediate or common
    instruction difference that no sample can be shown to target by shape. It is reported, never
    counted as reproduced and never as a corpus gap.
    """
    parts = components(diff, background, k, max_rate)
    register_only = residual_shapes(diff)[3]
    if not parts:
        reason = ("residual is register/immediate only; no shape to reproduce" if register_only
                  else "residual has no mismatched target instructions")
        return Verdict(False, (), reason, register_only)
    have = sample_listing if isinstance(sample_listing, frozenset) else sample_grams(sample_listing, k)
    verdicts = []
    for part in parts:
        status, cov, shared = judge(part, have, min_coverage)
        verdicts.append(ComponentVerdict(part["span"], tuple(part["shapes"]), status,
                                         round(cov, 3), tuple(sorted(shared))))
    reproduced = sum(v.status == "reproduced" for v in verdicts)
    shaped = sum(v.status != "unshaped" for v in verdicts)
    reason = (f"reproduces {reproduced} of {shaped} shaped components" if shaped
              else "no component has a rare instruction of its own")
    return Verdict(reproduced > 0, tuple(verdicts), reason, register_only)


# --- corpus coverage of real residuals ---------------------------------------

def coverage(residuals: dict[str, str], samples: list[tuple[str, frozenset]],
             background: Background, k: int = K) -> dict:
    """Which real faults the corpus can supply data for, per family, and which are gaps.

    `residuals` maps a function to its best compiled non-exact oracle diff. A shaped component
    is COVERED when at least one sample reproduces it. The per-family hit lists exist for a
    precision audit: a dense-switch family matching residuals with no jump table is a gate bug.
    """
    report = {"residuals": len(residuals), "register_only_residuals": 0,
              "shaped_components": 0, "covered_components": 0,
              "unshaped_components": 0, "families": {}, "gaps": [], "hits": {}}
    for family in sorted({f for f, _ in samples}):
        report["families"][family] = {"residuals_hit": 0, "components_hit": 0}
        report["hits"][family] = []
    for name in sorted(residuals):
        diff = residuals[name]
        parts = components(diff, background, k)
        if not parts and residual_shapes(diff)[3]:
            report["register_only_residuals"] += 1
            continue
        hit_families = set()
        for part in parts:
            if not part["anchors"]:
                report["unshaped_components"] += 1
                continue
            report["shaped_components"] += 1
            covered_by = {family for family, have in samples if judge(part, have)[0] == "reproduced"}
            if covered_by:
                report["covered_components"] += 1
                for family in covered_by:
                    report["families"][family]["components_hit"] += 1
                hit_families |= covered_by
            else:
                report["gaps"].append({"function": name, "rare_shapes": part["rare_shapes"],
                                       "shapes": part["shapes"][:12]})
        for family in hit_families:
            report["families"][family]["residuals_hit"] += 1
            report["hits"][family].append(name)
    report["top_gap_shapes"] = Counter(s for g in report["gaps"] for s in g["rare_shapes"]).most_common(15)
    return report


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sqlite3
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Corpus coverage of real residual components")
    ap.add_argument("--kb", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--corpus", type=Path, required=True, help="tools/synthetic_corpus.py JSONL")
    ap.add_argument("--exclude-sets", nargs="*", default=[], help="eval set JSONs whose heldout is excluded")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    held: set[str] = set()
    for path in args.exclude_sets:
        held |= {r["function"] for r in json.loads(Path(path).read_text()).get("heldout", [])}
    conn = sqlite3.connect(f"file:{args.kb.expanduser()}?mode=ro", uri=True)
    # Game code only, by clean_set's own library exclusion. libultra builds with another recipe
    # (-mips2): the first report's float gaps were all `__cosf` `ldc1`, an instruction the game's
    # -mips1 recipe cannot emit, so no game-code sample could ever have covered them.
    from eval.clean_set import EXCLUDE_TU
    library = " ".join(f"AND t.name NOT LIKE '{pattern}'" for pattern in EXCLUDE_TU)
    residuals: dict[str, str] = {}
    for name, diff in conn.execute(
            "SELECT f.name, a.diff_summary FROM attempts a JOIN functions f ON f.addr=a.func_addr "
            "JOIN tus t ON t.id=f.tu_id "
            "WHERE a.compiled=1 AND COALESCE(a.exact,0)=0 AND a.diff_summary IS NOT NULL "
            f"AND length(a.diff_summary)>0 AND t.name LIKE '%src/%' {library} "
            "ORDER BY f.name, a.score DESC, a.id"):
        if name not in held and name not in residuals:
            residuals[name] = diff
    samples = []
    for line in args.corpus.read_text().splitlines():
        row = json.loads(line)
        if row.get("compiled"):
            samples.append((row["family"], sample_grams(row["asm"].splitlines())))
    background = game_background(args.repo.expanduser(), held)
    report = coverage(residuals, samples, background)
    report["provenance"] = {"kb": str(args.kb), "corpus": str(args.corpus), "excluded_heldout": len(held),
                            "background_functions": background.functions, "library_tus_excluded": list(EXCLUDE_TU), "k": K,
                            "min_coverage": MIN_COVERAGE, "max_document_frequency": MAX_DOCUMENT_FREQUENCY}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    summary = {k: v for k, v in report.items() if k not in ("gaps", "hits")}
    summary["gap_examples"] = report["gaps"][:3]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
