"""Export an auditable source-repair dataset from the attempt knowledge base.

One record per **verified repair**: a candidate C that compiled and scored worse
(the state being repaired), a real compiler outcome for it, and a C that
compiled and scored higher (the verified improvement). One JSONL record per
pair, plus a manifest carrying the split digest and the full filter audit.

PROVENANCE RULES (the single most important field on a record)
-------------------------------------------------------------

``observed-repair``
    A real ``attempt_edges`` row parent->child exists, both endpoints have
    ``compiled=1``, and ``child.score > parent.score`` (``EDGE_IMPROVEMENT_EPSILON``;
    reconstructed pairs keep the 0.5 margin). The lineage is
    recorded, not inferred. Whether a *model* wrote the child is reported
    separately in ``meta.generator`` / ``meta.mechanism_confidence`` -- an edge
    with no model records is still observed lineage, and is only called
    ``deterministic-repair`` when the mechanism is on the model-free registry
    below (``unverified`` otherwise, never guessed).

``deterministic-repair``
    Same observed edge, but the child was produced by the project's own
    model-free repair machinery. The mechanism is labelled with the exact
    ``attempts.strategy`` string, and the registry entry that justifies calling
    it model-free names the live module it comes from. A strategy that is
    merely *suspected* to be deterministic is not promoted here.

``reconstructed-lineage``
    NO edge row. A real (candidate, feedback) state and a real, better compiled
    candidate for the SAME function are paired by a deterministic rule, and the
    parent->child *relationship* is inferred. Both rows and both scores are
    genuine; the pair is not. These records carry
    ``meta.lineage="reconstructed"`` and ``meta.reconstruction_basis``, and must
    never be described as the original model interaction.

EXCLUSIONS (all counted in the audit, each one is a named filter that a test
proves fires on its motivating case)
------------------------------------

``parent-or-child-not-compiled`` / ``parent-child-different-function`` /
``empty-source-code`` / ``not-improving-edge`` / ``function-has-exact-attempt`` (the
function is finished; OFF by default since 2026-09-27, ``--exclude-finished``) / ``sealed-dev-heldout`` / ``sealed-cluster-panel`` (the
``cluster`` and ``panel`` keys of ``eval/sets/*.json``; ``eval.trajectory_factory.
sealed_functions`` read only ``dev`` and ``heldout`` when this task started, and were
fixed by another agent mid-session -- this module never imports it, scans the files
itself, and is tested on all four keys regardless) / ``sealed-unrecognized-shape``
(names carried by an eval-set file in a shape the four sealed keys do not reach,
e.g. ``completion-campaign-dev-seeds-v1.json``) / ``library-tu``
(``eval.clean_set.EXCLUDE_TU``) / ``duplicate-pair`` (same
``(parent_sha256, child_sha256)``) / ``reconstructed-per-function`` (a documented
export budget, reported with the supply it truncates, never as an exclusion).

FIELD ADDITIONS beyond the requested schema (all optional, all named):
``input.target_asm_available``, ``input.target_asm_source``, ``input.flags``,
``input.recipe``, ``input.declarations_note``, ``meta.parent_sha256_source``,
``meta.child_sha256_source``, ``meta.score_delta``, ``meta.reconstruction_basis``,
``meta.lineage_note`` (present only on reconstructed records),
``meta.feedback_available``, ``meta.generator.mechanism_family``,
``meta.generator.mechanism_evidence``, ``meta.mechanism_confidence``.

Read-only: the KB is opened with ``mode=ro``. Nothing in this module writes to
any database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
GAME = "sbk1"
COMPILER = "ido-5.3"
SEALED_KEYS = ("dev", "heldout", "cluster", "panel")
# The extra two keys are the latent gap in eval/trajectory_factory.sealed_functions,
# which reads only "dev" and "heldout". They are kept separate so the audit can
# say exactly how many records that gap would have leaked.
EXTRA_SEALED_KEYS = ("cluster", "panel")
SPLIT_NAMES = ("train", "dev", "test")
DEFAULT_SEED = 20260920
# Margin for RECONSTRUCTED pairs only: there it guards something real, pairing two unrelated
# attempts whose scores are merely close.
IMPROVEMENT_EPSILON = 0.5
# Observed edges need no margin. Parent and child are scored by the same scorer in the same
# session (campaign.sqlite, 2026-09-27: 14,145 of 14,147 improving compiled edges were created
# under an hour apart), so any strict increase is a verified improvement. The old 0.5 cut removed
# 7,784 of those 14,147 edges, 91 of them with an EXACT child: one fixed instruction in a
# 200-instruction function moves the score by 0.5.
EDGE_IMPROVEMENT_EPSILON = 0.0
# Filters that exist but are off unless named in ``enabled_filters``. A finished function's edges
# are the best-labelled supply there is (the route reached a match), so excluding them is only
# right when the export is used as a pool of states still to repair.
DEFAULT_OFF_FILTERS = frozenset({"function-has-exact-attempt"})
# Strategy markers of a candidate that IS the reference decomp's answer (eval/status.py's recovered
# tier, plus the campaign's historical seed, which status.py documents as reference-derived).
REFERENCE_SEED_MARKERS = ("history-recovery", "historical-provenance", "symbol-restoration",
                          "explicit-historical-seed")
# Permille cut points over sha256(seed:tu) % 10000. Deterministic and recorded.
SPLIT_TEST_CUT = 1500
SPLIT_DEV_CUT = 3000
DEFAULT_RECONSTRUCTED_PER_FUNCTION = 20
DEFAULT_WORKSPACE_ROOT = Path("/home/grant/decomp/sbk1/nonmatchings")


# --- provenance policy --------------------------------------------------------

@dataclass(frozen=True)
class Mechanism:
    """One strategy string that is known, from live source, to be model-free."""
    strategy: str
    family: str
    evidence: str

    @property
    def prefix(self) -> bool:
        return self.strategy.endswith(":*")


# Every entry was checked by reading the module named in `evidence`. A strategy
# absent from this registry is NEVER called deterministic.
DETERMINISTIC_MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism("typed-semantic-gradient-beam", "typed-semantic-gradient-beam",
              "eval/semantic_gradient_beam.py: module docstring 'Zero-LLM beam search over "
              "typed dynamic semantic gradients'; records model_calls=0 and model=''"),
    Mechanism("typed-semantic-gradient", "typed-semantic-gradient-beam",
              "eval/semantic_gradient_pilot.py / eval/semantic_gradient_beam.py: the same "
              "zero-LLM variant generator"),
    Mechanism("alloc-order", "alloc-order",
              "eval/allocsearch.py: deterministic allocation-order search over solver.rewrites; "
              "imports no model transport"),
    Mechanism("differential-deterministic-statement-order", "statement-order",
              "eval/differential_repair_pilot.py: the deterministic statement-order arm"),
    Mechanism("differential-deterministic-semantic-principle", "semantic-principle",
              "eval/differential_repair_pilot.py: the deterministic semantic-principle arm"),
    Mechanism("m2c-semantic-seed", "m2c-semantic-seed",
              "eval/m2c_semantic_seed.py: m2c draft seeding; m2c is a decompiler, not a model"),
    Mechanism("do-restore:*", "do-restore",
              "eval/do_restore_search.py: deterministic do-while restoration search"),
    *(Mechanism(f"repair-d{depth}", "rewrite-beam",
                "solver/repair.py: bounded rewrite beam over solver.rewrites, "
                "strategy=f'repair-d{depth}' with max_depth=6; imports no model transport")
      for depth in range(1, 7)),
    Mechanism("repair-plateau-v1", "rewrite-beam",
              "solver/repair.py: plateau search over the same rewrites"),
    Mechanism("regalloc-search-probe", "regalloc-search",
              "eval/agentrepair.py: register-allocation mutation probes, model='zero-model'"),
    Mechanism("modelrepair-normalize:*", "compiler-normalization",
              "solver/modelrepair.py: deterministic normalization variants of the parent "
              "source, model='zero-model', relation='compiler-normalization'"),
    Mechanism("operand-repair:*", "operand-repair",
              "eval/operand_repair.py: imports diffrepair, regalloc_mutations, residual, "
              "rodata_symbol and workspace only"),
    *(Mechanism(strategy, strategy.removeprefix("agentrepair-").rstrip(":*"),
                f"eval/agentrepair.py: strategy {strategy!r} written with model='zero-model'")
      for strategy in ("agentrepair-regalloc-search", "agentrepair-stack-layout",
                       "agentrepair-structural-rewrites", "agentrepair-address-symbols",
                       "agentrepair-frontend-fixits", "agentrepair-compile-recovery:*",
                       "agentrepair-compile-chain:*", "agentrepair-type-constraints")),
)

# Values of attempts.model that carry no model identity. Anything else is a model.
MODEL_FREE_MODEL_IDS = frozenset({"", "zero-model", "deterministic", "m2c", "none",
                                  "no-model", "null"})

# Strategies whose child content is known to come from a model even though the
# attempt row carries no model records. Labelled observed-repair (the lineage is
# real) with an explicit note, never deterministic.
MODEL_DERIVED_MECHANISMS = {
    "automatic-proposal-recovery":
        "replays a stored model proposal (eval/differential_repair_pilot.py, "
        "strategy='automatic-proposal-recovery'); the replayed content is model-authored",
    "factory-refine":
        "eval/trajectory_factory.py drives a model generator (OllamaGenerator) and records "
        "the parent receipt; the attempt row kept neither prompt nor raw response",
    "logic-reference-leave_one_tu_out":
        "eval agentrepair logic lane; model proposals exist for its siblings",
}

FILTER_ORDER = (
    "parent-or-child-not-compiled",
    "parent-child-different-function",
    "not-improving-edge",
    "empty-source-code",
    "function-has-exact-attempt",
    "sealed-dev-heldout",
    "sealed-cluster-panel",
    "sealed-unrecognized-shape",
    "library-tu",
    "missing-parent-feedback",
    "duplicate-pair",
)

FILTER_REASONS = {
    "parent-or-child-not-compiled":
        "a parent or child that did not compile has no real outcome to learn from",
    "parent-child-different-function":
        "the parent C is a different function, so it is not the state being repaired",
    "not-improving-edge":
        "both endpoints compiled but child.score is not > parent.score (plus the edge epsilon "
        "in the policy), so there is no verified improvement to learn",
    "empty-source-code": "nothing to repair or nothing to learn",
    "function-has-exact-attempt":
        "OFF BY DEFAULT (enable with --exclude-finished): the function is finished, so its "
        "states are not supply for a pool of functions still to repair; as training data its "
        "edges are the best-labelled there are",
    "sealed-dev-heldout":
        "function name is in a sealed eval set under 'dev' or 'heldout'",
    "sealed-cluster-panel":
        "function name is in a sealed eval set under 'cluster' or 'panel' -- the keys "
        "eval.trajectory_factory.sealed_functions does not read",
    "sealed-unrecognized-shape":
        "function name is carried by an eval/sets/*.json file in a shape the four sealed "
        "keys do not reach (reported separately, never silently dropped)",
    "library-tu": "translation unit matches eval.clean_set.EXCLUDE_TU (published/library code)",
    "missing-parent-feedback":
        "reconstructed pair whose parent has no stored compiler diff: a candidate without "
        "feedback is not a repair state",
    "duplicate-pair":
        "another record already carries the same (parent_sha256, child_sha256)",
}

RECONSTRUCTED_BASIS = "best-strictly-worse-compiled-predecessor-same-function"

DECLARATIONS_NOTE = (
    "null: the knowledge base stores no declaration context per attempt. attempts.prompt_context "
    "exists only for model-generated children (and there it is an agent/JSON-action prompt, not a "
    "declarations block), the workspace <func>.frontend.json carries a clang syntax-check recipe "
    "rather than a declarations list, and model_proposals.kind='declarations' rows are proposed "
    "edits, not the permitted declaration set. Any declaration list here would be reconstructed "
    "rather than recorded, so it is left null."
)


# --- sealed sets --------------------------------------------------------------

def _iter_function_names(value: object) -> Iterable[str]:
    """Every function-like name under a sealed key, whatever the row shape.

    Only ``function`` keys and bare strings are names. Recursing blindly into every
    string value would also harvest residual diff text (``'+lw v1,0(a0)'``, ``''``)
    from nested blocks such as ``fresh_residual``: harmless-looking, but a sealed
    set that grows by accident can silently drop real supply.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        name = value.get("function")
        if isinstance(name, str):
            yield name
            return
        for child in value.values():
            yield from _iter_function_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_function_names(child)


