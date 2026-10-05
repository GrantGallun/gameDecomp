"""Build verified repair tasks from generated C, with the generator's answer kept hidden.

WHAT A RECORD CONTAINS, AND WHAT IT MUST NOT
--------------------------------------------
A task is: a real compiler target, a candidate C that is known to be wrong, and the compiler's
own feedback about that candidate. The solver is given `assembly + candidate + feedback` and
must emit better C.

The generator's clean source IS the answer. It is the only known-correct repair, and it must
therefore never appear in `input` -- not in the candidate, not in the feedback, not in a hint.
`generator_source` is stored on the record for AUDIT and for computing the split groups, and
`leakage_report` proves mechanically that it does not occur anywhere in the solver-visible part
of the record. `tests/test_repair_dataset_synth.py` asserts the same property independently.

VERIFICATION IS THE COMPILER'S
------------------------------
`parent.score` and `child.score` are never guessed. The target object is produced by compiling
the generator's C with the game's real IDO recipe; the parent object by compiling the damaged C
the same way; and the child's verdict is `solver.byte_certificate.certify` over the two objects
(`certify_exact` below), not a similarity score.

THAT VERDICT IS NOT `.text` EQUALITY, AND THE DIFFERENCE IS NOT ACADEMIC. An earlier version of
this module called two objects equal when their `.text` bytes were equal, and `.text` carries no
relocations -- so a candidate that calls `external_b` where the target calls `external_a`
compiled to the SAME `.text` and was recorded as a verified repair. Measured with the real recipe
(`eval/results/local-posttraining-20260920/VERIFIER-FIX.md`): identical `.text`,
`R_MIPS_26 external_a` versus `external_b` at offset 0x8, and `certify` refuses it. `code_image`
remains as a DIAGNOSTIC -- instruction bytes are what a human reads -- but no `exact` verdict in
this repository may rest on it.

`certify` is also why the oracle can be used at all where a whole-object comparison could not be:
IDO objects are not byte-reproducible (the absolute source path and the random `asm_processor`
temp filename are embedded), those bytes live in NON-allocated sections, and `certify` never
reads them. Measured both ways in one probe: the same C compiled into two differently-named
directories produced 1416- and 1476-byte objects differing at byte 35, and `certify` reports
`exact` -- correctly, because the code and its relocations are the same.

FAILED BRANCHES ARE KEPT
------------------------
`child.exact` is frequently False -- most damaged candidates are not repaired by anything in
this repository yet. Those records are retained with `outcome: "unrepaired"` and with the
parent's own compile result, because they are the negative half of the task and because
dropping them would make the yield look better than it is.

WHAT IS NOT VERIFIED HERE
-------------------------
Byte-identical OBJECT SECTIONS are not the same claim as a byte-identical FUNCTION in the game
binary: these are generated functions in a standalone translation unit, so exactness is
object-level and is reported as such. `solver/byte_certificate.certify` is the oracle used here,
and its own scope statement travels with every verdict: allocated text/data/BSS sections and
their relocation expressions under the same link environment, excluding debug and ABI metadata
and the final link layout, with `whole_rom_verified` always False. The ROM-backed function-level
claim is a different, stronger certificate (`solver/function_boundary.certify`) and is not made
here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = 2
# The MANIFEST has its own version because it is the freeze, not a record. Version 3 added the
# per-record content hashes and the dataset file digest; a version-2 manifest cannot bind the
# records it names, so `eval.evaluate_source_repair.load_frozen_tasks` refuses it explicitly
# rather than evaluating a panel that could have been edited after it was frozen.
MANIFEST_SCHEMA_VERSION = 3
OBJDUMP = "mips-linux-gnu-objdump"
DEFAULT_TARGET = "build/src/race/ui/race_ui_effects.o"

INPUT_FIELDS = ("assembly", "candidate", "feedback")


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- what a freeze binds -------------------------------------------------------

def canonical_record(task: dict) -> str:
    """One record as a canonical string: `sort_keys`, so field order cannot change the digest."""
    return json.dumps(task, sort_keys=True)


def answer_source(task: dict) -> str:
    """The answer this record is supervised with.

    `child.source_c` is the verified repair; `generator_source` is the same string kept for audit.
    Either may be absent on a record that was never verified, and an absent answer is "".
    """
    child = task.get("child") or {}
    return child.get("source_c") or task.get("generator_source") or ""


def record_hashes(task: dict) -> dict:
    """The digests a frozen manifest binds one record to.

    THREE digests, because a swap has three shapes and each of them was possible before:

      `content`  the whole record, so ANY post-freeze edit is caught;
      `input`    what the solver is shown (`assembly`, `candidate`, `feedback`) -- the audit's
                 swap replaced `input.candidate` with the hidden answer, and the loader accepted
                 it, which turned a held-out task into a copy of its own solution;
      `answer`   the supervision the record carries, so the answer cannot be replaced either.

    A hash of the record is not a hash of "the code" -- it does not claim the record is correct,
    only that it is the one that was frozen.
    """
    return {"content": sha_text(canonical_record(task)),
            "input": sha_text(canonical_record(task.get("input") or {})),
            "answer": sha_text(answer_source(task))}


def dataset_bytes(tasks: list[dict]) -> bytes:
    """The dataset file's exact bytes: one compact JSON object per line, newline-terminated.

    The freeze records the digest of THIS function's output, and `build` writes the same bytes, so
    "the dataset file's digest" is a checkable claim rather than a convention. Serialising the
    rows twice by two different code paths is how a manifest ends up attesting to a file it was
    not built from.
    """
    return ("\n".join(json.dumps(task) for task in tasks) + "\n").encode("utf-8")


# --- the recipe and the compiler ----------------------------------------------

def resolve_recipe(repo: Path, target: str = DEFAULT_TARGET) -> dict:
    """The game's own IDO recipe for a game-code TU, with hashes for the manifest."""
    from solver import compiler_recipe
    resolved = compiler_recipe.resolve(repo, target)
    provenance = {key: resolved.get(key) for key in
                  ("target", "makefile_sha256", "projection_sha256")}
    provenance["command_sha256"] = sha_text(json.dumps(resolved["command"]))
    provenance["command"] = resolved["command"]
    provenance["compiler"] = "ido-5.3"
    return {"resolved": resolved, "provenance": provenance}


