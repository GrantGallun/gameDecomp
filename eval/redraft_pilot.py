"""Model redraft pilot: does a model-written alternative ROOT reach a match where more polishing
of the incumbent does not, at equal cost?

THE THEORY BEING TESTED
-----------------------
The campaign data (eval/results/refinement-data-20260927/analysis/draft_basin*.out) says the
starting draft largely decides the outcome: functions whose best root draft scores >=95 go exact
95/71/27% of the time in the <=30/31-80/81-200 instruction bands, those below 85 at 24/6/0%, and
deterministic search adds a mean 3-9 points from roots in 70-95. Search polishes within a basin;
it rarely moves a function between basins. If that is right, the model's leverage is not late
sub-edits but a DIFFERENT starting point: rebuild the function's source shape (control flow,
indexing, temporaries, types), then let the same deterministic polisher run from there.

THE DECISION IT INFORMS
-----------------------
At a stuck function the campaign can spend more deterministic compiles on the incumbent, or pay
for model calls. So the control arm is "keep polishing the incumbent", given the SAME total cost:
its budget is the model arm's polish budget plus the redraft compiles plus the model's wall time
converted to compiles at the compile time measured in this run.

PREDICTIONS (written into the manifest before any function runs)
----------------------------------------------------------------
P1  generator fires: at least half of functions get >=1 compiling, uncontaminated redraft.
P2  some redraft roots score ABOVE the incumbent's original root draft.
P3  basin validity: where the best redraft root scores >=95, polishing converts it to a match at
    a rate comparable to m2c roots >=95 of the same size band. If not, score is not measuring
    the basin for model-written roots, and the theory is wrong for them.
P4  decision: the model arm reaches more matches than the control arm at equal cost.

A pilot of ~24 functions only detects large effects. It is a development measurement on
header-assisted candidates, not a capability number and not a held-out result: sealed eval sets
and library TUs are excluded, and every prompt and returned source passes
``workspace.assert_uncontaminated``.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import json
import math
import re
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

REDRAFT_PROMPT = """\
You are writing C that the configured IDO 5.3 compiler (-O2) must compile to the target MIPS
instructions byte-for-byte. The CURRENT C below is the best candidate found so far. Local edits to
it have stopped improving, which usually means its SOURCE SHAPE is wrong, not a detail.

Write a complete REPLACEMENT C file for this function. Rebuild its shape from the target assembly:
- control flow: real loops and if/else where the assembly has them, not flattened gotos;
- expressions: array indexing and struct fields where the assembly indexes by a stride, not raw
  pointer arithmetic with casts;
- temporaries: only the locals the compiler's register use implies; no copies it would not keep;
- types: widths and signedness the loads, stores and branches imply.