def sealed_members(sets_dir: Path) -> dict:
    """Function names per sealed key across ``eval/sets/*.json``.

    All four keys are read. ``eval/trajectory_factory.sealed_functions`` reads only
    ``dev`` and ``heldout`` (latent bug, reported not fixed here), so callers that
    need the real sealed set must use this function.
    """
    members: dict[str, set[str]] = {key: set() for key in SEALED_KEYS}
    files: list[str] = []
    directory = Path(sets_dir)
    if not directory.is_dir():
        return {"members": members, "files": files, "unrecognized": {}, "per_file": {}}
    per_file: dict[str, dict[str, int]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        files.append(path.name)
        counts: dict[str, int] = {}
        for key in SEALED_KEYS:
            found = set()
            if key == "dev" and payload.get("kind") == "sealed-near-miss-split":
                continue            # its dev side is for generator development, not sealed
            for row in payload.get(key, []) or []:
                found.update(_iter_function_names(row))
            members[key] |= found
            counts[key] = len(found)
        per_file[path.name] = counts
    unrecognized = unrecognized_shape_members(directory, members)
    return {"members": members, "files": files, "unrecognized": unrecognized,
            "per_file": per_file}


def unrecognized_shape_members(directory: Path,
                               members: dict[str, set[str]]) -> dict[str, list[str]]:
    """Function names an eval-set file exposes in a shape the four keys do not read.

    ``eval/sets/completion-campaign-dev-seeds-v1.json`` is ``{"<function>": <attempt_id>}``
    at the top level, so neither this module nor ``trajectory_factory`` seals it by
    reading ``dev``/``heldout``/``cluster``/``panel``. Contamination is the primary
    risk in this project, so these names are excluded too -- but separately
    counted, so the number is auditable rather than buried in the total.
    """
    known = set().union(*members.values()) if members else set()
    out: dict[str, list[str]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        found = {key for key, value in payload.items()
                 if isinstance(key, str) and re.fullmatch(r"[A-Za-z_]\w*", key)
                 and isinstance(value, (int, float)) and not isinstance(value, bool)
                 and key not in SEALED_KEYS}
        extra = sorted(found - known)
        if extra:
            out[path.name] = extra
    return out


def sealed_functions(sets_dir: Path | None = None) -> set[str]:
    """The sealed set this exporter uses: ``dev`` + ``heldout`` + ``cluster`` + ``panel``."""
    directory = Path(sets_dir) if sets_dir else ROOT / "eval" / "sets"
    members = sealed_members(directory)["members"]
    return set().union(*members.values()) if members else set()


# --- library translation units ------------------------------------------------

def exclude_tu_patterns() -> tuple[tuple[str, ...], str]:
    """``eval.clean_set.EXCLUDE_TU``, reused rather than re-derived."""
    try:
        from eval.clean_set import EXCLUDE_TU
        return tuple(EXCLUDE_TU), "eval.clean_set.EXCLUDE_TU"
    except Exception:                                    # pragma: no cover - import guard
        return ("%ultra%", "%libmus%", "%libc%", "%audio%"), "literal fallback"


def like_in(pattern: str, value: str) -> bool:
    """SQL ``LIKE '%...%'`` semantics for the shapes ``EXCLUDE_TU`` actually uses."""
    return pattern.strip("%").lower() in value.lower()


def is_library_tu(tu: str | None, patterns: Sequence[str]) -> bool:
    return bool(tu) and any(like_in(pattern, tu) for pattern in patterns)


# --- mechanism classification -------------------------------------------------

def mechanism_for(strategy: str, registry: Sequence[Mechanism] = DETERMINISTIC_MECHANISMS
                  ) -> Mechanism | None:
    for entry in registry:
        if entry.prefix and strategy.startswith(entry.strategy[:-1]):
            return entry
        if not entry.prefix and strategy == entry.strategy:
            return entry
    return None


def classify_generator(attempt_id: int, strategy: str, model: str, prompt_len: int,
                       raw_len: int, proposal_ids: frozenset[int],
                       registry: Sequence[Mechanism] = DETERMINISTIC_MECHANISMS) -> dict:
    """What produced the child, and how strongly that is known.

    ``family`` is ``model`` (model records or a model identity exist), ``deterministic``
    (the strategy is on the model-free registry), or ``unverified`` (no model records
    and no registry entry -- reported, never guessed).
    """
    evidence: list[str] = []
    if raw_len:
        evidence.append("attempts.raw_response")
    if prompt_len:
        evidence.append("attempts.prompt_context")
    if model and model.lower() not in MODEL_FREE_MODEL_IDS:
        evidence.append(f"attempts.model={model}")
    if attempt_id in proposal_ids:
        evidence.append("model_proposals.child_attempt_id")
    entry = mechanism_for(strategy, registry)
    note = MODEL_DERIVED_MECHANISMS.get(strategy, "")
    if evidence:
        return {"family": "model", "strategy": strategy, "model": model,
                "mechanism_family": entry.family if entry else None,
                "mechanism_evidence": evidence, "confidence": "model-records", "note": note}
    if entry is not None:
        return {"family": "deterministic", "strategy": strategy, "model": model,
                "mechanism_family": entry.family, "mechanism_evidence": [entry.evidence],
                "confidence": "deterministic-registry", "note": note}
    return {"family": "unverified", "strategy": strategy, "model": model,
            "mechanism_family": None, "mechanism_evidence": [], "confidence": "unverified",
            "note": note or ("no model records on the attempt row and the strategy is not on the "
                             "model-free registry; the edge is real but the mechanism is unknown")}


def provenance_of(has_edge: bool, generator_family: str) -> tuple[str, str]:
    """``(provenance, lineage)``. Determinism is decided before edge presence, and
    ``meta.lineage`` records edge presence separately so both facts survive."""
    if not has_edge:
        return "reconstructed-lineage", "reconstructed"
    if generator_family == "deterministic":
        return "deterministic-repair", "observed"
    return "observed-repair", "observed"


# --- compilation artefacts on disk --------------------------------------------

def target_body(text: str, func: str) -> str:
    """The function body between ``glabel <func>`` and ``endlabel <func>``.

    Mirrors ``solver.workspace.target_asm`` deliberately (that module is imported
    for its whole call graph and this exporter must stay side-effect free); the
    equivalence is asserted by ``tests/test_repair_dataset.py``.
    """
    start = text.find(f"glabel {func}")
    if start == -1:
        raise RuntimeError(f"glabel {func} not found in target.s")
    end = text.find(f"endlabel {func}")
    return text[start:end if end != -1 else None].strip()


def read_target_asm(workspace_root: Path, func: str) -> tuple[str | None, str, str]:
    """``(assembly, source_path, note)``. Never substitutes or fabricates assembly."""
    path = Path(workspace_root) / func / "target.s"
    if not path.is_file():
        return None, str(path), "no target.s for this function in the workspace"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, str(path), f"target.s unreadable: {exc}"
    try:
        body = target_body(text, func)
    except RuntimeError as exc:
        return None, str(path), str(exc)
    if not body:
        return None, str(path), "target.s carries an empty body for this label"
    return body, str(path), ""


def read_recipe(workspace_root: Path, func: str) -> dict | None:
    """The compiler recipe the project actually used, read from the workspace.

    ``tus.compiler``/``tus.flags`` are empty in this KB (verified: 0 of 469 rows), so
    the recipe comes from ``nonmatchings/<func>/.compiler-<hash>.json``, whose
    ``settings`` block names the IDO driver and the C flags.
    """
    directory = Path(workspace_root) / func
    if not directory.is_dir():
        return None
    target_name = None
    mapping = directory / ".compiler-target.json"
    candidates = sorted(directory.glob(".compiler-*.json"))
    if not candidates:
        return None
    if mapping.is_file():
        try:
            target_name = json.loads(mapping.read_text(encoding="utf-8")).get("target")
        except (OSError, UnicodeError, json.JSONDecodeError):
            target_name = None
    chosen, payload = None, None
    for path in candidates:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if chosen is None:
            chosen, payload = path, loaded
        if target_name and loaded.get("target") == target_name:
            chosen, payload = path, loaded
            break
    if payload is None:
        return None
    settings = payload.get("settings", {}) if isinstance(payload, dict) else {}
    flags = " ".join(str(settings.get(key, "")) for key in ("CFLAGS", "C_OPT", "C_MIPS")).strip()
    return {
        "target_object": payload.get("target"),
        "ido_cc": settings.get("IDO_CC"),
        "cflags": settings.get("CFLAGS"),
        "c_opt": settings.get("C_OPT"),
        "c_mips": settings.get("C_MIPS"),
        "asflags": settings.get("ASFLAGS"),
        "flags": flags or None,
        "source": str(chosen),
    }


# --- KB access ----------------------------------------------------------------

ATTEMPT_COLUMNS = ("id, func_addr, iteration, source_code, prompt_context, compiled, "
                   "compiler_stderr, score, diff_summary, strategy, model, sampling, "
                   "created_at, raw_response, exact, run_id, parent_attempt_id, source_sha256")


def connect_readonly(kb: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(kb)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sources(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    unique = sorted(set(ids))
    for start in range(0, len(unique), 400):
        chunk = unique[start:start + 400]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"select {ATTEMPT_COLUMNS} from attempts where id in ({marks})", chunk):
            out[row["id"]] = dict(row)
    return out


def _edge_rows(conn: sqlite3.Connection) -> list[dict]:
    sql = """
    select e.parent_attempt_id as parent_id, e.child_attempt_id as child_id,
           e.relation, e.action, e.feedback,
           p.func_addr as parent_addr, c.func_addr as child_addr,
           coalesce(p.compiled,0) as parent_compiled, coalesce(c.compiled,0) as child_compiled,
           p.score as parent_score, c.score as child_score,
           coalesce(p.exact,0) as parent_exact, coalesce(c.exact,0) as child_exact,
           p.source_sha256 as parent_sha, c.source_sha256 as child_sha,
           p.created_at as parent_created, c.created_at as child_created,
           c.strategy as strategy, c.model as model, c.sampling as sampling,
           c.run_id as run_id, c.iteration as iteration,
           length(coalesce(p.prompt_context,'')) as parent_prompt_len,
           length(coalesce(c.prompt_context,'')) as prompt_len,
           length(coalesce(c.raw_response,'')) as raw_len,
           length(coalesce(p.diff_summary,'')) as parent_diff_len,
           length(coalesce(c.diff_summary,'')) as child_diff_len,
           length(coalesce(p.source_code,'')) as parent_source_len,
           length(coalesce(c.source_code,'')) as child_source_len
    from attempt_edges e
    join attempts p on p.id = e.parent_attempt_id
    join attempts c on c.id = e.child_attempt_id
    order by e.child_attempt_id, e.parent_attempt_id
    """
    return [dict(row) for row in conn.execute(sql)]


def _child_ids_with_edges(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("select distinct child_attempt_id from attempt_edges")}


def _proposal_child_ids(conn: sqlite3.Connection) -> frozenset[int]:
    return frozenset(row[0] for row in conn.execute(
        "select distinct child_attempt_id from model_proposals "
        "where child_attempt_id is not null"))


def _function_index(conn: sqlite3.Connection) -> dict[int, dict]:
    """addr -> function/TU metadata. The whole table is ~2k rows: load it once."""
    return {row["addr"]: dict(row) for row in conn.execute(
        """select f.addr, f.name, f.tu_id, f.insn_count, f.is_leaf, t.name as tu
           from functions f left join tus t on t.id = f.tu_id""")}


def _exact_functions(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute(
        "select distinct func_addr from attempts where coalesce(exact,0)=1")}


def _reconstructed_pairs(conn: sqlite3.Connection, *, epsilon: float) -> tuple[list[dict], dict]:
    """Deterministically pair each lineage-free compiled child with a real predecessor.

    Rule: the parent is the compiled attempt of the SAME function with the highest
    score that is still worse than the child by more than ``epsilon``, was created
    no later than the child, and is a different source. One parent per child, so the
    pairing is a function of the KB, not of iteration order.

    Whether that parent carries a stored compiler diff is deliberately NOT part of
    the rule: it is the ``missing-parent-feedback`` filter's job, so the count of
    pairs dropped for lack of feedback is visible in the audit instead of silently
    shrinking the candidate pool.
    """
    children = [dict(row) for row in conn.execute("""
        select c.id as child_id, c.func_addr as child_addr, c.score as child_score,
               c.created_at as child_created, c.source_sha256 as child_sha,
               c.strategy as strategy, c.model as model, c.sampling as sampling,
               c.run_id as run_id, c.iteration as iteration,
               length(coalesce(c.prompt_context,'')) as prompt_len,
               length(coalesce(c.raw_response,'')) as raw_len,
               length(coalesce(c.diff_summary,'')) as child_diff_len,
               length(coalesce(c.source_code,'')) as child_source_len
        from attempts c
        where coalesce(c.compiled,0)=1 and c.score is not null
          and c.parent_attempt_id is null
          and not exists (select 1 from attempt_edges e where e.child_attempt_id = c.id)
        order by c.func_addr, c.id""")]
    parents: dict[int, list[dict]] = {}
    for row in conn.execute("""
            select p.id as parent_id, p.func_addr as parent_addr, p.score as parent_score,
                   p.created_at as parent_created, p.source_sha256 as parent_sha,
                   length(coalesce(p.diff_summary,'')) as parent_diff_len,
                   length(coalesce(p.source_code,'')) as parent_source_len
            from attempts p
            where coalesce(p.compiled,0)=1 and p.score is not null
            order by p.func_addr, p.score, p.created_at, p.id"""):
        parents.setdefault(row["parent_addr"], []).append(dict(row))

    pairs: list[dict] = []
    no_parent = 0
    for child in children:
        pool = parents.get(child["child_addr"], [])
        chosen = None
        for candidate in pool:
            if candidate["parent_score"] is None:
                continue
            if candidate["parent_score"] > child["child_score"] - epsilon:
                continue                                    # pool is score-ascending
            if candidate["parent_created"] > child["child_created"]:
                continue
            if candidate["parent_sha"] and candidate["parent_sha"] == child["child_sha"]:
                continue
            if candidate["parent_id"] == child["child_id"]:
                continue
            chosen = candidate                        # keep the highest such score
        if chosen is None:
            no_parent += 1
            continue
        pair = dict(child)
        pair.update({key: chosen[key] for key in
                     ("parent_id", "parent_score", "parent_created", "parent_sha",
                      "parent_diff_len", "parent_source_len")})
        pair.update({"parent_compiled": 1, "child_compiled": 1,
                     "parent_exact": 0, "child_exact": 0,
                     "parent_prompt_len": 0, "relation": None, "action": None,
                     "feedback": None, "has_edge": False,
                     "parent_addr": child["child_addr"]})
        pairs.append(pair)
    return pairs, {"children_considered": len(children), "children_without_parent": no_parent,
                   "children_with_feedback_parent": sum(
                       1 for pair in pairs if pair["parent_diff_len"]),
                   "pairs": len(pairs)}


# --- record assembly ----------------------------------------------------------

@dataclass
class ExportContext:
    conn: sqlite3.Connection
    game: str
    seed: int
    workspace_root: Path
    sealed: dict
    sealed_union: set[str]
    sealed_extra: set[str]
    sealed_unrecognized: dict[str, list[str]]
    library_patterns: tuple[str, ...]
    library_source: str
    proposal_ids: frozenset[int]
    exact_functions: set[int]
    functions: dict[int, dict]
    reconstructed_per_function: int
    sealed_files: list[str] = field(default_factory=list)
    sealed_unrecognized_raw: dict[str, list[str]] = field(default_factory=dict)
    # None when no build tree was given: the tag then says "unchecked", never "clean".
    reference_types: frozenset[str] | None = None
    game_identifiers: dict[str, frozenset[str]] | None = None
    strategies: dict[int, str] = field(default_factory=dict)
    parents: dict[int, list[int]] = field(default_factory=dict)
    _seed_lineage: dict[int, bool] = field(default_factory=dict)
    _target_cache: dict[str, tuple] = field(default_factory=dict)
    _recipe_cache: dict[str, dict | None] = field(default_factory=dict)


def _name_of(ctx: ExportContext, addr: int) -> str:
    info = ctx.functions.get(addr)
    return info["name"] if info else f"func_{addr:08X}"


def _tu_of(ctx: ExportContext, addr: int) -> str | None:
    info = ctx.functions.get(addr)
    return info["tu"] if info else None


def _artifacts(ctx: ExportContext, func: str) -> tuple[str | None, str, str, dict | None]:
    if func not in ctx._target_cache:
        ctx._target_cache[func] = read_target_asm(ctx.workspace_root, func)
    asm, path, note = ctx._target_cache[func]
    if func not in ctx._recipe_cache:
        ctx._recipe_cache[func] = read_recipe(ctx.workspace_root, func)
    return asm, path, note, ctx._recipe_cache[func]


_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PREPROCESSOR = re.compile(r"^[ \t]*#[^\n]*", re.M)
_CAPITALISED = re.compile(r"\b[A-Z]\w*\b")
_IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")


def reference_types_used(source: str, reference_types: frozenset[str]) -> list[str]:
    """Reference-only type names a source uses, by eval/status.py's reference-type-assisted rule
    (capitalised identifiers outside comments, intersected with the types only src/*.c defines)."""
    return sorted(set(_CAPITALISED.findall(_COMMENTS.sub(" ", source or ""))) & reference_types)


def game_header_identifiers(build_tree: Path) -> dict[str, frozenset[str]]:
    """Types and prototypes/externs that ONLY the reconstructed ``include/game/**`` headers declare.

    Measured by use, not by ``#include``: every draft includes ``common.h``, which itself pulls in
    ``game/math/geometry.h``, so on 2026-09-27 313 of 327 records with no ``game/`` include still
    used a game-only identifier. Declarations come from the same scanners the pipeline uses
    (``solver.project_headers`` for prototypes/externs, ``eval.status`` for type definitions).
    """
    from eval.status import _TYPE_DEFS
    from solver.project_headers import _declared_name, _top_level_declarations
    include = Path(build_tree) / "include"
    found = {True: {"types": set(), "prototypes": set()},
             False: {"types": set(), "prototypes": set()}}
    for path in include.rglob("*.h"):
        text = path.read_text(errors="replace")
        bucket = found[path.relative_to(include).parts[0] == "game"]
        for pattern in _TYPE_DEFS:
            bucket["types"].update(pattern.findall(_COMMENTS.sub(" ", text)))
        for _, _, declaration in _top_level_declarations(text):
            name = _declared_name(declaration)
            if name:
                bucket["prototypes"].add(name)
    elsewhere = found[False]["types"] | found[False]["prototypes"]
    return {kind: frozenset(found[True][kind] - elsewhere) for kind in ("types", "prototypes")}


def identifiers_used(source: str, names: frozenset[str]) -> list[str]:
    """Names from ``names`` used in code: comments and preprocessor lines are not use."""
    body = _PREPROCESSOR.sub(" ", _COMMENTS.sub(" ", source or ""))
    return sorted(set(_IDENTIFIER.findall(body)) & names)


def has_reference_seed_ancestor(ctx: ExportContext, attempt_id: int) -> bool:
    """Whether this attempt or any recorded ancestor IS the reference decomp's answer.

    Edges make a DAG (an attempt can have several parents), so every ancestor is visited once.
    """
    if attempt_id in ctx._seed_lineage:
        return ctx._seed_lineage[attempt_id]
    stack, seen, found = [attempt_id], set(), False
    while stack and not found:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        if ctx._seed_lineage.get(node) or any(
                marker in ctx.strategies.get(node, "") for marker in REFERENCE_SEED_MARKERS):
            found = True
            break
        stack.extend(ctx.parents.get(node, ()))
    ctx._seed_lineage[attempt_id] = found
    return found


def assistance_tags(ctx: ExportContext, parent_id: int, parent_source: str,
                    child_source: str, function: str = "", *, child_id: int) -> dict:
    """What a record's text or lineage owes to the reference decomp. Tags, not filters: the
    training data is not held out, so contamination here matters for what a model learns to
    emit, and a capability run must be able to select the clean subset."""
    checked = ctx.reference_types is not None
    types = ctx.reference_types or frozenset()
    game = ctx.game_identifiers or {}

    def used(source: str, kind: str) -> list[str] | None:
        # A function's own prototype lives in a game/ header, but defining yourself is not
        # assistance: the name comes from the symbol map. Callee prototypes stay counted -- their
        # names come from the symbol map too, but their parameter/return types come from the
        # header whenever it is in effect -- so this tag is an upper bound on dependence.
        return identifiers_used(source, game[kind] - {function}) if checked else None

    return {
        "reference_types_checked": checked,
        "parent_reference_types": reference_types_used(parent_source, types) if checked else None,
        "child_reference_types": reference_types_used(child_source, types) if checked else None,
        "parent_game_header_types": used(parent_source, "types"),
        "child_game_header_types": used(child_source, "types"),
        "parent_game_header_prototypes": used(parent_source, "prototypes"),
        "child_game_header_prototypes": used(child_source, "prototypes"),
        "parent_reference_seed_lineage": has_reference_seed_ancestor(ctx, parent_id),
        "child_reference_seed_lineage": has_reference_seed_ancestor(ctx, child_id),
    }


# Both endpoints matter: a repair can remove an assisted call, and the child can
# introduce reference lineage that was absent from the parent.
ASSISTANCE_KEYS = ("parent_reference_types", "child_reference_types", "parent_game_header_types",
                   "child_game_header_types", "parent_game_header_prototypes",
                   "child_game_header_prototypes", "parent_reference_seed_lineage",
                   "child_reference_seed_lineage")


def is_clean(tags: dict) -> bool | None:
    """Nothing in either endpoint's text that only the reference decomp's src/ or its
    reconstructed game/ headers declare, and no reference seed in the lineage. ``None`` when the
    build tree was not checked."""
    if not tags["reference_types_checked"]:
        return None
    return not any(tags[key] for key in ASSISTANCE_KEYS)


def sealed_key_of(ctx: ExportContext, name: str) -> str | None:
    """Which sealed source carries this name, or None. Reported by the audit."""
    for key in SEALED_KEYS:
        if name in ctx.sealed.get(key, ()):
            return key
    for file_names in ctx.sealed_unrecognized.values():
        if name in file_names:
            return "unrecognized-shape"
    return None


def matched_filters(ctx: ExportContext, row: dict, func: str, tu: str | None, *,
                    disabled: frozenset[str] = frozenset()) -> list[str]:
    """Every named filter that drops this candidate, in ``FILTER_ORDER``.

    All matching filters are returned, not just the first, so the audit can report
    an independent count per filter as well as the additive funnel position.
    """
    matches: list[str] = []

    def check(name: str, hit: bool) -> None:
        if hit and name not in disabled:
            matches.append(name)

    check("parent-or-child-not-compiled",
          not (row.get("parent_compiled") and row.get("child_compiled")))
    check("parent-child-different-function", row.get("parent_addr") != row.get("child_addr"))
    # Owned only by edges whose endpoints BOTH compiled: an uncompiled endpoint is
    # `parent-or-child-not-compiled`'s case, and the two filters must not overlap or
    # one of them becomes untestable dead weight.
    check("not-improving-edge",
          bool(row.get("parent_compiled")) and bool(row.get("child_compiled"))
          and not row.get("is_improving"))
    check("empty-source-code",
          not row.get("parent_source_len") or not row.get("child_source_len"))
    check("function-has-exact-attempt", row.get("child_addr") in ctx.exact_functions)
    check("sealed-dev-heldout", func in ctx.sealed.get("dev", ())
          or func in ctx.sealed.get("heldout", ()))
    check("sealed-cluster-panel", func in ctx.sealed.get("cluster", ())
          or func in ctx.sealed.get("panel", ()))
    check("sealed-unrecognized-shape",
          any(func in names for names in ctx.sealed_unrecognized.values()))
    check("library-tu", is_library_tu(tu, ctx.library_patterns))
    check("missing-parent-feedback",
          row.get("has_edge") is False and not row.get("parent_diff_len"))
    return matches


def drop_reason(ctx: ExportContext, row: dict, func: str, tu: str | None, *,
                disabled: frozenset[str] = frozenset()) -> tuple[str | None, str]:
    """The first named filter that drops this candidate, with its reason."""
    matches = matched_filters(ctx, row, func, tu, disabled=disabled)
    if not matches:
        return None, ""
    return matches[0], FILTER_REASONS[matches[0]]


def sha_pair(ctx: ExportContext, row: dict, sources: dict[int, dict]) -> tuple[str, str, str, str]:
    """``(parent_sha, child_sha, parent_source, child_source)``.

    ``attempts.source_sha256`` is NULL on 22,310 of 52,804 rows in the research KB;
    where it is present it matches sha256(source_code) exactly (30,494 of 30,494
    verified), so a missing hash is computed from the stored source and the
    provenance of the value is recorded rather than assumed.
    """
    parent = sources.get(row["parent_id"], {})
    child = sources.get(row["child_id"], {})
    parent_sha = row.get("parent_sha") or ""
    child_sha = row.get("child_sha") or ""
    parent_source = parent.get("source_code") or ""
    child_source = child.get("source_code") or ""
    parent_from = "stored" if parent_sha else "computed-from-source_code"
    child_from = "stored" if child_sha else "computed-from-source_code"
    if not parent_sha:
        parent_sha = sha256_text(parent_source)
    if not child_sha:
        child_sha = sha256_text(child_source)
    return parent_sha, child_sha, parent_from, child_from


def build_record(ctx: ExportContext, row: dict, sources: dict[int, dict]) -> dict:
    child = sources.get(row["child_id"], {})
    parent = sources.get(row["parent_id"], {})
    func = _name_of(ctx, row["child_addr"])
    tu = _tu_of(ctx, row["child_addr"])
    asm, asm_path, asm_note, recipe = _artifacts(ctx, func)
    parent_sha, child_sha, parent_sha_from, child_sha_from = sha_pair(ctx, row, sources)
    generator = classify_generator(child.get("id"), child.get("strategy") or "",
                                   child.get("model") or "", row.get("prompt_len", 0),
                                   row.get("raw_len", 0), ctx.proposal_ids)
    provenance, lineage = provenance_of(bool(row.get("has_edge", True)), generator["family"])
    delta = None
    if row.get("parent_score") is not None and row.get("child_score") is not None:
        delta = round(row["child_score"] - row["parent_score"], 6)
    assistance = assistance_tags(ctx, row["parent_id"], parent.get("source_code") or "",
                                 child.get("source_code") or "", func, child_id=row["child_id"])
    record = {
        "id": f"{ctx.game}:{func}:{row['parent_id']}->{row['child_id']}",
        "game": ctx.game,
        "function": func,
        "func_addr": int(row["child_addr"]),
        "tu": tu,
        "provenance": provenance,
        "split": split_for(tu, func, ctx.seed),
        "input": {
            "target_asm": asm,
            "target_asm_available": asm is not None,
            "target_asm_source": asm_path,
            "target_asm_note": asm_note,
            "compiler": COMPILER,
            "flags": (recipe or {}).get("flags"),
            "recipe": recipe,
            "declarations": None,
            "declarations_note": DECLARATIONS_NOTE,
            "candidate_c": parent.get("source_code") or "",
            "compiler_outcome": {
                "compiled": bool(row.get("parent_compiled")),
                "stderr": parent.get("compiler_stderr") or "",
                "score": row.get("parent_score"),
                "exact": bool(row.get("parent_exact")),
                "diff": parent.get("diff_summary") or "",
            },
        },
        "target": {
            "source_c": child.get("source_code") or "",
            "exact": bool(row.get("child_exact")),
            "score": row.get("child_score"),
        },
        "meta": {
            "parent_attempt_id": row["parent_id"],
            "child_attempt_id": row["child_id"],
            "parent_sha256": parent_sha,
            "child_sha256": child_sha,
            "parent_sha256_source": parent_sha_from,
            "child_sha256_source": child_sha_from,
            "generator": generator,
            "mechanism_confidence": generator["confidence"],
            "lineage": lineage,
            "reconstruction_basis": None if lineage == "observed" else RECONSTRUCTED_BASIS,
            "relation": row.get("relation"),
            "action": row.get("action"),
            "feedback": row.get("feedback"),
            "score_delta": delta,
            "function_finished": row["child_addr"] in ctx.exact_functions,
            "assistance": assistance,
            "clean": is_clean(assistance),
            "feedback_available": bool(row.get("parent_diff_len")),
            "child_run_id": child.get("run_id"),
            "child_iteration": child.get("iteration"),
            "parent_created_at": row.get("parent_created"),
            "child_created_at": row.get("child_created"),
        },
    }
    if lineage == "reconstructed":
        record["meta"]["lineage_note"] = (
            "inferred pair: both attempts are real records with real compiler scores, but no "
            "attempt_edges row links them. This is NOT the original model interaction; the "
            "parent was selected by the recorded reconstruction rule.")
    return record


# --- splits -------------------------------------------------------------------

def split_for(tu: str | None, func: str, seed: int) -> str:
    """Deterministic TU-level split. Functions of one TU never separate."""
    key = tu if tu else f"func:{func}"
    bucket = int(hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()[:8], 16) % 10000
    if bucket < SPLIT_TEST_CUT:
        return "test"
    if bucket < SPLIT_DEV_CUT:
        return "dev"
    return "train"


def split_bucket(tu: str | None, func: str, seed: int) -> int:
    key = tu if tu else f"func:{func}"
    return int(hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()[:8], 16) % 10000


def split_manifest(functions: dict[str, dict], seed: int, *,
                   tier_counts: dict[str, dict[str, int]] | None = None) -> dict:
    """Per-split function/TU lists plus a digest over the assignment itself."""
    splits: dict[str, dict] = {name: {"functions": [], "tus": [], "records": 0,
                                      "records_by_provenance": {}}
                               for name in SPLIT_NAMES}
    for name, info in sorted(functions.items()):
        target = split_for(info["tu"], name, seed)
        splits[target]["functions"].append(name)
        if info["tu"] and info["tu"] not in splits[target]["tus"]:
            splits[target]["tus"].append(info["tu"])
        splits[target]["records"] += info["records"]
    for tier, counts in (tier_counts or {}).items():
        for split_name, count in counts.items():
            splits[split_name]["records_by_provenance"][tier] = count
    for name in SPLIT_NAMES:
        splits[name]["functions"].sort()
        splits[name]["tus"].sort()
        splits[name]["function_count"] = len(splits[name]["functions"])
        splits[name]["tu_count"] = len(splits[name]["tus"])
    assignment = [(name, split_for(info["tu"], name, seed))
                  for name, info in sorted(functions.items())]
    digest = hashlib.sha256(json.dumps(assignment, separators=(",", ":"))
                            .encode("utf-8")).hexdigest()
    return {"seed": seed, "unit": "translation-unit",
            "rule": "sha256(f'{seed}:{tu}')[:8] % 10000; <1500 test, <3000 dev, else train",
            "split_digest": digest, "splits": splits,
            "bucket": {name: split_bucket(info["tu"], name, seed)
                       for name, info in sorted(functions.items())}}


# --- export -------------------------------------------------------------------

def build_context(conn: sqlite3.Connection, *, game: str = GAME, seed: int = DEFAULT_SEED,
                  sets_dir: Path | None = None,
                  workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
                  reconstructed_per_function: int = DEFAULT_RECONSTRUCTED_PER_FUNCTION,
                  build_tree: Path | None = None
                  ) -> ExportContext:
    sealed = sealed_members(Path(sets_dir) if sets_dir else ROOT / "eval" / "sets")
    reference_types = None
    if build_tree is not None:
        from eval.status import reference_only_types
        reference_types = frozenset(reference_only_types(Path(build_tree)))
        if not reference_types:
            raise ValueError(f"no reference-only types found under {build_tree}: a wrong path "
                             "would otherwise tag every record clean")
        game_identifiers = game_header_identifiers(Path(build_tree))
        if not (game_identifiers["types"] or game_identifiers["prototypes"]):
            raise ValueError(f"no game/-only declarations found under {build_tree}/include")
    else:
        game_identifiers = None
    parents: dict[int, list[int]] = {}
    for parent, child in conn.execute(
            "select parent_attempt_id, child_attempt_id from attempt_edges"):
        parents.setdefault(child, []).append(parent)
    strategies = {row[0]: row[1] or "" for row in conn.execute(
        "select id, strategy from attempts")}
    members = sealed["members"]
    patterns, source = exclude_tu_patterns()
    functions = _function_index(conn)
    function_names = {info["name"] for info in functions.values()}
    # The unrecognized-shape heuristic also picks up scalar bookkeeping keys
    # ("schema_version": 1). Only names that are real functions of this KB can ever
    # reach a record, so the exclusion set is restricted to those -- and the raw
    # detection is kept in the audit rather than discarded.
    unrecognized = {file: sorted(name for name in names if name in function_names)
                    for file, names in sealed["unrecognized"].items()}
    unrecognized = {file: names for file, names in unrecognized.items() if names}
    return ExportContext(
        conn=conn, game=game, seed=seed, workspace_root=Path(workspace_root), sealed=members,
        sealed_union=set().union(*members.values()),
        sealed_extra=set(members["cluster"]) | set(members["panel"]),
        sealed_unrecognized=unrecognized,
        library_patterns=patterns, library_source=source,
        proposal_ids=_proposal_child_ids(conn), exact_functions=_exact_functions(conn),
        functions=functions,
        reconstructed_per_function=reconstructed_per_function,
        sealed_files=sealed["files"],
        sealed_unrecognized_raw=sealed["unrecognized"],
        reference_types=reference_types, game_identifiers=game_identifiers,
        strategies=strategies, parents=parents)


def export_dataset(conn: sqlite3.Connection, *, game: str = GAME, seed: int = DEFAULT_SEED,
                   sets_dir: Path | None = None,
                   workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
                   limit: int | None = None,
                   reconstructed_per_function: int = DEFAULT_RECONSTRUCTED_PER_FUNCTION,
                   collect_records: bool = True,
                   disabled_filters: Iterable[str] = (),
                   enabled_filters: Iterable[str] = (),
                   edge_epsilon: float = EDGE_IMPROVEMENT_EPSILON,
                   build_tree: Path | None = None) -> dict:
    """Build the dataset. Returns ``{"records", "audit", "splits", ...}``.

    ``enabled_filters`` switches on members of ``DEFAULT_OFF_FILTERS``; ``disabled_filters``
    switches off anything, and wins when a name is in both.
    """
    unknown = (set(enabled_filters) | set(disabled_filters)) - set(FILTER_ORDER)
    if unknown:
        raise ValueError(f"unknown filter(s): {sorted(unknown)}")
    disabled = frozenset((DEFAULT_OFF_FILTERS - set(enabled_filters)) | set(disabled_filters))
    ctx = build_context(conn, game=game, seed=seed, sets_dir=sets_dir,
                        workspace_root=workspace_root,
                        reconstructed_per_function=reconstructed_per_function,
                        build_tree=build_tree)
    edges = _edge_rows(conn)
    audit: dict = {
        "kb_edges": {},
        "observed": {},
        "reconstructed": {},
        "filters": [],
        "excluded_function_names": {},
        "policy": {
            "sealed_keys_read": list(SEALED_KEYS),
            "sealed_union_all_four_keys": len(ctx.sealed_union),
            "sealed_union_dev_heldout_only": len(set(ctx.sealed.get("dev", ()))
                                                 | set(ctx.sealed.get("heldout", ()))),
            "sealed_only_by_cluster_or_panel": len(
                ctx.sealed_union - set(ctx.sealed.get("dev", ()))
                - set(ctx.sealed.get("heldout", ()))),
            "library_tu_patterns": list(ctx.library_patterns),
            "library_tu_source": ctx.library_source,
            "improvement_epsilon": IMPROVEMENT_EPSILON,
            "edge_improvement_epsilon": edge_epsilon,
            "disabled_filters": sorted(disabled),
            "reference_types_checked": ctx.reference_types is not None,
            "reference_types_count": len(ctx.reference_types or ()),
            "game_header_only_types": len((ctx.game_identifiers or {}).get("types", ())),
            "game_header_only_prototypes": len((ctx.game_identifiers or {}).get("prototypes", ())),
            "reconstructed_basis": RECONSTRUCTED_BASIS,
            "reconstructed_per_function_budget": reconstructed_per_function,
            "kb_readonly": True,
        },
    }

    # ---- candidates: every unique edge plus every reconstructed pair
    #
    # Every filter is applied to this single pool in FILTER_ORDER, so each one is
    # load-bearing and independently counted. The compiled/improving gates are
    # filters here rather than build-time shortcuts for the same reason: a gate
    # that can only decline is a gate nobody has tested.
    seen_endpoints: set[tuple[int, int]] = set()
    duplicate_endpoints = 0
    parent_uncompiled = child_uncompiled = either_uncompiled = 0
    cross_function = 0
    improving_count = 0
    candidates: list[dict] = []
    for row in edges:
        key = (row["parent_id"], row["child_id"])
        if key in seen_endpoints:
            duplicate_endpoints += 1
            continue
        seen_endpoints.add(key)
        if not row["parent_compiled"]:
            parent_uncompiled += 1
        if not row["child_compiled"]:
            child_uncompiled += 1
        if not (row["parent_compiled"] and row["child_compiled"]):
            either_uncompiled += 1
        if row["parent_addr"] != row["child_addr"]:
            cross_function += 1
        row["has_edge"] = True
        row["is_improving"] = bool(
            row["parent_compiled"] and row["child_compiled"]
            and row["parent_score"] is not None and row["child_score"] is not None
            and row["child_score"] > row["parent_score"] + edge_epsilon)
        improving_count += 1 if row["is_improving"] else 0
        candidates.append(row)
    audit["kb_edges"] = {
        "edges_total": len(edges),
        "edges_duplicate_endpoints": duplicate_endpoints,
        "edges_unique_endpoints": len(seen_endpoints),
        "edges_parent_not_compiled": parent_uncompiled,
        "edges_child_not_compiled": child_uncompiled,
        "edges_either_not_compiled": either_uncompiled,
        "edges_compiled_both": len(edges) - either_uncompiled,
        "edges_cross_function": cross_function,
        "improving_edges": improving_count,
        "improving_edges_distinct_functions":
            len({row["child_addr"] for row in candidates if row["is_improving"]}),
        "improving_edges_with_exact_child":
            sum(1 for row in candidates if row["is_improving"] and row["child_exact"]),
    }

    # ---- reconstructed pairs
    pairs, recon_stats = _reconstructed_pairs(conn, epsilon=IMPROVEMENT_EPSILON)
    for row in pairs:
        row["has_edge"] = False
        row["is_improving"] = True
    audit["reconstructed"] = dict(recon_stats)
    candidates.extend(pairs)

    # ---- filters
    funnel: dict[str, dict] = {name: {"candidates_matched": 0, "removed_here": 0,
                                      "improving_removed_here": 0, "example_functions": []}
                               for name in FILTER_ORDER}
    # The two candidate populations have different cures, so the funnel is kept per
    # source as well as combined: "how many improving edges survive" is a different
    # number from "how many candidates survive".
    source_of: dict[int, str] = {}
    by_source: dict[str, dict[str, int]] = {
        "observed-edges": {"candidates": sum(1 for r in candidates if r["has_edge"]),
                           "improving_candidates": improving_count,
                           "survived_filters": 0, "kept": 0},
        "reconstructed-pairs": {"candidates": len(pairs), "improving_candidates": len(pairs),
                                "survived_filters": 0, "kept": 0},
    }
    per_source_funnel: dict[str, dict[str, int]] = {
        "observed-edges": {name: 0 for name in FILTER_ORDER},
        "reconstructed-pairs": {name: 0 for name in FILTER_ORDER},
    }
    improving_after: dict[str, int] = {}
    first_match: dict[int, str] = {}
    survivors: list[dict] = []
    dedup_removed = 0
    dedup_removed_improving = 0
    for row in candidates:
        key = "observed-edges" if row["has_edge"] else "reconstructed-pairs"
        source_of[id(row)] = key
        func = _name_of(ctx, row["child_addr"])
        tu = _tu_of(ctx, row["child_addr"])
        matches = matched_filters(ctx, row, func, tu, disabled=disabled)
        for name in matches:
            funnel[name]["candidates_matched"] += 1
        if matches:
            first = matches[0]
            first_match[id(row)] = first
            funnel[first]["removed_here"] += 1
            per_source_funnel[key][first] += 1
            if row["is_improving"]:
                funnel[first]["improving_removed_here"] += 1
            if len(funnel[first]["example_functions"]) < 5:
                funnel[first]["example_functions"].append(func)
            audit["excluded_function_names"].setdefault(first, set()).add(func)
            continue
        by_source[key]["survived_filters"] += 1
        survivors.append(row)

    # ---- duplicate (parent_sha256, child_sha256) collapse
    source_ids = [row["parent_id"] for row in survivors] + [row["child_id"] for row in survivors]
    sources = _sources(conn, source_ids)
    kept: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    for row in sorted(survivors, key=lambda r: (not r["has_edge"], r["child_addr"],
                                                r["child_id"], r["parent_id"])):
        parent_sha, child_sha, _, _ = sha_pair(ctx, row, sources)
        key = (parent_sha, child_sha)
        if key in seen_pairs and "duplicate-pair" not in disabled:
            dedup_removed += 1
            dedup_removed_improving += 1 if row["is_improving"] else 0
            per_source_funnel[source_of[id(row)]]["duplicate-pair"] += 1
            audit["excluded_function_names"].setdefault("duplicate-pair", set()).add(
                _name_of(ctx, row["child_addr"]))
            continue
        seen_pairs.add(key)
        row["_pair_key"] = key
        kept.append(row)
        by_source[source_of[id(row)]]["kept"] += 1
    if "duplicate-pair" not in disabled:
        funnel["duplicate-pair"]["removed_here"] = dedup_removed
        funnel["duplicate-pair"]["candidates_matched"] = dedup_removed
        funnel["duplicate-pair"]["improving_removed_here"] = dedup_removed_improving
    # The surviving-improving-edge curve is computed from the ordered filter stages,
    # not from candidate processing order: a filter that first fires late would
    # otherwise report a value that skips the stages before it.
    remaining = improving_count + len(pairs)
    for name in FILTER_ORDER:
        remaining -= sum(1 for row in candidates
                         if row["is_improving"] and first_match.get(id(row)) == name)
        if name == "duplicate-pair":
            # dedup happens after the candidate loop, so it is subtracted here rather
            # than picked up from first_match
            remaining -= dedup_removed_improving
        improving_after[name] = remaining
    improving_after["<exported>"] = remaining
    audit["filters"] = [dict(funnel[name], filter=name,
                             reason=FILTER_REASONS[name]) for name in FILTER_ORDER]
    audit["filters_removed_total"] = sum(entry["removed_here"] for entry in audit["filters"])
    audit["funnel_by_source"] = {
        source: {name: per_source_funnel[source][name] for name in FILTER_ORDER}
        for source in per_source_funnel}
    audit["improving_edges_remaining_after_filter"] = improving_after
    audit["candidates_before_filters"] = len(candidates)
    audit["candidates"] = {"observed_edges": by_source["observed-edges"]["candidates"],
                           "observed_improving_edges": improving_count,
                           "reconstructed_pairs": len(pairs),
                           "after_filters_and_dedup": len(kept)}
    audit["by_source"] = by_source

    # ---- reconstructed per-function budget (a budget, not an exclusion)
    budget_dropped: dict[str, int] = {}
    if reconstructed_per_function > 0:
        per_func: dict[int, int] = {}
        budgeted: list[dict] = []
        for row in kept:
            if row["has_edge"]:
                budgeted.append(row)
                continue
            used = per_func.get(row["child_addr"], 0)
            if used >= reconstructed_per_function:
                budget_dropped[_name_of(ctx, row["child_addr"])] = \
                    budget_dropped.get(_name_of(ctx, row["child_addr"]), 0) + 1
                continue
            per_func[row["child_addr"]] = used + 1
            budgeted.append(row)
        kept = budgeted
    limit_dropped = 0
    if limit is not None and limit >= 0 and len(kept) > limit:
        limit_dropped = len(kept) - limit
        kept = kept[:limit]

    # ---- records
    records: list[dict] = []
    if collect_records:
        records = [build_record(ctx, row, sources) for row in kept]
        records.sort(key=lambda r: (r["provenance"], r["function"], r["meta"]["child_attempt_id"],
                                    r["meta"]["parent_attempt_id"]))

    # ---- distributions
    by_tier: dict[str, int] = {}
    by_tier_split: dict[str, dict[str, int]] = {}
    by_strategy: dict[str, int] = {}
    by_tier_strategy: dict[str, dict[str, int]] = {}
    by_function: dict[str, int] = {}
    by_tu: dict[str, int] = {}
    per_function_info: dict[str, dict] = {}
    asm_available = asm_missing = 0
    recipe_available = 0
    mechanism: dict[str, int] = {}
    clean_by_split: dict[str, dict[str, int]] = {}
    assistance_counts = {key: 0 for key in ASSISTANCE_KEYS}
    finished_records = 0
    for row in kept:
        func = _name_of(ctx, row["child_addr"])
        tu = _tu_of(ctx, row["child_addr"])
        child = sources.get(row["child_id"], {})
        tags = assistance_tags(ctx, row["parent_id"],
                               sources.get(row["parent_id"], {}).get("source_code") or "",
                               child.get("source_code") or "", func, child_id=row["child_id"])
        for key in assistance_counts:
            assistance_counts[key] += 1 if tags[key] else 0
        clean_by_split.setdefault(split_for(tu, func, ctx.seed), {}).setdefault(
            str(is_clean(tags)).lower(), 0)
        clean_by_split[split_for(tu, func, ctx.seed)][str(is_clean(tags)).lower()] += 1
        if row["child_addr"] in ctx.exact_functions:
            finished_records += 1
        generator = classify_generator(child.get("id"), child.get("strategy") or "",
                                       child.get("model") or "", row.get("prompt_len", 0),
                                       row.get("raw_len", 0), ctx.proposal_ids)
        provenance, _lineage = provenance_of(bool(row["has_edge"]), generator["family"])
        split = split_for(tu, func, ctx.seed)
        by_tier[provenance] = by_tier.get(provenance, 0) + 1
        by_tier_split.setdefault(provenance, {}).setdefault(split, 0)
        by_tier_split[provenance][split] += 1
        strategy = child.get("strategy") or ""
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        by_tier_strategy.setdefault(provenance, {})
        by_tier_strategy[provenance][strategy] = by_tier_strategy[provenance].get(strategy, 0) + 1
        by_function[func] = by_function.get(func, 0) + 1
        by_tu[tu or "<none>"] = by_tu.get(tu or "<none>", 0) + 1
        mechanism[generator["confidence"]] = mechanism.get(generator["confidence"], 0) + 1
        info = per_function_info.setdefault(func, {"tu": tu, "records": 0})
        info["records"] += 1
        asm, _path, _note, recipe = _artifacts(ctx, func)
        if asm is None:
            asm_missing += 1
        else:
            asm_available += 1
        if recipe:
            recipe_available += 1
    audit["dataset"] = {
        "records": len(kept),
        "records_by_provenance": by_tier,
        "records_by_provenance_split": by_tier_split,
        "records_by_strategy": dict(sorted(by_strategy.items(), key=lambda kv: (-kv[1], kv[0]))),
        "records_by_provenance_strategy": {
            tier: dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
            for tier, counts in sorted(by_tier_strategy.items())},
        "records_by_function": dict(sorted(by_function.items(), key=lambda kv: (-kv[1], kv[0]))),
        "records_by_tu": dict(sorted(by_tu.items(), key=lambda kv: (-kv[1], kv[0]))),
        "distinct_functions": len(by_function),
        "distinct_tus": len(by_tu),
        "target_asm_available": asm_available,
        "target_asm_missing": asm_missing,
        "target_asm_missing_functions": sorted({_name_of(ctx, row["child_addr"])
                                                for row in kept
                                                if _artifacts(ctx, _name_of(ctx,
                                                                            row["child_addr"]))[0]
                                                is None}),
        "recipe_available": recipe_available,
        "declarations_available": 0,
        "declarations_note": DECLARATIONS_NOTE,
        "mechanism_confidence": mechanism,
        # "none" = reference types unchecked (no --build-tree), never read as clean
        "clean_by_split": {split: {("unchecked" if key == "none" else key): count
                                   for key, count in counts.items()}
                           for split, counts in sorted(clean_by_split.items())},
        "assistance": assistance_counts,
        "records_from_finished_functions": finished_records,
        "exact_child_records": sum(1 for row in kept if row.get("child_exact")),
        "max_records_one_function_share": (round(max(by_function.values()) / len(kept), 4)
                                           if kept else None),
    }
    audit["budgets"] = {
        "reconstructed_per_function": reconstructed_per_function,
        "reconstructed_dropped_by_budget": budget_dropped,
        "reconstructed_dropped_by_budget_total": sum(budget_dropped.values()),
        "limit": limit,
        "dropped_by_limit": limit_dropped,
    }
    audit["excluded_function_names"] = {name: sorted(names) for name, names
                                        in audit["excluded_function_names"].items()}
    audit["sealed"] = {
        "files": ctx.sealed.get("files", []),
        "counts_by_key": {key: len(ctx.sealed.get(key, ())) for key in SEALED_KEYS},
        "four_key_union": len(ctx.sealed_union),
        "dev_heldout_union": len(set(ctx.sealed.get("dev", ()))
                                 | set(ctx.sealed.get("heldout", ()))),
        "cluster_panel_extra": len(ctx.sealed_extra
                                   - set(ctx.sealed.get("dev", ()))
                                   - set(ctx.sealed.get("heldout", ()))),
        "cluster_panel_extra_names": sorted(
            ctx.sealed_extra - set(ctx.sealed.get("dev", ()))
            - set(ctx.sealed.get("heldout", ())))[:50],
        # The marginal leak the extra two keys close: names that reading only
        # dev+heldout (as eval/trajectory_factory.sealed_functions does) would miss.
        # It is 0 for the current snapshot because every cluster/panel-only name also
        # appears in some other file's dev/heldout list -- true today, not guaranteed
        # by anything, which is why the exporter reads all four keys regardless.
        "only_sealed_by_cluster_or_panel": sorted(
            ctx.sealed_union - set(ctx.sealed.get("dev", ()))
            - set(ctx.sealed.get("heldout", ())))[:50],
        "only_sealed_by_cluster_or_panel_count": len(
            ctx.sealed_union - set(ctx.sealed.get("dev", ()))
            - set(ctx.sealed.get("heldout", ()))),
        "unrecognized_shape_files": {name: names
                                     for name, names in ctx.sealed_unrecognized.items()},
        "unrecognized_shape_count": sum(len(names)
                                        for names in ctx.sealed_unrecognized.values()),
        # what the shape heuristic found before it was restricted to real KB functions
        "unrecognized_shape_raw_counts": {name: len(names) for name, names
                                          in ctx.sealed_unrecognized_raw.items()},
    }
    sealed_excluded = sorted(
        set(audit["excluded_function_names"].get("sealed-dev-heldout", []))
        | set(audit["excluded_function_names"].get("sealed-cluster-panel", []))
        | set(audit["excluded_function_names"].get("sealed-unrecognized-shape", [])))
    audit["sealed"]["excluded_function_sources"] = {name: sealed_key_of(ctx, name)
                                                    for name in sealed_excluded}
    splits = split_manifest(per_function_info, ctx.seed, tier_counts=by_tier_split)
    return {"records": records, "audit": audit, "splits": splits,
            "context": ctx}


def audit_filters(conn: sqlite3.Connection, **kwargs) -> dict:
    """Filter/audit only: the funnel and distributions, without materialising records."""
    kwargs.pop("collect_records", None)
    return export_dataset(conn, collect_records=False, **kwargs)["audit"]


# --- writing ------------------------------------------------------------------

def _json_digest(value: dict, drop: Sequence[str] = ("manifest_digest", "created_at")) -> str:
    """Stable content digest: the wall-clock stamp is not part of the manifest's identity.

    Without dropping ``created_at`` the digest changes on every run of an otherwise
    identical export, which makes it useless for verifying that two exports agree.
    """
    material = {key: item for key, item in value.items() if key not in drop}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def write_dataset(result: dict, out_dir: Path, *, kb: Path, seed: int,
                  audit_path: Path | None = None,
                  provenance_policy: str | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "repair_dataset.jsonl"
    payload = "".join(json.dumps(record, ensure_ascii=False) + "\n"
                      for record in result["records"])
    # bytes, not text: write_text translates newlines on Windows and the digest below would then
    # describe a file that does not exist
    jsonl.write_bytes(payload.encode("utf-8"))
    dataset_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "source-repair-dataset",
        "game": GAME,
        "created_at": int(time.time()),
        "seed": seed,
        "kb": str(kb),
        "kb_readonly": True,
        "compiler": COMPILER,
        "files": {"jsonl": jsonl.name, "records": len(result["records"]),
                  "jsonl_sha256": dataset_digest,
                  "jsonl_bytes": len(payload.encode("utf-8"))},
        "provenance_policy": provenance_policy or PROVENANCE_POLICY,
        "schema_additions": sorted(SCHEMA_ADDITIONS),
        "split_digest": result["splits"]["split_digest"],
        "splits": result["splits"],
        "audit": result["audit"],
    }
    manifest["manifest_digest"] = _json_digest(manifest)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n",
                                           encoding="utf-8")
    if audit_path is not None:
        audit_path = Path(audit_path)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(result["audit"], indent=2, default=str) + "\n",
                              encoding="utf-8")
    return manifest


PROVENANCE_POLICY = (
    "observed-repair: real attempt_edges row, both endpoints compiled=1, child score > parent "
    "score + 0.5. real lineage.\n"
    "deterministic-repair: observed edge whose child came from the project's own model-free "
    "repair machinery; the exact attempts.strategy is recorded and the registry entry names the "
    "live module that proves it is model-free.\n"
    "reconstructed-lineage: NO edge row. a real (candidate, feedback) state and a real, better "
    "compiled candidate for the same function, paired by a deterministic rule. the relationship "
    "is inferred and the record says so (meta.lineage='reconstructed', "
    "meta.reconstruction_basis=...); it is never the original model interaction."
)

SCHEMA_ADDITIONS = {
    "input.target_asm_available": "bool; false when no target.s body could be read",
    "input.target_asm_source": "path the assembly was read from, or the path that was missing",
    "input.target_asm_note": "why the assembly is unavailable, when it is",
    "input.flags": "compiler flags from the workspace recipe, or null",
    "input.recipe": "workspace compiler recipe dict, or null",
    "input.declarations_note": "why declarations are null",
    "meta.parent_sha256_source": "'stored' or 'computed-from-source_code'",
    "meta.child_sha256_source": "'stored' or 'computed-from-source_code'",
    "meta.score_delta": "child score - parent score",
    "meta.reconstruction_basis": "the rule that paired a reconstructed record, else null",
    "meta.lineage_note": "present only on reconstructed records",
    "meta.feedback_available": "whether the parent has a stored instruction diff",
    "meta.generator.mechanism_family": "normalised mechanism family, may be null",
    "meta.generator.mechanism_evidence": "why the mechanism is believed model-free / model",
    "meta.mechanism_confidence": "model-records | deterministic-registry | unverified",
    "meta.function_finished": "whether the function has any exact attempt",
    "meta.assistance": "what the record's text or lineage owes to the reference decomp",
    "meta.assistance.reference_types_checked": "false when no build tree was given",
    "meta.assistance.parent_reference_types": "reference-only types the parent uses, or null",
    "meta.assistance.child_reference_types": "reference-only types the child uses, or null",
    "meta.assistance.parent_game_header_types": "types only include/game declares, used by the "
                                                "parent, or null",
    "meta.assistance.child_game_header_types": "types only include/game declares, used by the "
                                               "child, or null",
    "meta.assistance.parent_game_header_prototypes": "functions/externs only include/game "
                                                    "declares, used by the parent",
    "meta.assistance.child_game_header_prototypes": "functions/externs only include/game "
                                                    "declares, used by the child, or null",
    "meta.assistance.parent_reference_seed_lineage": "the parent or an ancestor is a "
                                                     "reference-recovered/historical seed",
    "meta.assistance.child_reference_seed_lineage": "the child or an ancestor is a "
                                                    "reference-recovered/historical seed",
    "meta.clean": "no assistance of any tagged kind; null when reference types are unchecked",
}


# --- CLI ----------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kb", type=Path, required=True,
                        help="research KB (opened read-only)")
    parser.add_argument("--out", type=Path, required=True,
                        help="output directory for repair_dataset.jsonl and manifest.json")
    parser.add_argument("--audit", type=Path, default=None,
                        help="optional path for a standalone audit JSON report")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int, default=None,
                        help="export at most N records (deterministic order). The split manifest "
                             "and its digest then describe only the exported functions, so a "
                             "limited run's digest is not the canonical full-export digest")
    parser.add_argument("--sets-dir", type=Path, default=ROOT / "eval" / "sets")
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--game", default=GAME)
    parser.add_argument("--reconstructed-per-function", type=int,
                        default=DEFAULT_RECONSTRUCTED_PER_FUNCTION,
                        help="budget on reconstructed records per function, 0 = unlimited")
    parser.add_argument("--edge-epsilon", type=float, default=EDGE_IMPROVEMENT_EPSILON,
                        help="observed edges count as improving when child > parent + this")
    parser.add_argument("--exclude-finished", action="store_true",
                        help="enable function-has-exact-attempt (for a pool of states to repair)")
    parser.add_argument("--build-tree", type=Path, default=None,
                        help="target decomp tree (src/, include/) for reference-type tagging")
    parser.add_argument("--summary", action="store_true",
                        help="print only the headline numbers")
    args = parser.parse_args(argv)

    conn = connect_readonly(args.kb)
    started = time.time()
    result = export_dataset(conn, game=args.game, seed=args.seed, sets_dir=args.sets_dir,
                            workspace_root=args.workspace_root, limit=args.limit,
                            reconstructed_per_function=args.reconstructed_per_function,
                            edge_epsilon=args.edge_epsilon, build_tree=args.build_tree,
                            enabled_filters=(("function-has-exact-attempt",)
                                             if args.exclude_finished else ()))
    manifest = write_dataset(result, args.out, kb=args.kb, seed=args.seed,
                             audit_path=args.audit)
    audit = result["audit"]
    headline = {
        "records": audit["dataset"]["records"],
        "by_provenance": audit["dataset"]["records_by_provenance"],
        "by_provenance_split": audit["dataset"]["records_by_provenance_split"],
        "distinct_functions": audit["dataset"]["distinct_functions"],
        "distinct_tus": audit["dataset"]["distinct_tus"],
        "target_asm_available": audit["dataset"]["target_asm_available"],
        "target_asm_missing": audit["dataset"]["target_asm_missing"],
        "improving_edges": audit["kb_edges"]["improving_edges"],
        "exact_child_records": audit["dataset"]["exact_child_records"],
        "records_from_finished_functions": audit["dataset"]["records_from_finished_functions"],
        "clean_by_split": audit["dataset"]["clean_by_split"],
        "assistance": audit["dataset"]["assistance"],
        "split_digest": result["splits"]["split_digest"],
        "manifest_digest": manifest["manifest_digest"],
        "jsonl_sha256": manifest["files"]["jsonl_sha256"],
        "seconds": round(time.time() - started, 1),
    }
    print(json.dumps(headline, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