def compile_unit(repo: Path, resolved: dict, name: str, source: str, work: Path) -> dict:
    """Compile one C unit with the game's recipe. Returns the object bytes and stderr."""
    unit = work / f"{name}.c"
    obj = work / f"{name}.o"
    unit.write_text(source, encoding="utf-8")
    started = time.monotonic()
    try:
        proc = subprocess.run([*resolved["command"], "-o", str(obj), str(unit)],
                              cwd=repo, capture_output=True, text=True, timeout=180)
        code, stderr = proc.returncode, proc.stderr or ""
    except subprocess.TimeoutExpired:
        return {"compiled": False, "stderr": "compile timed out after 180s", "object": None,
                "compile_ms": 180000}
    data = obj.read_bytes() if (code == 0 and obj.exists()) else None
    return {"compiled": data is not None, "stderr": stderr[-4000:], "object": data,
            "compile_ms": int((time.monotonic() - started) * 1000)}


def disassemble(obj_path: Path, name: str) -> tuple[str, int]:
    """The function's instructions, and how many. Empty when the unit defines no such symbol."""
    from tools.synthetic_corpus import function_listing
    try:
        dump = subprocess.run([OBJDUMP, "-dr", "--no-show-raw-insn", str(obj_path)],
                              capture_output=True, text=True, timeout=120, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return "", 0
    listing = function_listing(dump, name)
    return "\n".join(listing), len(listing)


TEXT_SECTION = ".text"


def code_image(obj_path: Path, *, section: str = TEXT_SECTION) -> bytes | None:
    """The CODE the compiler emitted: the `.text` section's bytes.

    A DIAGNOSTIC AND A DIFF SUBJECT -- NOT AN EXACTNESS VERDICT. Use `certify_exact` for that.

    Why the whole object is not the subject: IDO object builds are not byte-reproducible. The
    same C compiled twice from the same directory produced a 1480-byte and a 1484-byte object
    whose first difference was at byte 35, because the object embeds the absolute source path and
    the `asm-processor` temporary filename, which is random per invocation:
        /tmp/asm_processorz0pp2_7h/preprocessed_c9e82aeffb294aa1b75d5c040b595c47.c
        /tmp/asm_processorbnm802k7/preprocessed_2cad60feead241f590e9e02c875cd239.c

    Why `.text` is ALSO not the subject: it carries no relocations, so two functions differing
    only in their callee are byte-identical in `.text`. Measured with the real recipe --
    `return external_a(x);` versus `return external_b(x);` both produce
    `27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000` with a differing
    `R_MIPS_26` at offset 0x8. An independent audit caught this: the `.text` comparison called
    them exact while `solver.byte_certificate.certify` correctly called them different. So `.text`
    equality was a **false positive** for a changed call target, which is exactly the kind of
    "repair" this dataset must never certify.
    """
    try:
        proc = subprocess.run(["mips-linux-gnu-objcopy", "-O", "binary", "--only-section",
                               section, str(obj_path), "/dev/stdout"],
                              capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or None


def certify_exact(target_obj: Path, candidate_obj: Path, *, source: str,
                  build_inputs: dict | None = None) -> dict:
    """THE exactness verdict: the project's own object/relocation certificate.

    Delegates to `solver.byte_certificate.certify`, which compares allocated text/data/BSS
    sections (ignoring the unstable debug and ABI metadata that made whole-object comparison
    useless) AND relocation expressions -- so a changed callee or a changed referenced global is
    caught. Its scope is its own and is carried through here rather than paraphrased: same link
    environment, relocations resolved as expressions, not a whole-ROM or final-link claim.

    Returning the receipt rather than a bool keeps `status` and `error` visible: a certificate
    that could not be computed reports `unverified`, which must never be read as "not exact" or
    as "exact".
    """
    from solver import byte_certificate
    return byte_certificate.certify(target_obj, candidate_obj, source=source,
                                    build_inputs=build_inputs or {})


def object_diff(target: bytes, candidate: bytes, *, limit: int = 4000) -> dict:
    """The measured before/after comparison. Byte equality is the verdict; the rest is context.

    Length is reported separately from the first differing offset because an object that is
    longer AND differs is a different failure from one that differs at a single byte, and the
    project's own instruction-level diffs came from exactly this distinction.
    """
    if target == candidate:
        return {"identical": True, "target_sha256": sha_bytes(target),
                "candidate_sha256": sha_bytes(candidate),
                "target_bytes": len(target), "candidate_bytes": len(candidate),
                "first_difference": None, "differing_bytes": 0}
    shared = min(len(target), len(candidate))
    differing = sum(1 for i in range(shared) if target[i] != candidate[i])
    first = next((i for i in range(shared) if target[i] != candidate[i]), shared)
    return {"identical": False, "target_sha256": sha_bytes(target),
            "candidate_sha256": sha_bytes(candidate),
            "target_bytes": len(target), "candidate_bytes": len(candidate),
            "first_difference": first,
            "differing_bytes": differing + abs(len(target) - len(candidate))}


# --- the compiler feedback the solver is allowed to see ------------------------

# The candidate's own compile diagnostic, or its instruction diff against the target. Both are
# derived from the TARGET and the CANDIDATE only. Nothing here can see the generator's source.
MAX_FEEDBACK_CHARS = 4000


def feedback_for(*, parent_compiled: bool, stderr: str, target_asm: str, parent_asm: str) -> dict:
    """The compiler's own words about the candidate, in the project's existing convention."""
    if not parent_compiled:
        return {"kind": "compile-error",
                "text": (stderr or "(no diagnostic)")[-MAX_FEEDBACK_CHARS:]}
    diff = instruction_diff(target_asm, parent_asm)
    return {"kind": "instruction-diff", "text": diff[-MAX_FEEDBACK_CHARS:] or "(no differences)"}


def instruction_diff(target_asm: str, candidate_asm: str, *, context: int = 0) -> str:
    """`-` target, `+` candidate, over instruction MNEMONICS with operands.

    Uses `difflib` over the listing lines so the format matches what `solver/refine` already
    feeds the model (`-` = target expects, `+` = this attempt produced).
    """
    import difflib
    left = [line.strip() for line in target_asm.splitlines() if line.strip()]
    right = [line.strip() for line in candidate_asm.splitlines() if line.strip()]
    return "\n".join(difflib.unified_diff(left, right, lineterm="", n=context))


# --- the task -----------------------------------------------------------------

@dataclass
class Task:
    """One repair task, with the audit material kept separate from the solver-visible input."""

    task_id: str
    family: str
    seed: int
    mutation: str
    fault_axis: str
    function: str
    group: str                     # split group: family for synthetic material
    source_kind: str               # "synthetic"
    input: dict                    # assembly, candidate, feedback -- what the solver sees
    parent: dict                   # the candidate's real compiler result
    target: dict                   # the hidden answer's compiler result
    child: dict                    # the verified repair, when one exists
    generator_source: str = ""     # AUDIT ONLY -- must not reach `input`
    provenance: dict = field(default_factory=dict)

    def visible_text(self) -> str:
        """Everything the solver is shown, concatenated, for the leakage check."""
        return "\n".join(str(self.input.get(key) or "") for key in INPUT_FIELDS)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        return out


def build_task(*, family: str, seed: int, mutation: str, candidate_source: str,
               clean_source: str, function: str, compiled: dict) -> dict | None:
    """Assemble one task from already-measured compile results.

    `compiled` maps a role ("target", "parent") to `compile_unit` output plus its disassembly.
    Returns None when the material cannot make a usable task, and the caller counts the reason.
    """
    target = compiled.get("target")
    parent = compiled.get("parent")
    if not target or not parent or target.get("code") is None:
        return None
    comparison = object_diff(target["code"], parent["code"]) if parent.get("code") else None
    if comparison and comparison["identical"]:
        # A candidate that already matches is not a repair task. This is the no-op case the
        # mutation probe measures; if it ever appears here the catalog has a bug.
        return None
    from eval.repair_mutations import MUTATIONS
    spec = MUTATIONS[mutation]
    feedback = feedback_for(parent_compiled=parent["compiled"], stderr=parent["stderr"],
                            target_asm=target["asm"], parent_asm=parent["asm"])
    # THE VERIFIED CHILD. The generator's source is the answer AND a genuine repair: it is a
    # DIFFERENT string from the candidate, and it compiles -- same recipe, same working
    # directory -- to the target object byte for byte. That is the compiler's own verdict, not a
    # label we assigned, which is what lets `child.exact` be True without qualifying it.
    #
    # Storing it on the record is supervision, not leakage. The leakage rule is about `input`:
    # the solver must not be SHOWN the answer while being asked to produce it. Withholding the
    # target from the record would leave nothing to train on, because a JSONL of tasks with no
    # answer is a benchmark, not a dataset.
    # The clean source's own object, produced in a second compile rather than assumed equal to
    # the target's: a compile is an empirical act, and asserting its result is how this project
    # got its analysis bugs.
    child_run = compiled.get("child")
    child_object_path = child_run.get("object_path") if child_run else None
    target_object_path = target.get("object_path")
    # THE VERDICT IS THE CERTIFICATE, NOT `.text`. `.text` equality accepts a changed callee
    # (measured: `external_a` vs `external_b` are byte-identical in `.text`), which would certify
    # a candidate that calls the wrong function. `certify_exact` compares allocated sections AND
    # relocation expressions, so it catches that, and it tolerates the unstable debug/ABI
    # metadata that made whole-object comparison report a mismatch on every task.
    verdict = (certify_exact(target_object_path, child_object_path, source=clean_source)
               if (target_object_path and child_object_path) else
               {"exact": False, "status": "unverified",
                "error": "the target or child object is missing"})
    child_matches_target = bool(verdict.get("exact"))
    task = Task(
        task_id=f"syn:{family}:{seed}:{mutation}",
        family=family, seed=seed, mutation=mutation, fault_axis=spec.fault.axis,
        function=function, group=family, source_kind="synthetic",
        input={"assembly": target["asm"], "candidate": candidate_source, "feedback": feedback},
        parent={"compiled": parent["compiled"], "stderr": parent["stderr"],
                "score": score_of(comparison, parent["compiled"]),
                "code_sha256": sha_bytes(parent["code"]) if parent.get("code") else None,
                "object_sha256": sha_bytes(parent["object"]) if parent.get("object") else None,
                "instruction_count": parent["instructions"],
                "asm": parent["asm"], "asm_sha256": sha_text(parent["asm"]),
                "same_as_target": bool(comparison and comparison["identical"])},
        target={"source_sha256": sha_text(clean_source), "asm": target["asm"],
                "asm_sha256": sha_text(target["asm"]),
                "code_sha256": sha_bytes(target["code"]) if target.get("code") else None,
                "object_sha256": sha_bytes(target["object"]) if target.get("object") else None,
                "instruction_count": target["instructions"],
                "exactness_scope": verdict.get("scope"),
                "exact": True},
        child={"exact": child_matches_target, "source_c": clean_source,
               "score": 100.0 if child_matches_target else None,
               "code_sha256": sha_bytes(child_run.get("code")) if child_run and child_run.get("code")
               else None,
               "outcome": "verified-repair" if child_matches_target else "certificate-rejected",
               "verification": (
                   "the generator's source was compiled a SECOND time, from a separate directory "
                   "under the SAME filename, and the child's verdict is "
                   "solver.byte_certificate.certify over allocated sections and relocation "
                   "expressions -- NOT `.text` equality, which accepts a changed callee"),
               "certificate": {k: verdict.get(k) for k in
                               ("kind", "status", "scope", "excluded", "exact", "error")},
               "generator_source_sha256": sha_text(clean_source)},
        generator_source=clean_source,
        provenance={"built_at": int(time.time())},
    )
    task.provenance["parent_object_diff"] = comparison
    task.provenance["leakage"] = leakage_check(task)
    return task.as_dict()


def score_of(comparison: dict | None, compiled: bool) -> float:
    """A 0-100 diagnostic. NOT the verdict -- `exact` is, and it is byte equality.

    Reported so a rollout can be described, and deliberately named `score` to match the
    project's existing vocabulary while the README says plainly that similarity is diagnostic.
    """
    if not comparison:
        return 0.0
    if comparison["identical"]:
        return 100.0
    total = max(comparison["target_bytes"], 1)
    return round(100.0 * (total - comparison["differing_bytes"]) / total, 3)


# --- leakage ------------------------------------------------------------------

# What a leaked answer would look like, separated from how SMALL the defect is.
#
# Two things look alike here and are not:
#
#   SHARED CONTEXT -- declarations, the signature, `#include`. The candidate is a damaged copy
#   of the answer, so it legitimately shares these. They are the problem statement, and every
#   real repair candidate has them too.
#
#   THE ANSWER'S OWN LINES IN THE CANDIDATE -- a mutation is a LOCAL edit, so a damaged
#   candidate necessarily still contains most of the answer's body. That is not a leak: the
#   solver is HANDED the candidate, so those lines carry no information it does not already
#   have. It is a measurement of how small the defect is, and it is reported as
#   `answer_overlap_fraction` rather than as a failure. A leak check that fires on every task
#   measures nothing, which is this project's "silent decline" defect wearing a warning label.
#
#   LEAKED ANSWER -- the generator's source, or its digest, appearing anywhere in what the
#   solver sees, or a candidate that IS the answer. Those are failures, and
#   `tests/test_repair_dataset_synth.py` injects each of them to prove this check fires.
_CONTEXT_LINE = re.compile(
    r"^\s*(#|$|\}|\{|\}|extern\b|typedef\b|struct\b|union\b|enum\b|static\b)")


def body_lines(source: str) -> list[str]:
    """Statement lines of a function body: the part that IS the answer.

    Excludes preprocessor lines, bare braces, declarations and the signature. What remains is
    arithmetic and control flow, which is precisely what a mutation changes.
    """
    out = []
    for line in (source or "").splitlines():
        stripped = line.strip()
        if len(stripped) < 8 or _CONTEXT_LINE.match(line):
            continue
        if stripped.endswith(";") and stripped.startswith(("s32 ", "s16 ", "u8 ", "u16 ",
                                                           "s8 ", "u32 ", "f32 ")):
            continue
        if stripped.endswith("{") and "(" in stripped:
            continue                      # the signature
        out.append(stripped)
    return out


# A run of the answer's own words long enough to be unambiguous. Word n-grams rather than a
# character window because generated functions are SHORT: a 48-character window exceeded an
# entire test function, so the check found nothing to compare and stayed silent. Six words clears
# every shared declaration and `return acc;`-sized boilerplate while catching a real quote.
LEAK_NGRAM = 6


def source_ngrams(source: str, width: int = LEAK_NGRAM) -> list[str]:
    """Word n-grams of the source, whitespace-normalised.

    Whitespace-insensitive on purpose: re-indenting or re-wrapping the answer before quoting it
    is still quoting it, and a byte-window check would miss exactly that case.
    """
    words = (source or "").split()
    if len(words) < width:
        return []
    return [" ".join(words[i:i + width]) for i in range(len(words) - width + 1)]


def leakage_check_text(*, answer: str, candidate: str, visible: str) -> dict:
    """The leakage property, stated over ARBITRARY text: does `visible` contain `answer`?

    This is the same check as `leakage_check`, lifted off the `Task` object so it can be run on
    what the model is ACTUALLY handed. The dataset-time check proves the record was clean when it
    was built; it cannot prove the final rendered prompt is clean, because the prompt is built
    later, by other code, from fields that could have been rewritten in between. The audit found
    exactly that gap: a candidate swapped for its hidden answer after freezing is, at the model
    boundary, a prompt that contains the answer.

    `candidate` is the part of `visible` the solver was legitimately handed, and is removed before
    the remainder is searched -- see `leakage_check` for why that distinction is the whole check.
    """
    flat_visible = " ".join((visible or "").split())
    clean = answer or ""
    findings: list[str] = []

    if clean.strip() and clean.strip() in (visible or ""):
        findings.append("full generator source present in input")
    if clean and sha_text(clean) in flat_visible:
        findings.append("generator source hash present in input")

    candidate = (candidate or "").strip()
    if candidate and clean.strip() and candidate == clean.strip():
        findings.append("candidate is byte-identical to the generator source")

    # What the solver sees that it was NOT handed: the feedback (and anything else), with the
    # candidate removed.
    handed = " ".join(candidate.split())
    outside = flat_visible.replace(handed, " ") if handed else flat_visible
    grams = source_ngrams(clean)
    leaked = [g for g in grams if g in outside]
    if leaked:
        findings.append(
            f"{len(leaked)}/{len(grams)} of the answer's {LEAK_NGRAM}-word runs appear outside "
            f"the candidate, i.e. in material the solver was not handed: {leaked[0]!r}")

    return {"clean": not findings, "findings": findings,
            "checked_fields": list(INPUT_FIELDS),
            "answer_ngrams": len(grams),
            "answer_ngrams_outside_the_candidate": len(leaked),
            "leaked_example": leaked[0] if leaked else None,
            "defect_visible_in_candidate": (candidate != clean.strip()) if candidate else False,
            "generator_source_sha256": sha_text(clean) if clean else None}


def leakage_check(task: Task) -> dict:
    """Is the answer present anywhere the solver was NOT handed it?

    The distinction this check has to make, and got wrong twice before it made it:

      * the CANDIDATE is given to the solver, and a mutation is a local edit, so the candidate
        legitimately contains most of the answer. Counting that as a leak flagged every valid
        task;
      * the ASSEMBLY is the target and is meant to be shown. It is machine code and cannot
        contain C text, so scanning it for source is noise;
      * the FEEDBACK is the channel where a leak would actually happen -- a generator's source
        quoted into a diagnostic, a "here is the expected answer" hint, a diff of sources
        rather than of instructions.

    So the check removes everything the solver was legitimately given, and asks whether what
    remains still contains the answer. That is the property, stated directly.
    """
    return leakage_check_text(answer=task.generator_source or "",
                              candidate=(task.input.get("candidate") or ""),
                              visible=task.visible_text())


# --- splits -------------------------------------------------------------------

SYNTHETIC_TEST_FAMILIES_DEFAULT = ("loop_for", "stack_spill", "switch_sparse")


def assign_splits(tasks: list[dict], *, test_families: tuple[str, ...]) -> list[dict]:
    """Split by GROUP: a whole template family goes to one side, never both.

    `TRAINING.md` requires a function's attempts to stay together. The synthetic analogue is
    the FAMILY (and the seed within it): two seeds of one family share a template, so splitting
    by seed would put the same shape on both sides and measure memorisation. Every task also
    carries `assembly_sha256` so the freeze step can confirm that no test assembly appears in
    training under a different family.
    """
    for task in tasks:
        if task["family"] in test_families:
            task["split"] = "test"
        else:
            task["split"] = "train"
    return tasks


def freeze_manifest(tasks: list[dict], *, manifest_path: Path, recipe: dict,
                    test_families: tuple[str, ...], extra: dict | None = None) -> dict:
    """Write the frozen split manifest. Refuses to overwrite an existing one.

    A manifest that can be rewritten after a result is not a frozen split, and this project has
    already paid for that lesson once (`PREREGISTRATION.json` refuses the same way).

    WHAT IT BINDS, AND WHY EACH PART IS HERE
    ----------------------------------------
    Freezing the IDS was not enough. The audit rewrote one task's `input.candidate` to be the
    task's own hidden answer after the freeze and the loader accepted it, because nothing tied
    the manifest to the CONTENT of the records it named. So the manifest now carries:

      `record_hashes`    per task, a digest of the whole record, of the solver-visible input, and
                         of the answer (see `record_hashes`);
      `dataset_sha256`   the digest of the exact dataset file bytes (`dataset_bytes`);
      `dataset_lines`    how many records that file must contain.
    """
    if manifest_path.exists():
        raise SystemExit(
            f"{manifest_path} already exists. A split that can be rewritten after a result is "
            f"not a frozen split; delete it deliberately if the dataset is being rebuilt.")
    # Fill the hashes FIRST, then compare. The first version built the two sets and only then
    # wrote the hashes in, so it intersected two empty sets and reported no collision no matter
    # what the data contained -- a check that can never fail. The empty-string digest it used to
    # produce was the other half of the same bug: both sides hashed an absent `asm` and matched.
    for task in tasks:
        if not task["parent"].get("asm_sha256"):
            task["parent"]["asm_sha256"] = sha_text(task["parent"].get("asm") or "")
        if not task["target"].get("asm_sha256"):
            task["target"]["asm_sha256"] = sha_text(task["target"].get("asm") or "")

    # A manifest that lists one id twice, or lists it in two splits, cannot describe a frozen
    # panel: the loader would silently keep whichever record came last. Refused here, where the
    # cause is visible, rather than at load time where it looks like a data problem.
    ids = [task["task_id"] for task in tasks]
    duplicated = sorted({tid for tid in ids if ids.count(tid) > 1})
    if duplicated:
        raise SystemExit(f"{len(duplicated)} task id(s) appear more than once in this build: "
                         f"{duplicated[:5]}. A frozen manifest has to name each task exactly once.")
    in_two = sorted({task["task_id"] for task in tasks if task["split"] not in ("train", "test")})
    if in_two:
        raise SystemExit(f"{len(in_two)} task(s) carry a split that is neither train nor test "
                         f"(a task cannot be in two splits): {in_two[:5]}")

    train = [t for t in tasks if t["split"] == "train"]
    test = [t for t in tasks if t["split"] == "test"]

    def asm_hashes(rows):
        """Assembly hashes for the cross-split leak check.

        A row with no assembly contributes nothing rather than a placeholder, and the
        empty-string digest is excluded explicitly: an absent field is not "the same assembly".
        """
        return {t["parent"]["asm_sha256"] for t in rows
                if t["parent"].get("asm") and t["parent"].get("asm_sha256")
                and t["parent"]["asm_sha256"] != sha_text("")}
    train_asm, test_asm = asm_hashes(train), asm_hashes(test)
    overlap = sorted(train_asm & test_asm)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "frozen_at": int(time.time()),
        "compiler_recipe": recipe,
        "verifier": {"exactness": "solver.byte_certificate.certify",
                     "scope": ("allocated text/data/BSS sections and relocation expressions; the "
                               "same link environment. Not a final-link or whole-ROM claim.")},
        "counts": {"train": len(train), "test": len(test), "total": len(tasks)},
        "families": {"train": sorted({t["family"] for t in train}),
                     "test": sorted({t["family"] for t in test})},
        "mutations": sorted({t["mutation"] for t in tasks}),
        "fault_axes": sorted({t["fault_axis"] for t in tasks}),
        "test_families": list(test_families),
        "assembly_overlap_train_test": overlap,
        "grouping": ("split by whole template family; tasks within a family share a template and "
                     "are never separated"),
        "task_ids": {"train": sorted(t["task_id"] for t in train),
                     "test": sorted(t["task_id"] for t in test)},
        # The content binding. Computed from the SAME dicts that `build` then writes, so the
        # digest and the file cannot disagree unless something rewrites one of them.
        "record_hashes": {t["task_id"]: record_hashes(t) for t in tasks},
        "dataset_sha256": sha_bytes(dataset_bytes(tasks)),
        "dataset_lines": len(tasks),
        "leakage": extra or {},
    }
    manifest["manifest_sha256"] = sha_text(json.dumps(manifest, sort_keys=True))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# --- the build ----------------------------------------------------------------

def build(repo: Path, out: Path, *, families: list[str], seeds: list[int],
          target: str = DEFAULT_TARGET, jobs: int = 2, limit: int = 0,
          test_families: tuple[str, ...] = SYNTHETIC_TEST_FAMILIES_DEFAULT,
          freeze: bool = True) -> dict:
    """Generate, damage, compile and compare. Deterministic in (family, seed)."""
    from tools import synthetic_corpus as sc
    from eval import repair_mutations as rm

    resolved_bundle = resolve_recipe(repo, target)
    resolved = resolved_bundle["resolved"]
    provenance = resolved_bundle["provenance"]
    work = Path(tempfile.mkdtemp(prefix="repair-dataset-"))
    started = time.time()
    counts = {"generated": 0, "original_failed": 0, "mutants": 0, "mutant_failed_compile": 0,
              "mutant_identical": 0, "tasks": 0, "mutations_that_never_fired": {}}
    tasks: list[dict] = []
    fired: dict[str, int] = {}

    def one(family: str, seed: int) -> list[dict]:
        name, clean = sc.generate(family, seed)
        target_run = compile_unit(repo, resolved, name, clean, work)
        if not target_run["compiled"]:
            return [{"_skip": "original_failed", "family": family, "seed": seed,
                     "stderr": target_run["stderr"][-300:]}]
        target_obj_path = work / f"{name}.o"
        target_asm, target_n = disassemble(target_obj_path, name)
        target_code = code_image(target_obj_path)
        rows = []
        for mutation, damaged in rm.candidates_for(family, clean):
            mname = f"{name}_{mutation.replace('-', '_')}"
            parent_run = compile_unit(repo, resolved, mname, damaged, work)
            entry = {"mutation": mutation}
            if not parent_run["compiled"]:
                entry["_skip"] = "mutant_failed_compile"
                entry["stderr"] = parent_run["stderr"][-300:]
                rows.append(entry)
                continue
            parent_obj_path = work / f"{mname}.o"
            parent_asm, parent_n = disassemble(parent_obj_path, mname)
            # The answer is compiled AGAIN so `child.exact` rests on a measurement, into its own
            # directory and under the SAME filename.
            answer_dir = work / "answer-check"
            answer_dir.mkdir(parents=True, exist_ok=True)
            child_run = compile_unit(repo, resolved, name, clean, answer_dir)
            answer_obj = answer_dir / f"{name}.o"
            compiled = {
                # `object_path` is what the certificate needs; `code` (.text bytes) is kept for
                # the DIFF diagnostic only. The verdict no longer reads `code`.
                "target": {"object": target_run["object"], "code": target_code,
                           "object_path": target_obj_path,
                           "asm": target_asm, "instructions": target_n, "compiled": True,
                           "stderr": ""},
                "parent": {"object": parent_run["object"],
                           "code": code_image(parent_obj_path),
                           "object_path": parent_obj_path, "asm": parent_asm,
                           "instructions": parent_n, "compiled": True,
                           "stderr": parent_run["stderr"]},
                "child": {"object": child_run["object"] if child_run["compiled"] else None,
                          "code": code_image(answer_obj) if child_run["compiled"] else None,
                          "object_path": answer_obj if child_run["compiled"] else None,
                          "compiled": child_run["compiled"], "stderr": child_run["stderr"]},
            }
            task = build_task(family=family, seed=seed, mutation=mutation,
                              candidate_source=damaged, clean_source=clean,
                              function=name, compiled=compiled)
            if task is None:
                entry["_skip"] = "mutant_identical"
                rows.append(entry)
                continue
            task["parent"]["asm_exact"] = object_diff(target_run["object"],
                                                      parent_run["object"])["identical"]
            task["provenance"]["compiler_recipe"] = provenance
            task["provenance"]["clean_source_line_count"] = len(clean.splitlines())
            rows.append(task)
        return rows

    tasks_by_pair = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [(f, s) for f in families for s in seeds]
        for rows in pool.map(lambda pair: one(*pair), futures):
            for row in rows:
                if "_skip" in row:
                    counts[row["_skip"]] = counts.get(row["_skip"], 0) + 1
                    continue
                counts["mutants"] += 1
                fired[row["mutation"]] = fired.get(row["mutation"], 0) + 1
                tasks_by_pair.append(row)
    shutil.rmtree(work, ignore_errors=True)

    counts["generated"] = len(futures)
    counts["tasks"] = len(tasks_by_pair)
    counts["mutations_that_never_fired"] = {
        name: 0 for name in rm.MUTATIONS if name not in fired}
    tasks_by_pair = assign_splits(tasks_by_pair, test_families=test_families)
    if limit:
        tasks_by_pair = tasks_by_pair[:limit]

    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "tasks.jsonl"
    leak_failures = [t["task_id"] for t in tasks_by_pair
                     if not t["provenance"]["leakage"]["clean"]]

    # The manifest is written BEFORE the JSONL so it can carry the JSONL's digest. Writing the
    # rows first and the digest second is how a manifest ends up attesting to a file it was not
    # built from. The file is written from `dataset_bytes`, the same function whose output the
    # manifest digests, so the recorded `dataset_sha256` is a fact about this file and not a
    # convention about how it ought to be serialised.
    manifest = None
    tasks_sha = sha_text("\n".join(json.dumps(t, sort_keys=True) for t in tasks_by_pair))
    if freeze:
        manifest = freeze_manifest(
            tasks_by_pair, manifest_path=out / "FROZEN_SPLIT.json", recipe=provenance,
            test_families=test_families,
            extra={"leakage_failures": leak_failures, "tasks_sha256": tasks_sha})
    jsonl.write_bytes(dataset_bytes(tasks_by_pair))

    summary = {
        "schema_version": SCHEMA_VERSION,
        "built_at": int(time.time()),
        "seconds": round(time.time() - started, 1),
        "compiler_recipe": provenance,
        "families": sorted(families), "seeds": sorted(seeds),
        "counts": counts,
        "mutations_fired": fired,
        "leakage_failures": leak_failures,
        "tasks_sha256": tasks_sha,
        "dataset_sha256": sha_bytes(dataset_bytes(tasks_by_pair)),
        "split_counts": {
            "train": sum(1 for t in tasks_by_pair if t["split"] == "train"),
            "test": sum(1 for t in tasks_by_pair if t["split"] == "test")},
    }
    (out / "build_receipt.json").write_text(json.dumps(summary, indent=2) + "\n",
                                            encoding="utf-8")
    return {"summary": summary, "manifest": manifest}


def load_tasks(path: Path, *, split: str | None = None) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            task = json.loads(line)
            if split is None or task.get("split") == split:
                rows.append(task)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--families", nargs="*", default=None)
    ap.add_argument("--seeds", type=int, default=6, help="seeds per family: 0..N-1")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--test-families", nargs="*", default=list(SYNTHETIC_TEST_FAMILIES_DEFAULT))
    ap.add_argument("--no-freeze", action="store_true")
    args = ap.parse_args(argv)

    from tools.synthetic_corpus import FAMILIES
    families = args.families or sorted(FAMILIES)
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        raise SystemExit(f"unknown families: {unknown}")
    missing = [f for f in args.test_families if f not in families]
    if missing:
        raise SystemExit(f"test families not in the built set: {missing}")
    result = build(args.repo, args.out, families=families,
                   seeds=list(range(args.seeds)), target=args.target, jobs=args.jobs,
                   limit=args.limit, test_families=tuple(args.test_families),
                   freeze=not args.no_freeze)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