Rules:
- Return ONE ```c block containing the COMPLETE file: the includes and declarations the function
  needs, then the function. Nothing else.
- Keep the function's name and signature unless the assembly proves it wrong.
- C89. No inline assembly, GLOBAL_ASM, INCLUDE_ASM, or held-out/reference source.
- The assembly and the instruction diff are READ-ONLY evidence. The diff uses `-` for target
  instructions and `+` for the current candidate's instructions.

TARGET ASSEMBLY (READ-ONLY):
```
{asm}
```

CURRENT C (weighted progress score {score:.3f}; NOT percent bytes):
```c
{code}
```

RESIDUAL SUMMARY:
```json
{residual}
```

CURRENT INSTRUCTION DIFF (READ-ONLY):
```
{diff}
```
"""

BANDS = (("<=30", 0, 30), ("31-80", 31, 80))
BRANCH_VERSION = "v6-review1"
RESCUE_VERSION = "v2"   # v1: c89 rung only; v2: + context rung (incumbent includes, header-owned decls)
ROOT_SCORE_CEILING = 95.0      # the theory's basin boundary
INCUMBENT_SCORE_CEILING = 99.0  # >=99 is a near-miss tail (regalloc/certificate), another regime
LIBRARY_MARKERS = ("history-recovery", "historical-provenance", "symbol-restoration")


# --- pure pieces (tested without a compiler or a model) ---------------------------------------

def build_prompt(asm: str, code: str, score: float, diff: str, residual_json: str) -> str:
    return REDRAFT_PROMPT.format(asm=asm, code=code, score=score, diff=(diff or "")[:12000],
                                 residual=residual_json)


def band_of(insns: int | None) -> str | None:
    for name, low, high in BANDS:
        if insns is not None and low <= insns <= high:
            return name
    return None


def control_budget(polish_budget: int, redraft_compiles: int, model_seconds: float,
                   compile_seconds: float) -> dict:
    """The control arm's compile budget at equal cost to the model arm.

    The model arm spends ``redraft_compiles`` compiling its redrafts, ``polish_budget`` polishing
    the best one, and ``model_seconds`` of generation. The control gets all of it as compiles.
    """
    if compile_seconds <= 0:
        raise ValueError("compile time must be measured, not assumed")
    model_equivalent = math.ceil(model_seconds / compile_seconds)
    return {"polish_budget": polish_budget, "redraft_compiles": redraft_compiles,
            "model_seconds": round(model_seconds, 3), "compile_seconds": round(compile_seconds, 4),
            "model_equivalent_compiles": model_equivalent,
            "budget": polish_budget + redraft_compiles + model_equivalent}


# Single-stream cost of one generation, fitted on the pilot's 241 sequential calls
# (least squares of total_duration on decode tokens): 127.3 decode tokens/s + 2.744 s per call,
# 0.00% error on the aggregate. Arms that share the GPU (OLLAMA_NUM_PARALLEL > 1) see each
# call's wall time stretch with its neighbours' load, which would charge an arm for running
# concurrently and make it incomparable with the sequential arms. Tokens do not stretch.
SECONDS_PER_DECODE_TOKEN = 1 / 127.3
SECONDS_PER_CALL = 2.744


def generation_seconds(meta: dict, wall: float) -> float:
    """Charge the ORIGINAL generation at the single-stream rate. A cache hit returns in
    milliseconds, and charging that would silently shrink the control arm's equal-cost budget
    on any rerun; charging a concurrent call's stretched wall time would inflate it."""
    tokens = (meta or {}).get("eval_count")
    if isinstance(tokens, int) and tokens > 0:
        return tokens * SECONDS_PER_DECODE_TOKEN + SECONDS_PER_CALL
    duration = (meta or {}).get("total_duration")
    if isinstance(duration, (int, float)) and duration > 0:
        return duration / 1e9   # Ollama reports nanoseconds
    if (meta or {}).get("_cache_hit"):
        raise ValueError("cache hit without a recorded generation time: cannot charge the model")
    return wall


def retire_partial(conn: sqlite3.Connection, run_id: str) -> int:
    """Move rows left under ``run_id`` by a killed run out of the way. Arms count cost and
    best result from every row with their run_id, and a function only runs when it has no
    result line, so any such rows are from an interrupted run: counting them would charge the
    rerun twice and let it inherit the dead run's results."""
    moved = conn.execute("update attempts set run_id = run_id || '-abandoned' where run_id=?",
                         (run_id,)).rowcount
    conn.commit()
    return moved


@contextlib.contextmanager
def function_lock(native: Path, function: str):
    """Non-blocking per-function lock across pilot processes. Arms compile in the same
    ``repos/<function>`` workspace with tags that can coincide, so two arms must never work on
    one function at once. Yields False when another process holds it."""
    import fcntl
    directory = native / "locks"
    directory.mkdir(exist_ok=True)
    with open(directory / f"{function}.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class Redraft:
    seed: int
    status: str            # ok | no-code | contaminated | duplicate | transport-error
    source: str = ""
    raw: str = ""
    seconds: float = 0.0
    error: str = ""


_INCLUDE = re.compile(r'^[ \t]*#\s*include\s*[<"]([^>"]+)[>"][^\n]*\n?', re.M)


def extract_source(text: str, resolves: Callable[[str], bool]) -> str:
    """The last fenced block that defines a function, keeping includes that RESOLVE.

    Not ``llm.extract_c``: it keeps only ``common.h`` and deletes every other include. The pilot's
    canary showed the model correctly keeping the incumbent's ``game/`` headers and extract_c
    deleting them, so 3 of 4 redrafts failed to compile on undefined types -- a harness failure
    that would have been booked against the model. Unresolvable includes (``<stdint.h>``, which
    IDO does not ship) are still dropped.
    """
    from solver import llm
    body = llm.THINK_RE.sub("", text or "")
    blocks = [block.strip() for block in llm.FENCE_RE.findall(body)]
    if not blocks:
        opened = llm.OPEN_FENCE.search(body)   # truncated generation: an unclosed fence
        blocks = [body[opened.end():].strip()] if opened else []
    blocks = [block for block in blocks if llm.FUNC_DEF_RE.search(block)]
    if not blocks:
        return ""   # prose, a refusal or assembly: nothing to compile
    source = blocks[-1]
    source = _INCLUDE.sub(lambda m: m.group(0) if resolves(m.group(1).strip()) else "", source)
    return source.strip()


def extract_redrafts(responses: list[tuple[int, str, float, str]], *, incumbent: str,
                     guard: Callable[[str], None],
                     resolves: Callable[[str], bool] = lambda name: True) -> list[Redraft]:
    """Turn raw model responses into candidate sources, rejecting what must not be compiled.

    ``responses`` is ``(seed, text, seconds, transport_error)``. ``guard`` raises on contamination.
    A redraft identical to the incumbent or to an earlier redraft is a duplicate, not a new root.
    """
    seen = {hashlib.sha256(incumbent.encode()).hexdigest()}
    out: list[Redraft] = []
    for seed, text, seconds, error in responses:
        if error:
            out.append(Redraft(seed, "transport-error", raw=text, seconds=seconds, error=error))
            continue
        source = extract_source(text, resolves)
        if not source:
            out.append(Redraft(seed, "no-code", raw=text, seconds=seconds))
            continue
        try:
            guard(source)
        except RuntimeError as exc:
            out.append(Redraft(seed, "contaminated", raw=text, seconds=seconds, error=str(exc)))
            continue
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest in seen:
            out.append(Redraft(seed, "duplicate", source=source, raw=text, seconds=seconds))
            continue
        seen.add(digest)
        out.append(Redraft(seed, "ok", source=source, raw=text, seconds=seconds))
    return out


# --- selection ---------------------------------------------------------------------------------

def select(campaign: sqlite3.Connection, *, other_exact_dbs: list[Path], sealed: set[str],
           library_patterns: tuple[str, ...], per_band: int, seed: int) -> dict:
    """Stuck functions whose best ROOT draft is below the basin boundary.

    Excluded, each counted: exact in any ledger, sealed eval set, library TU, no compiled root,
    root >= 95 (the theory already predicts these), incumbent >= 99 (near-miss tail), outside the
    size bands. Order within a band is a seeded hash, so the pick is reproducible and not
    cherry-picked by score.
    """
    from eval.repair_dataset import is_library_tu
    exact = {r[0] for r in campaign.execute(
        "select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
        "where a.exact=1")}
    for path in other_exact_dbs:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as other:
            exact |= {r[0] for r in other.execute(
                "select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                "where a.exact=1")}
    children = {r[0] for r in campaign.execute("select distinct child_attempt_id from attempt_edges")}
    info = {addr: {"name": name, "insns": insns, "tu": tu} for addr, name, insns, tu in campaign.execute(
        "select f.addr, f.name, f.insn_count, t.name from functions f left join tus t on t.id=f.tu_id")}
    root_best: dict[int, float] = {}
    incumbent: dict[int, tuple] = {}
    for aid, addr, score, created in campaign.execute(
            "select id, func_addr, score, created_at from attempts "
            "where coalesce(compiled,0)=1 and coalesce(exact,0)=0 and score is not null "
            "and source_code is not null"):
        if aid not in children:
            root_best[addr] = max(root_best.get(addr, -1.0), score)
        key = (score, created or 0, aid)
        if addr not in incumbent or key > incumbent[addr]:
            incumbent[addr] = key
    reasons: dict[str, int] = {}
    pools: dict[str, list] = {name: [] for name, _, _ in BANDS}

    def drop(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for addr, meta in info.items():
        name = meta["name"]
        if name in exact:
            drop("exact-in-a-ledger"); continue
        if name in sealed:
            drop("sealed-eval-set"); continue
        if is_library_tu(meta["tu"], library_patterns):
            drop("library-tu"); continue
        if addr not in root_best or addr not in incumbent:
            drop("no-compiled-root"); continue
        if root_best[addr] >= ROOT_SCORE_CEILING:
            drop("root-already->=95"); continue
        if incumbent[addr][0] >= INCUMBENT_SCORE_CEILING:
            drop("incumbent->=99-near-miss"); continue
        band = band_of(meta["insns"])
        if band is None:
            drop("outside-size-bands"); continue
        order = hashlib.sha256(f"{seed}:{name}".encode()).hexdigest()
        pools[band].append((order, {"function": name, "addr": addr, "band": band,
                                    "insns": meta["insns"], "tu": meta["tu"],
                                    "root_best": root_best[addr],
                                    "incumbent_attempt": incumbent[addr][2],
                                    "incumbent_score": incumbent[addr][0]}))
    chosen = []
    for band, pool in pools.items():
        chosen += [row for _, row in sorted(pool)[:per_band]]
    return {"selected": chosen, "eligible_by_band": {b: len(p) for b, p in pools.items()},
            "excluded": reasons}


def control_prefix(conn: sqlite3.Connection, run_id: str, compiles: int) -> dict:
    """The control arm's best result after its first ``compiles`` compiles, in the order it ran.

    The control is logged in execution order with a budget larger than any other arm's, so any
    arm can be compared with the control AT THAT ARM'S OWN COST without another run."""
    rows = conn.execute("select id, score, coalesce(compiled,0), coalesce(exact,0) from attempts "
                        "where run_id=? order by id limit ?", (run_id, max(compiles, 0))).fetchall()
    compiled = [r for r in rows if r[2]]
    best = max(compiled, key=lambda r: (r[3], r[1] or 0), default=None)
    return {"compiles": len(rows), "best_score": (best[1] or 0.0) if best else 0.0,
            "exact_attempt": best[0] if best and best[3] else None}


def run_branch_arm(row: dict, *, repo: Path, native: Path, conn, campaign, model: str,
                   samples: int, polish_budget: int, think: str, temperature: float,
                   beam: int = 2, depth: int = 3, equivalences: Path | None = None,
                   gated: bool = False, steer: bool = False, stage_filter: bool = False,
                   hybrid: bool = False) -> dict:
    """The branch-point arm as a TREE (solver/branch_points.py docstring has the measurements).

    Each expanded node gets its own model call on ITS residual, because one choice changes which
    lines and spellings matter below it. Every alternative is compiled; children are distinct
    OBJECTS that improve on their parent (exact > up); the best ``beam`` per depth are expanded
    next. Stops on an exact match, a depth with no improving child, or ``depth``. The best node is
    polished with the same polisher as every arm. The root is expanded with ``samples`` calls, the
    other nodes with one.
    """
    from eval import campaign_workers
    from solver import branch_points as bp
    from solver import llm, residual_sites, workspace
    name = row["function"]
    iso = campaign_workers.isolate(repo, native / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    target_asm = workspace.target_asm(ws, name)
    incumbent, recorded = campaign.execute(
        "select source_code, score from attempts where id=?", (row["incumbent_attempt"],)).fetchone()
    stamp = f"redraft-pilot-{name}"
    # Opt-in: mined IDO equivalences (patterns/equivalences.py). Without them this is exactly the
    # preregistered v4 arm; with them it is a separate arm with its own run id and model cache.
    eq_index = None   # NOT `index`: expand() loops `for index, alt in enumerate(...)`
    version = BRANCH_VERSION
    if equivalences is not None:
        from patterns.equivalences import Index
        eq_index = Index.load(equivalences)
        version = f"{BRANCH_VERSION}-eq"
    if gated:
        # Expert order (solver/invariants.py): children are accepted on OBJECT TRUTH level by
        # level, the prompt names the first unmatched level, and winning edits are ablated.
        version = f"{version}-gated"
    # v5 options (analysis/noop_kinds.out, optimizer_key_probe.out): steer the prompt with the
    # measured kinds IDO ignores/responds to; filter no-ops with IDO's own optimizer output before
    # compiling (same key => same object on 293/293, 97% of no-ops caught, 7x cheaper than a
    # compile); and enumerate the sensitive families deterministically at every node.
    for flag, tag in ((steer, "steer"), (stage_filter, "filter"), (hybrid, "hybrid")):
        if flag:
            version = f"{version}-{tag}"
    from kb.attempts import record_model_proposal
    from solver import ido_stages
    opt_keys: dict = {}
    seen_opt: set = set()
    key_seconds = [0.0]

    def opt_key(source):
        started = time.monotonic()
        key = ido_stages.optimizer_key(iso, ws, name, source)
        key_seconds[0] += time.monotonic() - started
        return key
    run_id = f"{stamp}-branch-{version}"
    retire_partial(conn, run_id)
    compile_times: list[float] = []
    tags: dict = {}

    def compile_logged(tag, code, **kw):
        started = time.monotonic()
        attempt = workspace.score(ws, iso, tag, code, conn=conn, func=name, **kw)
        compile_times.append(time.monotonic() - started)
        tags[attempt.receipt_id] = tag
        return attempt

    from solver import invariants as inv
    distances: dict = {}

    def truth(att):
        if att.exact:
            return (0,) * len(inv.LEVELS)
        if att.receipt_id not in distances:
            # Call-sequence edits and saved-register sets are not additive across
            # diff hunks. Missing artifacts must fail, never look like zero distance.
            distances[att.receipt_id] = inv.distance(
                listing(ws / "target_object_dump_normalized.s"),
                listing(ws / f"{tags[att.receipt_id]}_object_dump_normalized.s"))
        return distances[att.receipt_id]

    def rank(att):
        return inv.rank_key(truth(att), bool(att.exact), att.score or 0.0)

    def listing(path):
        return inv.parse(path.read_text())

    base = compile_logged(f"{name}_branch_base", incumbent, strategy="redraft-pilot-incumbent",
                          run_id=f"{stamp}-branch-base-{version}", run_kind="redraft-pilot")
    report = {**row, "arm": "branch", "branch_version": version, "beam": beam,
              "equivalence_rules": len(eq_index.rules) if eq_index else 0,
              "depth_limit": depth, "incumbent_score_here": base.score}
    if not base.compiled or abs((base.score or 0) - (recorded or 0)) > 1e-6:
        return {**report, "status": "incumbent-does-not-reproduce"}

    model_seconds, cache_hits, transport = 0.0, 0, []
    seen = {bp.object_key(True, bool(base.exact), base.diff or "")}
    levels: list[dict] = []
    all_nodes = [(base, incumbent, 0)]

    def expand(node_att, node_src, calls, level, reasks=1):
        """One node: ask, compile every alternative, and -- when nothing improved -- re-ask up to
        ``reasks`` times with what was tried and how the compiler treated it."""
        nonlocal model_seconds, cache_hits
        sites = residual_sites.render(node_src, name, node_att.diff or "",
                                      direct=node_att.source_attribution)
        node_key = bp.object_key(True, bool(node_att.exact), node_att.diff or "")
        node_key_obj = node_key
        kinds: dict[str, int] = {}
        children, tried, tried_keys = [], [], set()
        points_total = rejects_total = 0
        for round_index in range(1 + reasks):
            if round_index and children:
                break                      # something improved: no re-ask needed
            level_text = ""
            if gated:
                level_text = inv.guide(
                    inv.level(truth(node_att)),
                    listing(ws / "target_object_dump_normalized.s"),
                    listing(ws / f"{tags.get(node_att.receipt_id, '')}_object_dump_normalized.s"))
            prompt = bp.build_prompt(target_asm, node_src, node_att.score, node_att.diff, sites,
                                     tried=(bp.STEER if steer else "") + level_text
                                     + (eq_index.prompt_block() if eq_index else "")
                                     + (bp.tried_block(tried) if round_index else ""))
            workspace.assert_uncontaminated(prompt, iso, name)
            parsed = []
            seeds = range(1, calls + 1) if round_index == 0 else [100 * round_index + level]
            for seed in seeds:
                started = time.monotonic()
                try:
                    text, meta = llm.generate(llm.host(), model, prompt, timeout=900, think=think,
                                              temperature=temperature, num_predict=8000, seed=seed,
                                              cache_dir=native / "llm-cache",
                                              cache_namespace=f"branch-pilot-{version}",
                                              response_schema=bp.SCHEMA)
                    model_seconds += generation_seconds(meta, time.monotonic() - started)
                    cache_hits += bool(meta.get("_cache_hit"))
                    parsed.append(bp.parse(llm.THINK_RE.sub("", text).strip(), node_src))
                except Exception as exc:
                    model_seconds += time.monotonic() - started
                    transport.append(f"{type(exc).__name__}: {exc}"[:200])
            points = bp.merge(parsed)
            points_total += len(points)
            rejects_total += sum(len(p.rejects) for p in parsed)
            for point in points:
                for index, alt in enumerate(point.alternatives):
                    spelling = (point.start, point.end, "".join(alt.split()))
                    if spelling in tried_keys:
                        kinds["repeat"] = kinds.get("repeat", 0) + 1
                        continue
                    tried_keys.add(spelling)
                    label = (f"d{level}r{round_index} {point.slot}-{point.through}#{index}: "
                             f"{point.why[:80]}")
                    try:
                        source = bp.apply(node_src, [(point, alt)])
                        workspace.assert_uncontaminated(source, iso, name)
                    except (ValueError, RuntimeError):
                        kinds["refused"] = kinds.get("refused", 0) + 1
                        continue
                    if stage_filter:
                        if node_att.receipt_id not in opt_keys:
                            opt_keys[node_att.receipt_id] = opt_key(node_src)
                            # every node's object is "in the tree": an alternative that returns
                            # to an ancestor's object is a duplicate, not new
                            if opt_keys[node_att.receipt_id] is not None:
                                seen_opt.add(opt_keys[node_att.receipt_id])
                        k = opt_key(source)
                        if k is not None and (k == opt_keys[node_att.receipt_id] or k in seen_opt):
                            # Same optimizer output => same object: a no-op (or a duplicate of an
                            # object already in the tree) found without compiling it.
                            which = ("noop" if k == opt_keys[node_att.receipt_id] else "duplicate")
                            kinds[f"{which}-prefiltered"] = kinds.get(f"{which}-prefiltered", 0) + 1
                            # Never compiled, so no attempts row: log the proposal itself, or the
                            # record (and any measure of what the model proposes) loses it.
                            record_model_proposal(
                                conn, run_id=run_id, parent_attempt_id=node_att.receipt_id,
                                prompt=prompt, raw_response=source, status="duplicate",
                                model=model, kind=f"optimizer-key:{which}", hypothesis=point.why,
                                edits=[{"slot": point.slot, "alternative": alt}])
                            tried.append((point.slot, alt, "noop"))
                            continue
                        if k is not None:
                            seen_opt.add(k)
                    if eq_index is not None and eq_index.predicts_noop(node_src, source):
                        # A local statistical pattern omits types and whole-function
                        # context. Record its prediction, then measure the candidate.
                        kinds["predicted-noop"] = kinds.get("predicted-noop", 0) + 1
                    att = compile_logged(
                        f"{name}_bp_{len(compile_times)}", source, strategy="model-branch-point",
                        model=model, prompt=prompt,
                        raw_response=json.dumps({"slot": point.slot, "through": point.through,
                                                 "why": point.why, "alternative": alt,
                                                 "depth": level, "round": round_index},
                                                sort_keys=True),
                        run_id=run_id, run_kind="redraft-pilot",
                        parent_attempt_id=node_att.receipt_id, relation="branch-point",
                        action=label[:120])
                    key = bp.object_key(bool(att.compiled), bool(att.exact), att.diff or "")
                    outcome = bp.Outcome(point, alt, bool(att.compiled), att.score or 0.0,
                                         bool(att.exact), same_object=key == node_key)
                    kind = bp.classify(outcome, node_att.score)
                    if gated and kind not in ("exact", "not-compiled"):
                        # Object truth decides, not the score: a child that repairs a higher
                        # level is kept even if its score fell; one that only raised the score
                        # while breaking a higher level is not.
                        kind = "truth-up" if rank(att) < rank(node_att) else f"truth-no:{kind}"
                    if kind in ("up", "exact", "truth-up") and key in seen:
                        kind = "transposition"   # an object the tree already holds
                    kinds[kind] = kinds.get(kind, 0) + 1
                    kinds[f"round{round_index}:{kind}"] = kinds.get(f"round{round_index}:{kind}", 0) + 1
                    if kind in ("up", "exact", "truth-up"):
                        seen.add(key)
                        children.append((att, source, point.why[:120]))
                    else:
                        tried.append((point.slot, alt, kind))
        if hybrid:
            # The families IDO is measurably sensitive to, enumerated deterministically so the
            # model's proposals go to structure and meaning; prefiltered by the optimizer key.
            from solver import regalloc_mutations as rm
            generators = ((rm.statement_moves, 10), (rm.commutative_swaps, 10),
                          (rm.declaration_swaps, 8), (rm.local_types, 8))
            node_opt = opt_keys.get(node_att.receipt_id) or opt_key(node_src)
            opt_keys[node_att.receipt_id] = node_opt
            if node_opt is not None:
                seen_opt.add(node_opt)
            for generator, limit in generators:
                try:
                    variants = list(generator(node_src, name, limit=limit))
                except (ValueError, IndexError, KeyError):
                    continue
                for label, family, source in variants:
                    k = opt_key(source)
                    if k is not None and (k == node_opt or k in seen_opt):
                        kinds["det-prefiltered"] = kinds.get("det-prefiltered", 0) + 1
                        record_model_proposal(
                            conn, run_id=run_id, parent_attempt_id=node_att.receipt_id,
                            prompt="", raw_response=source, status="duplicate",
                            model="zero-model", kind=f"optimizer-key:det:{family}",
                            hypothesis=label)
                        continue
                    if k is not None:
                        seen_opt.add(k)
                    att = compile_logged(f"{name}_det_{len(compile_times)}", source,
                                         strategy="deterministic-branch", model="zero-model",
                                         run_id=run_id, run_kind="redraft-pilot",
                                         parent_attempt_id=node_att.receipt_id,
                                         relation="deterministic-branch", action=label[:120])
                    key2 = bp.object_key(bool(att.compiled), bool(att.exact), att.diff or "")
                    outcome = bp.Outcome(None, "", bool(att.compiled), att.score or 0.0,
                                         bool(att.exact), same_object=key2 == node_key_obj)
                    kind = bp.classify(outcome, node_att.score)
                    if gated and kind not in ("exact", "not-compiled"):
                        kind = "truth-up" if rank(att) < rank(node_att) else f"truth-no:{kind}"
                    if kind in ("up", "exact", "truth-up") and key2 in seen:
                        kind = "transposition"
                    kinds[f"det:{kind}"] = kinds.get(f"det:{kind}", 0) + 1
                    if kind in ("up", "exact", "truth-up"):
                        seen.add(key2)
                        children.append((att, source, f"deterministic {family}: {label}"[:120]))
        return children, {"points": points_total, "rejects": rejects_total, "kinds": kinds}

    ablations: list[dict] = []
    node_src_of: dict = {}

    def ablate(parent_src, child_src, child_att):
        """Revert each changed line-hunk of a winning edit on its own; a hunk whose removal leaves
        the object identical was not load-bearing. Keep only what the compiler responded to --
        the minimal edit is the node expanded next and the clean input for rule mining."""
        import difflib
        a = parent_src.splitlines(keepends=True)
        c = child_src.splitlines(keepends=True)
        ops = difflib.SequenceMatcher(None, a, c, autojunk=False).get_opcodes()
        changed = [i for i, op in enumerate(ops) if op[0] != "equal"]
        info = {"hunks": len(changed), "inert": 0, "ablated": False}
        if len(changed) < 2 or len(changed) > 6:
            return child_src, child_att, info
        want = bp.object_key(True, bool(child_att.exact), child_att.diff or "")

        def build(revert):
            out = []
            for i, (op, i1, i2, j1, j2) in enumerate(ops):
                out += a[i1:i2] if (op == "equal" or i in revert) else c[j1:j2]
            return "".join(out)
        inert = set()
        for i in changed:
            trial = build({i})
            att = compile_logged(f"{name}_ablate_{len(compile_times)}", trial,
                                 strategy="branch-ablation", run_id=run_id,
                                 run_kind="redraft-pilot", parent_attempt_id=child_att.receipt_id,
                                 relation="ablation", action=f"revert hunk {i}")
            if att.compiled and bp.object_key(True, bool(att.exact), att.diff or "") == want:
                inert.add(i)
        info["inert"] = len(inert)
        if not inert:
            return child_src, child_att, info
        minimal = build(inert)
        att = compile_logged(f"{name}_ablate_{len(compile_times)}", minimal,
                             strategy="branch-ablation", run_id=run_id, run_kind="redraft-pilot",
                             parent_attempt_id=child_att.receipt_id, relation="ablation",
                             action="minimal edit")
        if att.compiled and bp.object_key(True, bool(att.exact), att.diff or "") == want:
            info["ablated"] = True
            node_src_of[att.receipt_id] = node_src_of.get(child_att.receipt_id, parent_src)
            return minimal, att, info
        return child_src, child_att, info   # the hunks interact: keep the whole edit

    frontier = [(base, incumbent)]
    for level in range(1, depth + 1):
        children, stats = [], {"points": 0, "rejects": 0, "kinds": {}}
        for node_att, node_src in frontier:
            got, s = expand(node_att, node_src, samples if level == 1 else 1, level)
            for att, _src, _why in got:
                node_src_of[att.receipt_id] = node_src
            children += got
            stats["points"] += s["points"]
            stats["rejects"] += s["rejects"]
            for k, v in s["kinds"].items():
                stats["kinds"][k] = stats["kinds"].get(k, 0) + v
        if gated:
            children.sort(key=lambda c: rank(c[0]))
            kept = []
            for att, src, why in children[:beam]:
                src2, att2, info = ablate(node_src_of[att.receipt_id], src, att)
                ablations.append(info)
                kept.append((att2, src2, why))
            children = kept + children[beam:]
        else:
            children.sort(key=lambda c: (bool(c[0].exact), c[0].score or 0.0), reverse=True)
        all_nodes += [(att, src, level) for att, src, _ in children]
        levels.append({"depth": level, "expanded": len(frontier), **stats,
                       "improving_children": len(children),
                       "best": children[0][0].score if children else None,
                       "best_why": children[0][2] if children else None})
        if not children or children[0][0].exact:
            break
        frontier = [(att, src) for att, src, _ in children[:beam]]
    report["levels"] = levels
    report.update(model_seconds=round(model_seconds, 2), model_cache_hits=cache_hits,
                  transport_errors=transport)
    if gated:
        best_att, best_src, best_level = min(all_nodes, key=lambda n: rank(n[0]))
        report["ablations"] = ablations
        report["truth"] = {"incumbent": list(truth(base)), "best": list(truth(best_att)),
                           "incumbent_level": inv.level(truth(base)),
                           "best_level": inv.level(truth(best_att))}
    else:
        best_att, best_src, best_level = max(all_nodes, key=lambda n: (bool(n[0].exact),
                                                                       n[0].score or 0.0))
    # The user's hypothesis, measured: did expanding a chosen node find improvements that the
    # root's own branch points could not? (depth >= 2 beating the best depth-1 node)
    depth1 = max((n[0].score or 0.0 for n in all_nodes if n[2] == 1), default=None)
    report["tree"] = {"best_depth": best_level, "best_depth1_score": depth1,
                      "deeper_beat_depth1": bool(depth1 is not None and best_level >= 2
                                                 and (best_att.score or 0.0) > depth1)}
    polish_log: list[str] = []
    if best_level == 0:
        polish_log = ["no improving node: the tree is stuck at the incumbent; not polished again"]
    elif not best_att.exact:
        polish_log = polish(iso, conn, ws, name, best_src, best_att.receipt_id, polish_budget,
                            run_id=run_id, target_asm=target_asm)

    def verify(attempt_id: int) -> bool:
        again = workspace.score(ws, iso, f"{name}_bp_verify", conn.execute(
            "select source_code from attempts where id=?", (attempt_id,)).fetchone()[0],
            conn=conn, func=name, strategy="redraft-pilot-verify", run_id=f"{stamp}-verify",
            run_kind="redraft-pilot", parent_attempt_id=attempt_id, relation="reverify")
        return workspace.repair_complete(again)

    arm = arm_outcome(conn, run_id, verify)
    arm.log = polish_log
    report["branch_arm"] = arm.__dict__
    # One model-time -> compiles rate per function for every arm: the one measured in that
    # function's redraft/control run (see the branch canary note in the manifest).
    compile_seconds = row.get("_pilot_compile_seconds") or statistics.median(compile_times)
    report["compile_seconds_source"] = ("redraft-run" if row.get("_pilot_compile_seconds")
                                        else "own-median")
    # Key computations are charged as compile time too: filtering is not free.
    cost = arm.compiles + math.ceil((model_seconds + key_seconds[0]) / compile_seconds)
    report["equal_cost"] = {"compiles": arm.compiles, "model_seconds": round(model_seconds, 2),
                            "key_seconds": round(key_seconds[0], 2),
                            "compile_seconds": round(compile_seconds, 4), "cost_in_compiles": cost,
                            "control_at_same_cost": control_prefix(conn, f"{stamp}-control", cost)}
    report["status"] = "done"
    return report


def run_rescue_arm(row: dict, *, repo: Path, native: Path, conn, campaign, model: str,
                   samples: int, polish_budget: int, think: str, temperature: float,
                   use_model: bool = False) -> dict:
    """The SAME redrafts as the redraft arm, with a compile-rescue ladder
    (solver/compile_fallback.py): deterministic C89 repair, the campaign's normalizers, then --
    with ``use_model`` -- the model fixing its own code from the compiler error. The redrafts are
    read from this KB (not regenerated), so the only difference from the redraft arm is the
    rescue; their original generation time is charged in full."""
    from eval import campaign_workers
    from solver import compile_fallback, llm, workspace
    name = row["function"]
    iso = campaign_workers.isolate(repo, native / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    stamp = f"redraft-pilot-{name}"
    run_id = f"{stamp}-rescue-{'model' if use_model else 'det'}-{RESCUE_VERSION}"
    retire_partial(conn, run_id)
    incumbent = (campaign.execute("select source_code from attempts where id=?",
                                  (row["incumbent_attempt"],)).fetchone() or [""])[0]         if campaign is not None and row.get("incumbent_attempt") else ""
    redrafts = conn.execute(
        "select id, source_code, compiler_stderr, coalesce(wall_ms,0), coalesce(compiled,0), score "
        "from attempts where run_id=? and strategy='model-redraft' order by id",
        (f"{stamp}-model",)).fetchall()
    report = {**row, "arm": "rescue", "rescue_model": use_model, "rescue_version": RESCUE_VERSION,
              "redrafts": len(redrafts)}
    if not redrafts:
        return {**report, "status": "no-redrafts"}
    compile_times: list[float] = []
    model_seconds = sum(r[3] for r in redrafts) / 1000.0     # the original generations

    def compile_fn(label, code):
        started = time.monotonic()
        att = workspace.score(ws, iso, f"{name}_rescue_{len(compile_times)}", code, conn=conn,
                              func=name, strategy="redraft-rescue", run_id=run_id,
                              run_kind="redraft-pilot", action=label[:120],
                              model=model if label.startswith("model-fix") else "")
        compile_times.append(time.monotonic() - started)
        return att

    def fix_fn(prompt):
        started = time.monotonic()
        try:
            text, meta = llm.generate(llm.host(), model, prompt, timeout=900, think=think,
                                      temperature=0.2, num_predict=8000, seed=1)
            seconds = generation_seconds(meta, time.monotonic() - started)
        except Exception:
            return "", time.monotonic() - started
        include_root = iso / "include"
        return extract_source(text, lambda inc: (include_root / inc).is_file()), seconds

    roots, steps = [], []
    for rid, source, stderr, _ms, compiled, score in redrafts:
        if compiled:
            roots.append((score or 0.0, source, rid, "original"))
            continue
        out = compile_fallback.rescue(source, stderr or "", function=name, compile_fn=compile_fn,
                                      fix_fn=fix_fn if use_model else None,
                                      context_includes=compile_fallback.includes_of(incumbent),
                                      repo=iso)
        model_seconds += out.model_seconds
        steps.append({"redraft": rid, "rescued_by": out.rescued_by, "steps": out.steps})
        if out.attempt is not None:
            roots.append((out.attempt.score or 0.0, out.source, out.attempt.receipt_id,
                          out.rescued_by))
    report["rescues"] = steps
    report["rescued"] = sum(1 for s in steps if s["rescued_by"])
    report["compiling_roots"] = len(roots)
    report["model_seconds"] = round(model_seconds, 2)
    polish_log = ["no compiling redraft even after rescue"]
    if roots:
        score, source, rid, origin = max(roots, key=lambda r: r[0])
        report["best_root"] = {"score": score, "origin": origin}
        polish_log = polish(iso, conn, ws, name, source, rid, polish_budget, run_id=run_id,
                            target_asm=workspace.target_asm(ws, name))

    def verify(attempt_id: int) -> bool:
        again = workspace.score(ws, iso, f"{name}_rescue_verify", conn.execute(
            "select source_code from attempts where id=?", (attempt_id,)).fetchone()[0],
            conn=conn, func=name, strategy="redraft-pilot-verify", run_id=f"{stamp}-verify",
            run_kind="redraft-pilot", parent_attempt_id=attempt_id, relation="reverify")
        return workspace.repair_complete(again)

    arm = arm_outcome(conn, run_id, verify)
    arm.log = polish_log
    originals = [r for r in roots if r[3] == "original"]
    if originals and max(r[0] for r in originals) > arm.best_score:
        arm.best_score = max(r[0] for r in originals)       # an uncompiled-free original won
    report["rescue_arm"] = arm.__dict__
    compile_seconds = row.get("_pilot_compile_seconds") or statistics.median(compile_times or [1.0])
    cost = len(redrafts) + arm.compiles + math.ceil(model_seconds / compile_seconds)
    report["equal_cost"] = {"compiles": len(redrafts) + arm.compiles,
                            "model_seconds": round(model_seconds, 2),
                            "compile_seconds": compile_seconds, "cost_in_compiles": cost,
                            "control_at_same_cost": control_prefix(conn, f"{stamp}-control", cost)}
    report["status"] = "done"
    return report


def run_body_arm(row: dict, *, repo: Path, native: Path, conn, campaign, model: str,
                 samples: int, polish_budget: int, think: str, temperature: float,
                 repairs: int = 0) -> dict:
    """The redraft arm with the context separated (solver/compile_context.py): the model sees the
    incumbent's VALIDATED context read-only and writes only the function. Same functions, samples,
    polisher and equal-cost accounting as the whole-file redraft arm, so the compile rate and the
    outcome are directly comparable. Every failure is attributed: a name the context does not
    declare is 'needs-context'; anything else is 'code'."""
    from eval import campaign_workers
    from solver import compile_context, compile_fallback, llm, workspace
    name = row["function"]
    iso = campaign_workers.isolate(repo, native / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    target_asm = workspace.target_asm(ws, name)
    incumbent, recorded = campaign.execute(
        "select source_code, score from attempts where id=?", (row["incumbent_attempt"],)).fetchone()
    stamp = f"redraft-pilot-{name}"
    # (4 samples, 0 repairs) is the preregistered body arm; other budget splits are separate runs.
    variant = "" if (samples, repairs) == (4, 0) else f"-s{samples}-r{repairs}"
    run_id = f"{stamp}-body{variant}"
    retire_partial(conn, run_id)
    compile_times: list[float] = []

    def compile_logged(tag, code, **kw):
        started = time.monotonic()
        att = workspace.score(ws, iso, tag, code, conn=conn, func=name, **kw)
        compile_times.append(time.monotonic() - started)
        return att

    base = compile_logged(f"{name}_body_base", incumbent, strategy="redraft-pilot-incumbent",
                          run_id=f"{stamp}-body-base", run_kind="redraft-pilot")
    report = {**row, "arm": "body", "samples": samples, "repairs": repairs,
              "incumbent_score_here": base.score}
    if not base.compiled or abs((base.score or 0) - (recorded or 0)) > 1e-6:
        return {**report, "status": "incumbent-does-not-reproduce"}
    try:
        sp = compile_context.split(incumbent, name)
    except ValueError as exc:
        return {**report, "status": "no-split", "error": str(exc)}
    stub = compile_logged(f"{name}_body_stub", sp.assemble(compile_context.stub_body(sp.body)),
                          strategy="context-validation", run_id=f"{stamp}-body-context",
                          run_kind="redraft-pilot")
    report["context_valid"] = bool(stub.compiled)
    report["context_key"] = compile_context.key(iso, sp.context)
    known = compile_context.symbols(iso, sp.context)
    prompt = compile_context.body_prompt(name, sp, target_asm, base.score, base.diff)
    workspace.assert_uncontaminated(prompt, iso, name)

    model_seconds, bodies = 0.0, []
    for seed in range(1, samples + 1):
        started = time.monotonic()
        try:
            text, meta = llm.generate(llm.host(), model, prompt, timeout=900, think=think,
                                      temperature=temperature, num_predict=12000, seed=seed,
                                      cache_dir=native / "llm-cache",
                                      cache_namespace=f"body-pilot-v1{variant}")
            model_seconds += generation_seconds(meta, time.monotonic() - started)
            bodies.append((seed, text, compile_context.extract_body(text, name)))
        except Exception as exc:
            model_seconds += time.monotonic() - started
            bodies.append((seed, "", ""))
    outcomes, roots, seen = [], [], set()
    for seed, text, body in bodies:
        if not body:
            outcomes.append({"seed": seed, "status": "no-body"})
            continue
        candidate = sp.assemble(body)
        if candidate in seen or candidate == incumbent:
            outcomes.append({"seed": seed, "status": "duplicate"})
            continue
        seen.add(candidate)
        try:
            workspace.assert_uncontaminated(candidate, iso, name)
        except RuntimeError:
            outcomes.append({"seed": seed, "status": "contaminated"})
            continue
        att = compile_logged(f"{name}_body_{seed}", candidate, strategy="model-body", model=model,
                             prompt=prompt, raw_response=text, run_id=run_id,
                             run_kind="redraft-pilot", parent_attempt_id=base.receipt_id,
                             relation="model-body", action=f"seed {seed}")
        flagged = compile_context.undeclared(body, known)
        entry = {"seed": seed, "status": "ok", "compiled_raw": bool(att.compiled),
                 "score": att.score, "undeclared": [f["name"] for f in flagged]}
        if not att.compiled:
            diag, _count = compile_fallback.diagnostics(att)
            names = compile_fallback.unknown_names(diag)
            entry["failure"] = "needs-context" if names else "code"
            entry["unknown_names"] = names[:6]
            fixed = compile_fallback.c89_repair(candidate, name)
            if fixed != candidate:
                att2 = compile_logged(f"{name}_body_{seed}_c89", fixed, strategy="model-body-c89",
                                      run_id=run_id, run_kind="redraft-pilot",
                                      parent_attempt_id=att.receipt_id, relation="compile-admission",
                                      action="c89")
                entry["compiled_after_c89"] = bool(att2.compiled)
                if att2.compiled:
                    att, candidate = att2, fixed
            # Repair rounds: the model fixes its own function against the fixed context, told
            # clang's diagnostics and every name it used that nothing declares (with the closest
            # declared names). The budget split between fresh samples and repairs is the question.
            current_body, current_att = body, att
            for round_index in range(repairs if not att.compiled else 0):
                diag, _n = compile_fallback.diagnostics(current_att)
                flagged_now = compile_context.undeclared(current_body, known)
                prompt_r = compile_context.repair_prompt(name, sp, current_body, diag, flagged_now)
                started = time.monotonic()
                try:
                    text_r, meta_r = llm.generate(llm.host(), model, prompt_r, timeout=900,
                                                  think=think, temperature=0.2, num_predict=8000,
                                                  seed=100 + seed * 10 + round_index)
                    model_seconds += generation_seconds(meta_r, time.monotonic() - started)
                except Exception:
                    model_seconds += time.monotonic() - started
                    break
                fixed_body = compile_context.extract_body(text_r, name)
                if not fixed_body:
                    entry.setdefault("repair", []).append("no-body")
                    continue
                fixed_candidate = compile_fallback.c89_repair(sp.assemble(fixed_body), name)
                att3 = compile_logged(f"{name}_body_{seed}_r{round_index}", fixed_candidate,
                                      strategy="model-body-repair", model=model, prompt=prompt_r,
                                      raw_response=text_r, run_id=run_id, run_kind="redraft-pilot",
                                      parent_attempt_id=current_att.receipt_id,
                                      relation="compile-admission", action=f"repair {round_index + 1}")
                entry.setdefault("repair", []).append(bool(att3.compiled))
                current_body, current_att = fixed_body, att3
                if att3.compiled:
                    att, candidate = att3, fixed_candidate
                    entry["compiled_after_repair"] = round_index + 1
                    break
        if att.compiled:
            roots.append((att.score or 0.0, candidate, att.receipt_id))
        outcomes.append(entry)
    report["bodies"] = outcomes
    # Diversity as decomp must count it: DISTINCT compiled objects, not distinct samples -- two
    # spellings that compile identically are the same key.
    from solver import branch_points
    report["distinct_objects"] = len({branch_points.object_key(True, False, a) for a in [
        r[0] for r in conn.execute("select diff_summary from attempts where run_id=? and compiled=1",
                                   (run_id,)).fetchall()]})
    report["model_seconds"] = round(model_seconds, 2)
    polish_log = ["no compiling body"]
    if roots:
        score, source, rid = max(roots, key=lambda r: r[0])
        report["best_root"] = score
        polish_log = polish(iso, conn, ws, name, source, rid, polish_budget, run_id=run_id,
                            target_asm=target_asm)

    def verify(attempt_id: int) -> bool:
        again = workspace.score(ws, iso, f"{name}_body_verify", conn.execute(
            "select source_code from attempts where id=?", (attempt_id,)).fetchone()[0],
            conn=conn, func=name, strategy="redraft-pilot-verify", run_id=f"{stamp}-verify",
            run_kind="redraft-pilot", parent_attempt_id=attempt_id, relation="reverify")
        return workspace.repair_complete(again)

    arm = arm_outcome(conn, run_id, verify)
    arm.log = polish_log
    report["body_arm"] = arm.__dict__
    compile_seconds = row.get("_pilot_compile_seconds") or statistics.median(compile_times)
    cost = arm.compiles + math.ceil(model_seconds / compile_seconds)
    report["equal_cost"] = {"compiles": arm.compiles, "model_seconds": round(model_seconds, 2),
                            "compile_seconds": compile_seconds, "cost_in_compiles": cost,
                            "control_at_same_cost": control_prefix(conn, f"{stamp}-control", cost)}
    report["status"] = "done"
    return report


def select_calibration(campaign: sqlite3.Connection, *, sealed: set[str],
                       library_patterns: tuple[str, ...], per_band: int, seed: int) -> list[dict]:
    """P3's reference: functions that DID match, starting from a non-exact root draft >=95.

    The campaign-wide conversion rates (95%/71%) were earned with the full campaign's search; the
    pilot polishes with a fixed small budget. Re-polishing these roots with the pilot's polisher
    gives the conversion rate of a >=95 root UNDER THAT POLISHER, which is what a model-written
    >=95 root must be compared with. These functions are already matched: no new-match claim.
    """
    from eval.repair_dataset import is_library_tu
    exact_addrs = {r[0] for r in campaign.execute(
        "select distinct func_addr from attempts where exact=1")}
    children = {r[0] for r in campaign.execute("select distinct child_attempt_id from attempt_edges")}
    info = {addr: (name, insns, tu) for addr, name, insns, tu in campaign.execute(
        "select f.addr, f.name, f.insn_count, t.name from functions f left join tus t on t.id=f.tu_id")}
    best_root: dict[int, tuple] = {}
    for aid, addr, score in campaign.execute(
            "select id, func_addr, score from attempts where coalesce(compiled,0)=1 "
            "and coalesce(exact,0)=0 and score >= ? and source_code is not null",
            (ROOT_SCORE_CEILING,)):
        if aid in children or addr not in exact_addrs:
            continue
        if addr not in best_root or score > best_root[addr][0]:
            best_root[addr] = (score, aid)
    pools: dict[str, list] = {name: [] for name, _, _ in BANDS}
    for addr, (score, aid) in best_root.items():
        name, insns, tu = info.get(addr, (None, None, None))
        band = band_of(insns)
        if not name or band is None or name in sealed or is_library_tu(tu, library_patterns):
            continue
        order = hashlib.sha256(f"{seed}:calibration:{name}".encode()).hexdigest()
        pools[band].append((order, {"function": name, "band": band, "insns": insns,
                                    "root_attempt": aid, "root_score": score}))
    return [row for band in pools for _, row in sorted(pools[band])[:per_band]]


def run_calibration(row: dict, *, repo: Path, native: Path, conn, campaign,
                    polish_budget: int) -> dict:
    from eval import campaign_workers
    from solver import workspace
    name = row["function"]
    iso = campaign_workers.isolate(repo, native / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    source, recorded = campaign.execute("select source_code, score from attempts where id=?",
                                        (row["root_attempt"],)).fetchone()
    stamp = f"redraft-calibration-{name}"
    base = workspace.score(ws, iso, f"{name}_calib_base", source, conn=conn, func=name,
                           strategy="redraft-calibration-root", run_id=f"{stamp}-base",
                           run_kind="redraft-pilot")
    report = {**row, "root_score_here": base.score}
    if not base.compiled or abs((base.score or 0) - (recorded or 0)) > 1e-6:
        return {**report, "status": "root-does-not-reproduce"}

    def verify(attempt_id: int) -> bool:
        again = workspace.score(ws, iso, f"{name}_calib_verify", conn.execute(
            "select source_code from attempts where id=?", (attempt_id,)).fetchone()[0],
            conn=conn, func=name, strategy="redraft-pilot-verify", run_id=f"{stamp}-verify",
            run_kind="redraft-pilot", parent_attempt_id=attempt_id, relation="reverify")
        return workspace.repair_complete(again)

    log = polish(iso, conn, ws, name, source, base.receipt_id, polish_budget,
                 run_id=f"{stamp}-polish", target_asm=workspace.target_asm(ws, name))
    arm = arm_outcome(conn, f"{stamp}-polish", verify)
    arm.log = log
    return {**report, "status": "done", "polish": arm.__dict__}


def slim_kb(source: Path, dest: Path) -> None:
    """A private KB with the campaign's schema and static tables but none of its attempts.

    Copying the 8.7 GB campaign DB for a pilot is what filled C: before; the attempts it would
    carry are not needed, because the pilot logs its own.
    """
    if dest.exists():
        raise FileExistsError(dest)
    skip = {"attempts", "attempt_edges", "model_proposals", "attempt_runs",
            "campaign_worker_imports"}
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(dest) as dst:
        for name, sql in src.execute(
                "select name, sql from sqlite_master where type='table' and sql is not null "
                "and name not like 'sqlite_%'"):
            dst.execute(sql)
            if name not in skip:
                cols = [r[1] for r in src.execute(f"pragma table_info({name})")]
                marks = ",".join("?" * len(cols))
                dst.executemany(f"insert into {name} values ({marks})",
                                src.execute(f"select * from {name}"))
        for (sql,) in src.execute("select sql from sqlite_master where type='index' and sql is not null"):
            dst.execute(sql)


# --- the run (needs WSL, the IDO toolchain and Ollama) ------------------------------------------

@dataclass
class ArmResult:
    best_score: float
    matched: bool
    compiles: int
    best_attempt_id: int | None = None
    log: list[str] = field(default_factory=list)


def _arm_rows(conn: sqlite3.Connection, run_id: str) -> list[tuple]:
    return conn.execute("select id, score, coalesce(compiled,0), coalesce(exact,0) from attempts "
                        "where run_id=?", (run_id,)).fetchall()


def arm_outcome(conn: sqlite3.Connection, run_id: str,
                verify: Callable[[int], bool]) -> ArmResult:
    """Best of everything an arm compiled. The ``exact`` column is byte equality only; the
    success verdict (``workspace.repair_complete``) also needs the project frontend, which is not
    stored per row, so a byte-exact best is recompiled and checked by ``verify``."""
    rows = _arm_rows(conn, run_id)
    compiled = [r for r in rows if r[2]]
    best = max(compiled, key=lambda r: (r[3], r[1] or 0), default=None)
    if best is None:
        return ArmResult(0.0, False, len(rows))
    matched = bool(best[3]) and verify(best[0])
    return ArmResult(best[1] or 0.0, matched, len(rows), best[0])


def polish(repo: Path, conn, ws: Path, function: str, source: str, root_id: int, budget: int, *,
           run_id: str, target_asm: str) -> list[str]:
    """The SAME deterministic polisher for both arms: the rewrite beam, then register search from
    its best source when register faults dominate. Spending is counted from logged rows added
    during THIS call (an arm's run may already hold its redraft rows), so an arm that runs out of
    moves shows up as under-spending rather than being assumed to have used its budget."""
    from eval import agentrepair
    from solver import repair, residual, workspace
    start = len(_arm_rows(conn, run_id))
    best, best_source, log = repair.search(repo, function, source, ws, conn=conn,
                                           max_pairs=budget // 2, verbose=False,
                                           parent_attempt_id=root_id, run_id=run_id)
    rows = _arm_rows(conn, run_id)
    remaining = budget - (len(rows) - start)
    if remaining > 0 and best.compiled and not workspace.repair_complete(best):
        best_id = next((r[0] for r in rows[start:] if r[1] == best.score and r[2]), root_id)
        packet = residual.build(best, target_asm=target_asm)
        searched = agentrepair._regalloc_search(
            repo, conn, ws, function, best_source, packet, remaining, run_id=run_id,
            config={"pilot": "redraft-20260927"}, root_attempt_id=best_id)
        log.append("regalloc: " + ("declined (not register-dominant)" if searched is None
                                   else json.dumps(searched.summary(), default=str)[:300]))
    log.append(f"polish spent {len(_arm_rows(conn, run_id)) - start} of {budget}")
    return log[-12:]


def run_function(row: dict, *, repo: Path, native: Path, conn, campaign, model: str, samples: int,
                 polish_budget: int, think: str, temperature: float) -> dict:
    from eval import campaign_workers
    from solver import llm, residual, workspace
    name = row["function"]
    iso = campaign_workers.isolate(repo, native / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    target_asm = workspace.target_asm(ws, name)
    incumbent_source, recorded = campaign.execute(
        "select source_code, score from attempts where id=?", (row["incumbent_attempt"],)).fetchone()
    stamp = f"redraft-pilot-{name}"
    compile_times: list[float] = []

    def timed_score(tag, code, **kw):
        started = time.monotonic()
        attempt = workspace.score(ws, iso, tag, code, conn=conn, func=name, **kw)
        compile_times.append(time.monotonic() - started)
        return attempt

    base = timed_score(f"{name}_pilot_base", incumbent_source, strategy="redraft-pilot-incumbent",
                       run_id=f"{stamp}-base", run_kind="redraft-pilot",
                       extra={"campaign_attempt_id": row["incumbent_attempt"]})
    report = {**row, "incumbent_recorded_score": recorded, "incumbent_score_here": base.score,
              "incumbent_compiled_here": base.compiled}
    if not base.compiled or abs((base.score or 0) - (recorded or 0)) > 1e-6:
        report["status"] = "incumbent-does-not-reproduce"
        return report
    packet = residual.build(base, target_asm=target_asm)
    prompt = build_prompt(target_asm, incumbent_source, base.score, base.diff, packet.render())
    workspace.assert_uncontaminated(prompt, iso, name)

    responses = []
    cache_hits = 0
    for seed in range(1, samples + 1):
        started = time.monotonic()
        try:
            text, meta = llm.generate(llm.host(), model, prompt, timeout=900, think=think,
                                      temperature=temperature, num_predict=12000, seed=seed,
                                      cache_dir=native / "llm-cache",
                                      cache_namespace="redraft-pilot-v1")
            responses.append((seed, text, generation_seconds(meta, time.monotonic() - started), ""))
            cache_hits += bool(meta.get("_cache_hit"))
        except Exception as exc:  # a transport failure is a logged outcome, not a crash
            responses.append((seed, "", time.monotonic() - started, f"{type(exc).__name__}: {exc}"))
    include_root = iso / "include"
    redrafts = extract_redrafts(responses, incumbent=incumbent_source,
                                guard=lambda s: workspace.assert_uncontaminated(s, iso, name),
                                resolves=lambda inc: (include_root / inc).is_file())
    model_seconds = sum(r.seconds for r in redrafts)
    report["model_cache_hits"] = cache_hits

    scored = []
    for r in redrafts:
        if r.status != "ok":
            continue
        att = timed_score(f"{name}_redraft_{r.seed}", r.source, strategy="model-redraft",
                          model=model, prompt=prompt, raw_response=r.raw, run_id=f"{stamp}-model",
                          run_kind="redraft-pilot", parent_attempt_id=base.receipt_id,
                          relation="model-redraft", action=f"seed {r.seed}",
                          wall_ms=int(r.seconds * 1000))
        scored.append((att, r))
    report["redrafts"] = [{"seed": r.seed, "status": r.status, "seconds": round(r.seconds, 2),
                           "error": r.error[:200]} for r in redrafts]
    for att, r in scored:
        entry = next(e for e in report["redrafts"] if e["seed"] == r.seed)
        entry.update(compiled=att.compiled, score=att.score,
                     matched=workspace.repair_complete(att))
    compiling = [(a, r) for a, r in scored if a.compiled]
    report["redraft_compiled"] = len(compiling)
    budget = control_budget(polish_budget, len(scored), model_seconds,
                            statistics.median(compile_times))
    report["equal_cost"] = budget

    def verify(attempt_id: int) -> bool:
        source = conn.execute("select source_code from attempts where id=?",
                              (attempt_id,)).fetchone()[0]
        again = workspace.score(ws, iso, f"{name}_pilot_verify", source, conn=conn, func=name,
                                strategy="redraft-pilot-verify", run_id=f"{stamp}-verify",
                                run_kind="redraft-pilot", parent_attempt_id=attempt_id,
                                relation="reverify", action="independent recompile")
        return workspace.repair_complete(again)

    model_log: list[str] = []
    if compiling:
        best_att, best_r = max(compiling, key=lambda ar: (bool(ar[0].exact), ar[0].score))
        report["best_redraft_root"] = best_att.score
        if not best_att.exact:
            model_log = polish(iso, conn, ws, name, best_r.source, best_att.receipt_id,
                               polish_budget, run_id=f"{stamp}-model", target_asm=target_asm)
    else:
        report["best_redraft_root"] = None
        model_log = ["no compiling redraft: the arm is its redraft attempts only"]
    # Both arms report the raw best of what THEY compiled; the incumbent's score is reported
    # separately, so neither arm is credited with a source it did not produce.
    model_arm = arm_outcome(conn, f"{stamp}-model", verify)
    model_arm.log = model_log
    control_log = polish(iso, conn, ws, name, incumbent_source, base.receipt_id, budget["budget"],
                         run_id=f"{stamp}-control", target_asm=target_asm)
    control_arm = arm_outcome(conn, f"{stamp}-control", verify)
    control_arm.log = control_log
    report["model_arm"] = model_arm.__dict__
    report["control_arm"] = control_arm.__dict__
    report["status"] = "done"
    return report


def calibrate(args) -> int:
    from eval.repair_dataset import exclude_tu_patterns, sealed_functions
    args.native.mkdir(parents=True, exist_ok=True)
    campaign = sqlite3.connect(f"file:{args.campaign}?mode=ro", uri=True)
    path = args.native / "calibration-manifest.json"
    if path.exists():
        rows = json.loads(path.read_text())["selected"]
    else:
        rows = select_calibration(campaign, sealed=sealed_functions(),
                                  library_patterns=exclude_tu_patterns()[0],
                                  per_band=args.calibrate, seed=args.seed)
        path.write_text(json.dumps({
            "purpose": "P3 reference: conversion rate of >=95 m2c roots under the pilot polisher "
                       "and budget; the campaign-wide rates were earned with far more search",
            "added": time.strftime("%Y-%m-%dT%H:%M:%S"), "polish_budget": args.polish_budget,
            "selected": rows}, indent=2) + "\n")
    kb = args.native / "pilot.sqlite"
    if not kb.exists():
        slim_kb(args.campaign, kb)
    conn = sqlite3.connect(kb, timeout=120)
    out = args.native / "calibration.jsonl"
    done = {json.loads(l)["function"] for l in out.read_text().splitlines()} if out.exists() else set()
    for row in rows:
        if row["function"] in done:
            continue
        try:
            report = run_calibration(row, repo=args.repo, native=args.native, conn=conn,
                                     campaign=campaign, polish_budget=args.polish_budget)
        except Exception as exc:
            report = {**row, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:2000]}
        conn.commit()
        with out.open("a") as stream:
            stream.write(json.dumps(report, default=str) + "\n")
        print(json.dumps({k: report.get(k) for k in ("function", "band", "root_score", "status")}
                         | {"matched": (report.get("polish") or {}).get("matched"),
                            "best": (report.get("polish") or {}).get("best_score")}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--kb", type=Path, action="append", default=[],
                        help="other ledgers whose exact functions are excluded")
    parser.add_argument("--repo", type=Path, default=Path("/home/grant/decomp/sbk1"))
    parser.add_argument("--native", type=Path, required=True, help="experiment directory (WSL)")
    parser.add_argument("--per-band", type=int, default=12)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--polish-budget", type=int, default=120)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--think", default="medium")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N selected")
    parser.add_argument("--arm", choices=("redraft", "branch", "rescue", "body"), default="redraft",
                        help="branch: the branch-point arm, run after the redraft/control run")
    parser.add_argument("--branch-samples", type=int, default=2)
    parser.add_argument("--body-repairs", type=int, default=0,
                        help="body arm: model repair rounds per uncompiled body")
    parser.add_argument("--rescue-model", action="store_true",
                        help="rescue arm: let the model fix its own code after the free rungs")
    parser.add_argument("--steer", action="store_true",
                        help="branch arm: measured IDO-sensitivity table in the prompt")
    parser.add_argument("--stage-filter", action="store_true",
                        help="branch arm: skip no-ops found by IDO's optimizer output (no compile)")
    parser.add_argument("--hybrid", action="store_true",
                        help="branch arm: deterministic enumeration of the sensitive families per node")
    parser.add_argument("--gated", action="store_true",
                        help="branch arm only: expert-order gate (solver/invariants.py)")
    parser.add_argument("--equivalences", type=Path, default=None,
                        help="branch arm only: mined IDO equivalences (patterns/equivalences.py)")
    parser.add_argument("--calibrate", type=int, default=0,
                        help="run P3's calibration arm instead: N matched >=95 roots per band")
    args = parser.parse_args(argv)
    if args.calibrate:
        return calibrate(args)

    from eval.repair_dataset import exclude_tu_patterns, sealed_functions
    args.native.mkdir(parents=True, exist_ok=True)
    campaign = sqlite3.connect(f"file:{args.campaign}?mode=ro", uri=True)
    manifest_path = args.native / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        picked = select(campaign, other_exact_dbs=args.kb, sealed=sealed_functions(),
                        library_patterns=exclude_tu_patterns()[0], per_band=args.per_band,
                        seed=args.seed)
        manifest = {"theory": __doc__, "config": {k: str(v) for k, v in vars(args).items()},
                    "predictions": ["P1 >=50% of functions get a compiling uncontaminated redraft",
                                    "P2 some redraft roots score above the original root draft",
                                    "P3 redraft roots >=95 convert at a rate comparable to m2c "
                                    "roots >=95 in the same size band",
                                    "P4 model arm matches > control arm matches at equal cost"],
                    "basin_reference": {"<=30": 0.946, "31-80": 0.714},
                    **picked}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"selected": len(manifest["selected"]),
                      "eligible_by_band": manifest["eligible_by_band"],
                      "excluded": manifest["excluded"]}, indent=2), flush=True)
    if args.select_only:
        return 0
    kb = args.native / "pilot.sqlite"
    if not kb.exists():
        slim_kb(args.campaign, kb)
    conn = sqlite3.connect(kb, timeout=120)
    branch = args.arm in ("branch", "rescue", "body")
    arm_name = ("branch" + ("_eq" if args.equivalences else "") + ("_gated" if args.gated else "")
                + ("_steer" if args.steer else "") + ("_filter" if args.stage_filter else "")
                + ("_hybrid" if args.hybrid else "")
                if args.arm == "branch"
                else ("body" if (args.samples, args.body_repairs) == (4, 0)
                      else f"body_s{args.samples}_r{args.body_repairs}") if args.arm == "body"
                else "rescue_" + ("model" if args.rescue_model else "det") + f"_{RESCUE_VERSION}")
    results_path = args.native / (f"{arm_name}_results.jsonl" if branch else "results.jsonl")
    done = set()
    if results_path.exists():
        done = {json.loads(line)["function"] for line in results_path.read_text().splitlines()}
    if branch:
        # The branch arm is compared with the control arm's logged prefix, so a function's
        # redraft/control run must exist before its branch run.
        pilot = args.native / "results.jsonl"
        pilot_rows = [json.loads(l) for l in pilot.read_text().splitlines()] if pilot.exists() else []
        rate = {r["function"]: r["equal_cost"]["compile_seconds"] for r in pilot_rows
                if r.get("status") == "done"}
        ready = set(rate)
    rows = manifest["selected"][:args.limit] if args.limit else manifest["selected"]
    pending = collections.deque(r for r in rows if r["function"] not in done
                                and not (branch and r["function"] not in ready))
    deferred_in_a_row = 0
    while pending:
        row = pending.popleft()
        with function_lock(args.native, row["function"]) as held:
            if not held:
                # Another arm is on this function; take the next one and come back.
                pending.append(row)
                deferred_in_a_row += 1
                if deferred_in_a_row >= len(pending):
                    time.sleep(20)
                    deferred_in_a_row = 0
                continue
            deferred_in_a_row = 0
            _run_one(args, row, branch, rate if branch else {}, runner_conn=conn,
                     campaign=campaign, results_path=results_path)
    return 0


def _run_one(args, row, branch, rate, *, runner_conn, campaign, results_path):
    conn = runner_conn
    runner = {"branch": run_branch_arm, "rescue": run_rescue_arm,
              "body": run_body_arm}.get(args.arm, run_function)
    extra = {}
    if branch:
        row = {**row, "_pilot_compile_seconds": rate[row["function"]]}
        extra = ({"equivalences": args.equivalences, "gated": args.gated, "steer": args.steer,
                  "stage_filter": args.stage_filter, "hybrid": args.hybrid}
                 if args.arm == "branch" else {"use_model": args.rescue_model}
                 if args.arm == "rescue" else {"repairs": args.body_repairs})
    try:
        report = runner(row, repo=args.repo, native=args.native, conn=conn,
                        campaign=campaign, model=args.model,
                        samples=(args.branch_samples if args.arm == "branch" else args.samples),
                        polish_budget=args.polish_budget, think=args.think,
                        temperature=args.temperature, **extra)
    except Exception as exc:
        report = {**row, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:2000]}
    conn.commit()
    with results_path.open("a") as stream:
        stream.write(json.dumps(report, default=str) + "\n")
    if branch:
        same = (report.get("equal_cost") or {}).get("control_at_same_cost") or {}
        arm = report.get("branch_arm") or report.get("rescue_arm") or report.get("body_arm") or {}
        print(json.dumps({k: report.get(k) for k in ("function", "status", "band",
                                                      "incumbent_score", "rescued",
                                                      "compiling_roots")}
                         | {"arm": arm.get("best_score"), "matched": arm.get("matched"),
                            "control_same_cost": same.get("best_score")}, default=str),
              flush=True)
        return
    print(json.dumps({k: report.get(k) for k in ("function", "status", "band", "root_best",
                                                  "incumbent_score", "best_redraft_root")}
                     | {"model": (report.get("model_arm") or {}).get("best_score"),
                        "model_matched": (report.get("model_arm") or {}).get("matched"),
                        "control": (report.get("control_arm") or {}).get("best_score"),
                        "control_matched": (report.get("control_arm") or {}).get("matched")},
                     default=str), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
