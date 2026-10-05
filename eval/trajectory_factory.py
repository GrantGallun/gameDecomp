"""Manufacture the data the loop closes on: verified improvement trajectories, unattended.

The measured problem this exists to solve. The knowledge base holds 5,952 parent->child edges where
both ends compiled, of which only **357 improve**, and only 114 parents have both an improving and a
regressing child -- 251 preference pairs over 19 functions. That is the difference between having a
training loop and having something to train on, and no amount of training fixes it. The trajectories
have to be manufactured.

What a trajectory is here. A function's current best C is the parent. The model is asked for better C
in the context of the actual residual, each candidate is compiled and compared against the binary,
and every candidate is recorded WITH ITS PARENT. When one scores higher, that is an improving edge --
labelled by the compiler, not by a judge. Repeat from the new best. The result is exactly the
(state -> better edit, worse edit) material `eval/distill_policy.py` consumes, at whatever volume the
budget allows.

Four properties, because an unattended loop is only useful if it is safe to leave running:

- RESUMABLE. State lives in a JSON file written after every function, so a run can be stopped and
  restarted and will not redo work or lose a trajectory.
- BUDGETED. Caps on attempts, wall-clock seconds and functions; the loop stops at whichever binds
  first and says which.
- HELD OUT. The sealed evaluation functions are excluded before any work is chosen, and the exclusion
  is by name against `eval/sets/*.json`, not by convention. A factory that trains on the benchmark is
  worse than no factory.
- MULTI-GAME. A `GameSpec` names the repository, knowledge base and holdout for one target, so
  adding a game is a registry entry rather than a rewrite. That is the whole point: a model tuned on
  one game learns that game, and the adaptation has to come from trajectories carrying the compiler
  and ABI they came from. Every recorded attempt is tagged with its game and compiler for that reason.

The loop logic is separable from the model and the compiler: `Factory` takes a `Generator` and a
`Scorer`, so the control flow is tested with fakes and the real ones are adapters.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]

AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")

TECHNIQUE_DIRECTIVES = {
    "refine": "Make the smallest change that removes faults of the kind listed above. Keep the "
              "structure that already compiles.",
    "redirect": "The faults above are not the ones being fixed. Change the part of the source that "
                "causes the DOMINANT fault kind instead, even if it is a larger edit.",
    "representation": "Several attempts produced the same residual. The representation is wrong: "
                      "reconsider the types, the field layout, or the decomposition, not the "
                      "statement order.",
    "reseed": "Ignore the previous attempt's structure and write the function again from the target "
              "assembly.",
}
# The directives above are recorded per attempt for analysis. They are NOT put in the prompt: this
# module's first version hand-rolled both a prompt and a rules block, and the model replied with
# 19,791 characters of prose that the compiler reported as "Unterminated string or character
# constant" -- 20 attempts, 0% compiled. `solver/refine.FIRST_PROMPT` already states every one of
# those rules and was tuned across 31k attempts, so the factory uses it verbatim.
#
# `refine.sample_one` also records a measurement that decides this module's shape: across 8 functions,
# diff-guided refinement rescued exactly zero -- every match landed on the first attempt -- while
# resampling the same prompt gave 87.61%, 82.42% and 100% on three runs. So the factory is best-of-N
# over ONE prompt, not a conversation about a diff the model cannot localise.


def normalize(candidate: str) -> tuple[str, list[str]]:
    """Deterministically lower constructs the build refuses, before anything is compiled.

    This is not a nicety. The do-while refusal happens in the project's build helper, before IDO, so a
    rejected proposal produces no score, no diff and no fault classes -- it is not a bad trajectory,
    it is no trajectory. `rewrite_do_while` is the campaign's own sanctioned lowering (for/break, and
    it declines when `continue` would change meaning), so this reuses the ruling rather than inventing
    a second one.
    """
    applied: list[str] = []
    try:
        from tools.score_repo_function import rewrite_do_while
    except Exception:
        return candidate, applied
    try:
        lowered = rewrite_do_while(candidate)
    except ValueError:
        return candidate, applied                       # declines rather than lowering unsafely
    if lowered != candidate:
        applied.append("do-while -> for(;;)+break")
        candidate = lowered
    return candidate, applied


@dataclass(frozen=True)
class GameSpec:
    """One target. Adding a game is adding one of these, not editing the loop.

    `ready` is not bookkeeping. A game that is declared but not wired would otherwise run, produce
    zero trajectories and look like a hard game -- the same silent-decline failure this project keeps
    finding. An unready game refuses with the list of what it still needs.
    """
    name: str
    repo: Path
    kb: Path
    compiler: str
    note: str = ""
    ready: bool = False
    needs: tuple[str, ...] = ()


GAMES: dict[str, GameSpec] = {
    "sbk1": GameSpec("sbk1", Path.home() / "decomp" / "sbk1",
                     Path.home() / "decomp" / "kb-sbk1.sqlite", "ido-5.3",
                     "the benchmark: generation here is excluded from the sealed holdout",
                     ready=True),
    "dkr": GameSpec("dkr", Path.home() / "decomp" / "corpus" / "dkr",
                    Path.home() / "decomp" / "corpus-pairs" / "dkr.pal.v80.jsonl", "ido-5.3",
                    "same compiler and ISA as the benchmark, a different game: the cheapest "
                    "generalisation axis available",
                    ready=False,
                    needs=("a per-function oracle: DKR has no tools/claude bootstrap or build.sh, so "
                           "solver/workspace cannot drive it",
                           "a knowledge base of attempts; only the 983 fetched matched pairs exist")),
    "sbk2": GameSpec("sbk2", Path.home() / "decomp" / "sbk2",
                     Path.home() / "decomp" / "kb-sbk2.sqlite", "kmc-gcc",
                     "a different compiler: the sharpest generalisation test, and the only one that "
                     "would show the technique is not IDO-shaped",
                     ready=False,
                     needs=("a per-function scorer: tools/gcc_oracle compiles a whole translation "
                           "unit, so a candidate has to be spliced into the TU before it can be "
                           "scored, which is not the same shape as one function in a workspace",
                           "an extraction step: the compressed ROM needs the repo's own "
                           "decompress_baserom.py, which belongs in tools/sandbox_extract.py")),
}


def require_ready(spec: GameSpec) -> None:
    """Refuse an unwired game with its shopping list, rather than running and producing nothing."""
    if spec.ready:
        return
    raise SystemExit(
        f"game '{spec.name}' is declared but not wired; it needs:\n  - "
        + "\n  - ".join(spec.needs)
        + f"\n\nuntil then it would run and manufacture zero trajectories, which reads as a hard "
          f"game rather than a missing oracle. sbk1 is ready: {GAMES['sbk1'].note}.")


# --- what to work on ----------------------------------------------------------

def sealed_functions(sets_dir: Path | None = None) -> set[str]:
    """Function names in any frozen eval set. Never generated against, whatever the budget says.

    All four key shapes are read. The first version read only `dev` and `heldout`, which
    silently left out every manifest that names its members differently -- and those are not
    hypothetical: `logic_first_connected_dev_v*.json` use `cluster` and
    `principle_openbook_dev_v1.json` uses `panel`. A holdout that is not read is not a
    holdout, and the failure is invisible from outside: generation succeeds, the data looks
    fine, and the benchmark has been trained on.
    """
    directory = sets_dir or ROOT / "eval" / "sets"
    sealed: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("dev", "heldout", "cluster", "panel"):
            if key == "dev" and payload.get("kind") == "sealed-near-miss-split":
                continue            # its dev side is for generator development, not sealed
            for row in payload.get(key, []) or []:
                name = row.get("function") if isinstance(row, dict) else row
                if name:
                    sealed.add(name)
    return sealed


def learnability(best_score: float, fault: dict, owned_share: float,
                 ceiling: float = 95.0, floor: float = 25.0) -> float:
    """How likely a competent edit is to move this function's score. Not how close it is to done.

    Tractability ranking was built to FINISH matches and it puts a 99.7% function with three ordering
    faults at the top. That is the right target for a completion campaign and the wrong one here:
    manufacturing a learnable improvement edge needs room to move, and two points from exact is the
    hardest ask in the whole problem. The factory's product is a trajectory a model can learn from,
    so the objective is different and has to be stated as such:

        headroom          how much score is left to win
        x owned_share     whether an implemented pass can act on the residual at all
        x concentration   whether one fault class dominates, so the lesson is legible

    Ranked by this, a 60% function with one clean repairable fault class beats a 99% function with
    sixty relocation addends, which is the trade the factory wants and the completion campaign does
    not.
    """
    if not (floor <= best_score <= ceiling) or not owned_share:
        return 0.0
    total = sum(fault.values())
    concentration = (max(fault.values()) / total) if total else 0.0
    return round(((ceiling - best_score) / 100.0) * owned_share * concentration, 6)


def library_patterns() -> tuple[str, ...]:
    """The library translation units `eval/clean_set.py` already excludes, reused not re-derived.

    Left in, the learn objective's top picks were `__cosf` and `__sinf`: libultra is built with a
    different recipe (`-mips2`, per-TU overrides), so a trajectory generated against it teaches a
    codegen the game does not use -- and it is famous code a model has already seen. The project
    already drew this line for evaluation; the factory draws it for generation.
    """
    try:
        from eval.clean_set import EXCLUDE_TU
        return tuple(EXCLUDE_TU)
    except Exception:
        return ()


def candidates(spec: GameSpec, limit: int, min_attempts: int = 1,
               sealed: set[str] | None = None, objective: str = "learn",
               only: str | None = None) -> list[dict]:
    """Work items. `objective` decides the ranking, and the two objectives disagree on purpose."""
    sealed = sealed if sealed is not None else sealed_functions()
    conn = sqlite3.connect(f"file:{spec.kb}?mode=ro", uri=True)
    library = " ".join(f"and t.name not like '{pattern}'" for pattern in library_patterns())
    rows = conn.execute(
        "select f.addr, f.name, "
        " (select max(a.score) from attempts a where a.func_addr=f.addr and a.compiled=1), "
        " (select count(*) from attempts a where a.func_addr=f.addr), "
        " (select max(coalesce(a.exact,0)) from attempts a where a.func_addr=f.addr), "
        " (select a.diff_summary from attempts a where a.func_addr=f.addr and a.compiled=1 "
        "  order by a.score desc limit 1), "
        " (select a.id from attempts a where a.func_addr=f.addr and a.compiled=1 "
        "  order by a.score desc limit 1) "
        "from functions f join tus t on t.id = f.tu_id where exists "
        " (select 1 from attempts a where a.func_addr=f.addr and a.compiled=1) " + library
    ).fetchall()
    out = []
    for addr, name, best, attempts, solved, diff, best_id in rows:
        if name in sealed or solved or diff is None or attempts < min_attempts:
            continue
        if only and name != only:
            continue
        from solver import signals
        verdict = signals.analyse(diff, best or 0.0, False, True)
        owned = verdict.repairable + verdict.conditional_repair
        total = owned + verdict.no_repair_implemented + verdict.unrepairable
        fault = {axis: int(getattr(verdict, axis)) for axis in AXES}
        out.append({"addr": addr, "name": name, "best_score": best or 0.0,
                    "attempts": attempts, "best_attempt_id": best_id, "faults": fault,
                    "owned_share": round(owned / total, 3) if total else 0.0,
                    "learnability": learnability(best or 0.0, fault,
                                                 owned / total if total else 0.0)})
    if objective == "learn":
        out.sort(key=lambda r: (-r["learnability"], -r["attempts"]))
    else:
        out.sort(key=lambda r: (-(r["owned_share"] * 100), -(r["best_score"])))
    return out[:limit]


# --- the generation contract --------------------------------------------------
#
# THE BUG THIS SECTION EXISTS TO KILL (found in review, 2026-09-19, reproduced here).
#
# `run_function` kept ONE prompt across all rounds -- the asm/m2c leaf prompt from
# `make_context` -- while moving `parent` to whichever candidate scored best. So round 2
# asked exactly the question round 1 asked, but the resulting attempt was written with
# round 1's winner as its parent and `relation="refine"`. A local probe reproduced
# identical prompts with recorded parents moving 42 -> 101.
#
# Three separate things were wrong at once and it is worth naming each, because fixing
# two of three leaves a dataset that still lies:
#
#   1. INDEPENDENT DRAWS WERE LABELLED AS REFINEMENTS. Best-of-N samples from one prompt
#      are independent roots. `TRAINING.md` distinguishes "best-of-N samples are
#      independent roots" from real edges, and the review's whole point is that this
#      distinction is the product.
#   2. NO REPAIR WAS EVER REQUESTED. A repair needs the parent's C AND its compiler
#      outcome in the prompt. The factory had both in hand and sent neither, so even the
#      attempts it called refinements contained no view of the parent.
#   3. THE MODEL INTERACTION WAS DISCARDED. `OllamaGenerator.sample` returned `list[str]`
#      and dropped meta; `WorkspaceScorer.score` had nothing to store. 29 `factory-refine`
#      attempts in the KB have no prompt, no model and no raw response.
#
# A repair that was not requested is not a repair that failed. It is not a data point.

REPAIR_ACTIONS = ("fix-compile", "fix-diff", "no-op")

ACTION_DIRECTIVES = {
    # Recorded per attempt for analysis. NOT injected into the prompt: the project's own
    # DIFF_PROMPT/COMPILE_FAIL_PROMPT already state the reasoning, and `refine_one`'s
    # history is that hand-rolled prompt text produced 19 KB of prose and 0% compiled.
    "fix-compile": "the candidate did not compile; the compiler's own error is the input",
    "fix-diff": "the candidate compiled with a residual; the instruction diff is the input",
    "no-op": "nothing to repair: no parent attempt was available",
}


@dataclass
class RepairState:
    """What the model is shown when the action is a repair.

    Holding the state as a first-class object rather than as loose arguments is what
    makes the provenance checkable: the C in the prompt, the C in `source_code` on the
    parent row, and the C the diff was produced against are then the same string by
    construction instead of by convention.
    """

    source: str
    compiled: bool = False
    score: float = 0.0
    exact: bool = False
    diff: str = ""
    compiler_stderr: str = ""
    attempt_id: int | None = None

    @property
    def action(self) -> str:
        if not self.source:
            return "no-op"
        return "fix-diff" if self.compiled else "fix-compile"

    def feedback(self) -> str:
        """Exactly the text handed to the model, recorded as the edge's feedback."""
        if self.action == "fix-compile":
            return self.compiler_stderr or ""
        return self.diff or ""

    def digest(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()


def render_repair_prompt(state: RepairState, asm: str, *, history: str = "",
                         hints: str | None = None) -> str:
    """The project's own repair prompt, driven by the recorded state.

    Reuses `solver/refine.py`'s `COMPILE_FAIL_PROMPT` / `DIFF_PROMPT` verbatim rather than
    writing a third prompt. `refine_one` already learned this lesson in production: anchor
    on the BEST attempt and hand over the real diff, never a summary.
    """
    from solver.refine import COMPILE_FAIL_PROMPT, DIFF_PROMPT, catalog_hints
    if state.action == "fix-compile":
        return COMPILE_FAIL_PROMPT.format(code=state.source,
                                          errors=state.compiler_stderr)
    if state.action == "no-op":
        raise ValueError("a no-op state has nothing to repair and must not be rendered")
    return DIFF_PROMPT.format(asm=asm, score=state.score, code=state.source,
                              diff=(state.diff or "")[:4000], history=history,
                              hints=catalog_hints(state.diff or "") if hints is None
                              else hints)


@dataclass
class Proposal:
    """One model call and everything it must leave behind.

    `receipt` is a `solver.llm.GenerationReceipt` for the real generator and may be None
    for a fake. `parent` is set ONLY for a repair: a best-of-N draw is an independent root
    and has no candidate parent, which is requirement one of the handoff.
    """

    source: str = ""
    prompt: str = ""
    action: str = "independent"
    parent: RepairState | None = None
    receipt: object | None = None
    error: str = ""

    @property
    def status(self) -> str:
        if self.receipt is not None:
            return getattr(self.receipt, "status", "ok")
        if self.error:
            return "error"
        return "ok" if self.source else "empty"

    @property
    def lineage(self) -> str:
        """`root` for an independent draw, `observed` for a requested repair."""
        return "observed" if self.parent is not None else "root"

    @property
    def has_parent(self) -> bool:
        return self.parent is not None and self.parent.attempt_id is not None


class Generator(Protocol):
    """A proposer. `draw` is the contract the factory needs.

    `role` is `"independent"` or `"repair"` and is passed so a generator can apply
    different settings to the two populations -- a repair is a different task from a
    draft, and `refine.BASE_TEMP` already distinguishes them.
    """

    def draw(self, prompt: str, n: int, temperature: float, *,
             role: str = "independent", deadline: float | None = None) -> list[Proposal]: ...


class Scorer(Protocol):
    """Compiles a proposal and records it with its parent. Returns the outcome dict."""

    def score(self, func: str, proposal: Proposal) -> dict: ...


@dataclass
class Outcome:
    func: str
    attempts: int = 0            # model calls made, including failures and refusals
    admitted: int = 0            # reached the oracle: the build did not refuse it
    refusals: int = 0            # the model declined the target outright
    improving: int = 0           # IMPROVING ROUNDS -- prefer the child counts below
    improving_children: int = 0  # every child that beat its baseline, not just the round winner
    repair_requests: int = 0     # model calls that carried a parent's C and feedback
    independent_requests: int = 0
    errors: int = 0              # calls that raised or timed out
    no_extract: int = 0          # calls that returned text with no C in it
    dropped_calls: int = 0       # calls that produced no scorable candidate at all
    best_before: float = 0.0
    best_after: float = 0.0
    stopping_note: str = ""
    exact: bool = False
    stopped: str = ""
    prompt_hashes: list[str] = field(default_factory=list)
    parent_ids: list[int] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return self.best_after > self.best_before + 1e-9

    @property
    def distinct_prompts(self) -> int:
        return len(set(self.prompt_hashes))

    @property
    def independent_prompt(self) -> bool:
        """True when every draw asked the same question -- the old bug's signature."""
        return self.distinct_prompts <= 1


@dataclass
class Factory:
    """Best-of-N refinement against the compiler, recorded as a trajectory."""
    generator: Generator
    scorer: Scorer
    context_for: Callable[[str], dict]
    rounds: int = 3
    samples: int = 4
    temperature: float = 0.8
    improvement_epsilon: float = 0.5
    game: str = "sbk1"
    compiler: str = "ido-5.3"
    repair_from_round: int = 1
    normalizer: Callable[[str], tuple[str, list[str]]] = normalize
    state_path: Path | None = None
    log: list[dict] = field(default_factory=list)
    # A callable returning True when a human wants the machine back. Checked between rounds.
    should_stop: Callable[[], bool] = staticmethod(lambda: False)
    pause_note: Callable[[], str] = staticmethod(lambda: "")

    def describe(self, func: str, fault: dict, plateau: str) -> str:
        """The technique label recorded with an attempt. Analysis, not prompt text.

        Only three labels are derivable from a plateau and one residual: whether to refine, whether
        the representation is wrong, or whether to start again. `redirect` -- attack a DIFFERENT
        fault class than the leader -- needs the leader's profile to compare against, so it is
        computed in `eval/distill_policy.technique_of`, where both are in hand.
        """
        if plateau == "representation":
            return "representation"
        if plateau == "exhausted" or not any(fault.values()):
            return "reseed"
        return "refine"

    def generator_detail(self) -> str:
        """Why the generator produced nothing, in the generator's own words."""
        errors = getattr(self.generator, "errors", [])
        heads = getattr(self.generator, "raw_heads", [])
        dropped = getattr(self.generator, "dropped", 0)
        refusals = getattr(self.generator, "refusals", 0)
        if errors:
            return f"generation raised: {errors[-1][:300]}"
        if refusals and refusals >= len(heads):
            return (f"{refusals} refusal(s); the model declined this target outright, which is a "
                    f"target-selection problem, not a candidate problem")
        if heads:
            return (f"{refusals} refusal(s), {dropped} response(s) with no extractable C; "
                    f"last began: {heads[-1][:160]!r}")
        return "the generator returned nothing and recorded no reason"

    def repair_state(self, current: dict | None, seed_id: int | None,
                     seed_score: float) -> RepairState | None:
        """The state to repair: the best candidate seen so far, or nothing.

        The FIRST round has no candidate of its own, so it repairs whatever the knowledge
        base already recorded as this function's best attempt. When that row carries no
        stored source -- and most historical rows do not -- there is no repair to request
        and the round is an independent draw. Returning None rather than inventing a
        state is the point: `TRAINING.md` says never infer a pair from adjacent rows.

        Deliberately NOT implemented: fetching the parent's source out of the KB when only
        an id is known. The source WOULD have to come from the same row the score came
        from, and `candidates()` already reads that row's score, so a fallback that reads
        the text too is straightforward -- it is left out because this run's own candidates
        are the ones whose prompts and receipts are complete, and mixing in a reconstructed
        prompt from a row whose original prompt is lost would reintroduce exactly the
        provenance ambiguity this rewrite removes.
        """
        if current is not None and current.get("source"):
            return RepairState(
                source=current["source"], compiled=bool(current.get("compiled")),
                score=float(current.get("score") or 0.0), exact=bool(current.get("exact")),
                diff=current.get("diff") or "",
                compiler_stderr=current.get("compiler_stderr") or "",
                attempt_id=current.get("attempt_id"))
        return None

    def run_function(self, item: dict, budget: list[int], *,
                     deadline: float | None = None,
                     on_proposal: Callable[[dict], None] | None = None) -> Outcome:
        """One function's trajectory: independent draws plus REQUESTED repairs of the best.

        Round 0 asks the project's leaf prompt N times. Those N draws are independent
        roots and are recorded with no candidate parent. Every later round asks the repair
        prompt, built from the best compiled candidate so far and carrying that candidate's
        own compiler outcome, and the resulting attempt is recorded against that candidate's
        receipt id with the exact feedback text used as the edge's `feedback`.

        `repair_from_round` is when repairs may begin. It is 1 -- repairs start after the first
        round of independent draws have been compiled -- and that default is load-bearing for
        an UNSEEN function: `item["best_attempt_id"]` names a row in the knowledge base, and a
        function with no attempts has no such row, so a repair cannot be requested before this
        run has produced a compiled candidate of its own. A caller that only wants the
        independent draws (the equal-budget evaluation is exactly that) sets it beyond
        `rounds` and gets pure best-of-N with no repair prompt at all.

        `budget` is a one-element list so a run can stop mid-function without losing work.
        `deadline` is a monotonic wall-clock instant; a call that cannot start before it is
        not started. `on_proposal` is called after EVERY proposal is scored, so an
        interruption loses at most one candidate rather than a whole function.
        """
        outcome = Outcome(item["name"], best_before=item["best_score"],
                          best_after=item["best_score"])
        # Told, not inferred. A generator derives a per-draw seed from the function identity so
        # that draws are independent while the run stays reproducible; a generator that had to
        # reconstruct the function name from the prompt would be guessing.
        if hasattr(self.generator, "begin_function"):
            self.generator.begin_function(item["name"])
        context = self.context_for(item["name"])
        asm = context.get("asm", "")
        current: dict | None = None
        for _round in range(self.rounds):
            if budget[0] <= 0:
                outcome.stopped = "attempt budget"
                break
            if deadline is not None and time.monotonic() >= deadline:
                outcome.stopped = "time budget"
                break
            if self.should_stop():
                # Checked between rounds, so a pause costs at most the candidates already
                # scored and stored. The run is resumable, so stopping here loses nothing.
                outcome.stopped = "paused"
                outcome.stopping_note = self.pause_note()
                break
            repairing = _round >= self.repair_from_round
            state = (self.repair_state(current, item.get("best_attempt_id"),
                                       item["best_score"]) if repairing else None)
            if repairing and state is None:
                # No compiled candidate to repair builds a repair request out of nothing.
                # Stop honestly and say so instead of silently repeating round 0.
                outcome.stopped = "no repair state"
                outcome.stopping_note = (
                    "round 0 produced no compiling candidate to repair; a repair prompt "
                    "needs the parent's C and its compiler outcome, and neither exists")
                break
            if state is None:
                prompt, role = context["prompt"], "independent"
            else:
                prompt = render_repair_prompt(state, asm)
                role = "repair"
            wanted = min(self.samples, budget[0])
            before_refusals = getattr(self.generator, "refusals", 0)
            if hasattr(self.generator, "draw"):
                proposals = self.generator.draw(prompt, wanted, self.temperature,
                                                role=role, deadline=deadline)
                # A generator that returns NOTHING is making no claim about the target. Its
                # calls are unaccounted for, so this is the one case where the count is
                # unknown rather than zero, and saying so is the honest report.
                if not proposals:
                    outcome.stopped = "no candidates returned"
                    outcome.stopping_note = self.generator_detail()
                    break
                # Refusals on this path are counted from each proposal's own status below.
                # Adding the generator's internal counter as well would double every refusal,
                # which is how a fix for one accounting defect becomes another one.
            else:
                # A generator written against the two-argument `sample` contract: it
                # returned a bare string per call, so each string IS one call and each call
                # is counted even when the string is empty. Detection is by CAPABILITY, not by
                # catching TypeError -- a TypeError raised inside a real `draw` would
                # otherwise be swallowed and re-run as `sample`, turning a generator bug into
                # a duplicate round of silent calls.
                raw = self.generator.sample(prompt, wanted, self.temperature)
                proposals = [Proposal(source=s, prompt=prompt, action=role) for s in raw]
                # This path carries no per-proposal status, so the generator's own refusal
                # counter is the only evidence the calls were refusals.
                outcome.refusals += getattr(self.generator, "refusals", 0) - before_refusals
            if role == "repair":
                outcome.repair_requests += len(proposals)
            else:
                outcome.independent_requests += len(proposals)
            outcome.prompt_hashes.append(
                hashlib.sha256(prompt.encode("utf-8")).hexdigest())
            scored = []
            for proposal in proposals:
                if budget[0] <= 0:
                    outcome.stopped = "attempt budget"
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    outcome.stopped = "time budget"
                    break
                # A refused, errored or empty call CONSUMES the budget. It used to be free,
                # which made a refusal loop look cheaper than a working target and made the
                # budget a promise the run did not keep. EVERY call is counted, including the
                # ones with nothing to compile, because a counter that only counts successes
                # cannot size a campaign.
                budget[0] -= 1
                outcome.attempts += 1
                if not proposal.source:
                    outcome.dropped_calls += 1
                if proposal.action == "independent":
                    proposal.action = role
                if proposal.status == "refusal":
                    outcome.refusals += 1
                elif proposal.status in ("error", "timeout"):
                    outcome.errors += 1
                elif proposal.status == "no-extract":
                    outcome.no_extract += 1
                if not proposal.source:
                    # Counted, budgeted, receipted -- and not compiled. There is no C to
                    # compile, and handing prose to IDO is how 19 KB of reasoning once became
                    # a "model error" instead of the extraction failure it was.
                    continue
                if state is not None:
                    # The repair is against the state the prompt was rendered from, and that
                    # state's receipt id is the edge's parent. Assigning it here rather than
                    # trusting the generator keeps parentage a property of the REQUEST.
                    #
                    # A generator that attaches NO parent object must still get an edge, so the
                    # state itself is attached. Measured 2026-09-20: `ServeGenerator` returned
                    # proposals with `parent=None` for repair draws, and the collection pilot
                    # stored eight repairs with `parent_attempt_id` NULL and no `attempt_edges`
                    # row -- prompts that correctly quoted the parent's C and score, recorded as
                    # though no parent had ever existed. An unlinked repair is not training data.
                    if proposal.parent is None:
                        proposal.parent = state
                    proposal.parent.attempt_id = state.attempt_id
                lowered, applied = self.normalizer(proposal.source)
                proposal.source = lowered
                result = self.scorer.score(item["name"], proposal)
                result["normalized"] = applied
                # The repaired state is the NORMALIZED source that was actually compiled and
                # actually logged on this attempt's row. The scorer is not trusted to report it
                # back: a scorer that echoed the pre-normalization text would make the next
                # repair prompt quote a candidate that compiles differently from the one its
                # diff was produced against -- a lineage that looks correct and is not.
                result["source"] = proposal.source
                scored.append(result)
                if result.get("compiled"):
                    outcome.admitted += 1
                    # Every child that beats the BASELINE is an improving child, whether or
                    # not it wins its round. The old metric counted winning rounds, which is
                    # a smaller number that is not the dataset's size.
                    if (result.get("score") or 0.0) > outcome.best_before + self.improvement_epsilon:
                        outcome.improving_children += 1
                outcome.parent_ids.append(
                    proposal.parent.attempt_id if proposal.has_parent else 0)
                self.log.append({"func": item["name"], "round": _round,
                                 "game": self.game, "compiler": self.compiler,
                                 "action": proposal.action, "lineage": proposal.lineage,
                                 "status": proposal.status,
                                 "parent_attempt_id": (proposal.parent.attempt_id
                                                       if proposal.has_parent else None),
                                 "compiled": result.get("compiled"),
                                 "score": result.get("score"), "exact": result.get("exact"),
                                 "faults": result.get("faults"),
                                 "normalized": result["normalized"],
                                 "attempt_id": result.get("attempt_id"),
                                 "prompt_sha256": result.get("prompt_sha256")})
                if on_proposal:
                    on_proposal(self.log[-1])
            if not scored:
                # Every proposal was a receipt with no C in it: a refusal, an error, a
                # timeout or prose. They are counted and budgeted above; there is nothing to
                # compile. One label for one cure -- choose a different target or a different
                # prompt shape -- with the generator's own reason attached.
                outcome.stopped = "no extractable C"
                outcome.stopping_note = self.generator_detail()
                break
            best = max(scored, key=lambda r: (bool(r.get("exact")), r.get("score") or 0.0))
            if (best.get("score") or 0.0) > outcome.best_after + self.improvement_epsilon:
                outcome.best_after = best["score"]
                outcome.improving += 1
            if best.get("compiled"):
                # A COPY, not the scorer's own dict. Aliasing it meant `repair_state` read
                # `source` from whatever the scorer happened to put there -- and a scorer that
                # returns no `source` produced a repair prompt that named a parent whose C it
                # did not contain. Caught by the identity test, not by inspection.
                current = dict(best)
            if best.get("exact"):
                outcome.exact = True
                outcome.stopped = "exact"
                break
        return outcome


def run(factory: Factory, items: Sequence[dict], *, max_attempts: int = 100,
        max_seconds: int = 3600, checkpoint: Callable[[dict], None] | None = None,
        on_proposal: Callable[[dict], None] | None = None) -> dict:
    """Drive the factory over work items under both budgets.

    The wall-clock budget is enforced INSIDE each function, not only between them. The old
    loop tested elapsed time once per function, so one slow function could overrun the cap
    by an unbounded amount -- and the cap is a promise the collection pilot is sized on.

    `checkpoint` fires after each function; `on_proposal` fires after each scored
    proposal. A caller that wants interruption-resume to lose no receipt passes both.
    """
    budget = [max_attempts]
    started = time.monotonic()
    deadline = started + max_seconds
    outcomes: list[Outcome] = []
    stopped = "items exhausted"
    for item in items:
        if budget[0] <= 0:
            stopped = "attempt budget"
            break
        if time.monotonic() >= deadline:
            stopped = "time budget"
            break
        if factory.should_stop():
            stopped = "paused"
            break
        outcome = factory.run_function(item, budget, deadline=deadline,
                                       on_proposal=on_proposal)
        outcomes.append(outcome)
        if checkpoint:
            checkpoint({"func": outcome.func, "attempts": outcome.attempts,
                        "admitted": outcome.admitted, "improving": outcome.improving,
                        "improving_children": outcome.improving_children,
                        "repair_requests": outcome.repair_requests,
                        "independent_requests": outcome.independent_requests,
                        "errors": outcome.errors, "refusals": outcome.refusals,
                        "no_extract": outcome.no_extract,
                        "best_before": outcome.best_before, "best_after": outcome.best_after,
                        "exact": outcome.exact, "stopped": outcome.stopped, "game": factory.game})
        if outcome.stopped == "time budget":
            stopped = "time budget"
            break
        if outcome.stopped == "paused":
            stopped = "paused"
            break
    return {
        "game": factory.game, "compiler": factory.compiler,
        "functions": len(outcomes), "attempts": sum(o.attempts for o in outcomes),
        "admitted": sum(o.admitted for o in outcomes),
        "refusals": sum(o.refusals for o in outcomes),
        "errors": sum(o.errors for o in outcomes),
        "no_extract": sum(o.no_extract for o in outcomes),
        "improving_rounds": sum(o.improving for o in outcomes),
        "improving_children": sum(o.improving_children for o in outcomes),
        "repair_requests": sum(o.repair_requests for o in outcomes),
        "independent_requests": sum(o.independent_requests for o in outcomes),
        "functions_improved": sum(1 for o in outcomes if o.improved),
        "exact": sum(1 for o in outcomes if o.exact),
        "gain": round(sum(o.best_after - o.best_before for o in outcomes), 3),
        "stopped": stopped, "seconds": round(time.monotonic() - started, 1),
        "outcomes": [{"func": o.func, "attempts": o.attempts, "admitted": o.admitted,
                      "improving": o.improving, "improving_children": o.improving_children,
                      "repair_requests": o.repair_requests,
                      "independent_requests": o.independent_requests,
                      "errors": o.errors, "refusals": o.refusals,
                      "before": o.best_before, "after": o.best_after,
                      "distinct_prompts": o.distinct_prompts,
                      "exact": o.exact, "stopped": o.stopped,
                      "stopping_note": o.stopping_note} for o in outcomes],
    }


def yield_summary(report: dict) -> dict:
    """The two numbers that size a campaign, kept apart because they have different cures.

    A single `improving / attempts` conflates two populations and hides which one is failing. A
    candidate the build REFUSES before IDO runs is not a worse move -- it is not a move in the space
    the repair machinery can act on at all, and averaging it in is this project's silent-decline
    failure applied to a metric instead of a pass. The first pilot reported
    `improving_edges_per_attempt: 0.0` on five attempts of which zero reached the oracle; that read
    like a measurement and was not one.

        P(compiles)              the admission gate. Its cure is deterministic.
        P(improves | compiles)   the actual yield. Only here is model quality the question.

    When nothing was admitted, the yield is `None`, not 0.0: unknown is not zero.
    """
    attempts = report.get("attempts", 0)
    admitted = report.get("admitted", 0)
    # `improving_children` is the dataset's size and `improving_rounds` is the old,
    # smaller number. Both are reported; the yield rate uses the child count, because
    # a rate over round-winners understates supply and sizes a campaign wrongly.
    improving = report.get("improving_children", report.get("improving_rounds", 0))
    refusals = report.get("refusals", 0)
    errors = report.get("errors", 0)
    enough = admitted >= MIN_ADMITTED_FOR_A_RATE
    if refusals and not attempts and not admitted:
        note = (f"{refusals} refusal(s) and nothing else: the model declined this target, which is "
                f"a target-selection problem, not a candidate problem. Choose another function.")
    elif enough:
        note = ""
    else:
        note = (f"{admitted} admitted candidate(s); a rate needs about "
                f"{MIN_ADMITTED_FOR_A_RATE} before a zero or a one means anything. "
                f"At the historical 6% base rate that is roughly "
                f"{round(MIN_ADMITTED_FOR_A_RATE / 0.06)} admitted candidates, and at the measured "
                f"compile rate that is the number of attempts to budget.")
    return {
        "attempts": attempts,
        "admitted": admitted,
        "refusals": refusals,
        "errors": errors,
        "no_extract": report.get("no_extract", 0),
        "P_compiles": round(admitted / attempts, 4) if attempts else None,
        "improving_rounds": report.get("improving_rounds", 0),
        "improving_children": report.get("improving_children", 0),
        "repair_requests": report.get("repair_requests", 0),
        "independent_requests": report.get("independent_requests", 0),
        "P_improves_given_compiles": round(improving / admitted, 4) if admitted else None,
        "improving_edges_per_attempt": round(improving / attempts, 4) if attempts else None,
        "usable_examples_per_100_calls": round(100.0 * improving / attempts, 2) if attempts else None,
        "powered": enough,
        "note": note,
        "functions_touched": report.get("functions", 0),
        "functions_improved_share": round(
            report.get("functions_improved", 0) / max(1, report.get("functions", 1)), 4),
        "mean_gain_per_function": round(report.get("gain", 0.0) / max(1, report.get("functions", 1)), 3),
    }


MIN_ADMITTED_FOR_A_RATE = 20


# --- adapters -----------------------------------------------------------------

class OllamaGenerator:
    """The real proposer, using the project's own prompt and extraction.

    `extract_c` returns the whole response when the model writes no function at all -- which is right
    for a caller that wants to see the text, and wrong here: the first version of this factory handed
    that prose to the compiler and booked 19,791 characters of reasoning as a model error. A proposal
    with no function definition is an EXTRACTION failure and is dropped, which is the distinction
    `llm.extract_c` exists to make possible.
    """

    def __init__(self, model: str = "gpt-oss:20b", endpoint: str | None = None,
                 think: str = "low", num_thread: int = 12, prefill: str | None = None):
        from solver import llm, pipeline
        self.llm = llm
        self.model = model
        self.endpoint = endpoint or llm.host()
        # `think="low"` and `num_thread=12` are `solver/refine.main`'s defaults. Passing think=""
        # instead made gpt-oss emit its reasoning in the RESPONSE channel: extract_c found no fence,
        # found a `foo() {`-shaped fragment inside the trace, and returned 19-22 KB of prose that the
        # compiler reported as "Unterminated string or character constant".
        self.think = think
        self.num_thread = num_thread
        # PREFILL, and it is not optional. `solver/llm.py:112-125` measures a partial ASSISTANT turn
        # stopping refusals outright -- 9/9 -> 0/9 on functions that refuse every draw -- and it only
        # works through `/api/chat`; the same text appended to `/api/generate` does nothing (18/18
        # still refused). This class was the ONE call site in the repo that omitted it, while
        # `solver/pipeline.py:280` and `:456` and every pilot passed it. Measured 2026-09-16: it
        # produced 4 refusals and 1 no-extract from a well-formed prompt, against 0 refusals in 2,197
        # historical gpt-oss attempts. Checking `is_refusal` after the fact cannot substitute for
        # never asking the question in the refusing way.
        self.prefill = pipeline.PREFILL if prefill is None else prefill
        self.dropped = 0
        self.refusals = 0
        self.errors: list[str] = []
        self.raw_heads: list[str] = []
        self.receipts: list = []
        self._digest = ""

    def model_digest(self) -> str:
        """The digest of the artifact actually served, fetched once.

        A model NAME is not an identity: `qwen2.5-coder:14b` can be re-pulled and the tag
        move. The experiment's M0 and M1 claims rest on which weights produced a token, so
        the digest is recorded in the run configuration and on every receipt.
        """
        if self._digest:
            return self._digest
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self.endpoint}/api/tags", timeout=20) as resp:
                tags = json.loads(resp.read())
            for entry in tags.get("models", []):
                if entry.get("name") == self.model or entry.get("model") == self.model:
                    self._digest = str(entry.get("digest") or "")
                    break
        except Exception:
            self._digest = ""
        return self._digest

    def draw(self, prompt: str, n: int, temperature: float, *,
             role: str = "independent", deadline: float | None = None) -> list[Proposal]:
        """N calls, each returned as a `Proposal` WITH its receipt -- including failures.

        Every call is attempted even if an earlier one failed, and a failed call yields a
        Proposal carrying the error rather than being dropped. The old `sample` caught the
        exception, appended a string, and `break`-ed -- so one transient HTTP error ended
        the round and the round's remaining budget was silently spent on nothing.
        """
        out: list[Proposal] = []
        digest = self.model_digest()
        for index in range(n):
            if deadline is not None and time.monotonic() >= deadline:
                out.append(Proposal(prompt=prompt, action=role, error="timeout-deadline"))
                break
            t0 = time.time()
            text, meta, error = "", {}, ""
            try:
                text, meta = self.llm.generate(self.endpoint, self.model, prompt,
                                                temperature=temperature, timeout=900,
                                                think=self.think, num_thread=self.num_thread,
                                                prefill=self.prefill)
            except Exception as exc:
                # Recorded, never swallowed: a generator that returns [] because every call
                # raised is indistinguishable from one that returned nothing to say, and the
                # first run of this size reported "no extractable C" in 2.8 seconds with no
                # reason attached.
                error = f"{type(exc).__name__}: {exc}"
                self.errors.append(error)
            wall_ms = int((time.time() - t0) * 1000)
            self.raw_heads.append((text or "")[:200])
            # A refusal is its own population with its own cure. Admission is fixed by
            # lowering, yield is fixed by model quality, and a refusal is fixed by choosing
            # another target -- a model asked to reconstruct one function of a commercial ROM
            # will sometimes decline, and counting that as a bad candidate would send the next
            # hour after the wrong problem.
            code = ""
            if not error and not self.llm.is_refusal(text or ""):
                candidate = self.llm.extract_c(text or "")
                if candidate and self.llm.FUNC_DEF_RE.search(candidate):
                    code = candidate
            receipt = self.llm.generation_receipt(
                text, meta, prompt=prompt, model=self.model, extracted=code,
                sampling={"temperature": temperature, "role": role, "think": self.think,
                          "num_thread": self.num_thread, "prefill": bool(self.prefill),
                          "draw_index": index},
                wall_ms=wall_ms, error=error)
            if not receipt.digest and digest:
                receipt.digest = digest
            if receipt.status == "refusal":
                self.refusals += 1
            elif receipt.status in ("no-extract", "empty"):
                self.dropped += 1
            self.receipts.append(receipt)
            out.append(Proposal(source=code, prompt=prompt, action=role,
                                receipt=receipt, error=error))
        return out

    def sample(self, prompt: str, n: int, temperature: float) -> list[str]:
        """Backward-compatible view returning only the extracted C.

        Kept because `solver/refine.py` and older callers use the string form; the factory
        no longer does, because the string form is how the receipts were lost.
        """
        return [p.source for p in self.draw(prompt, n, temperature) if p.source]


class WorkspaceScorer:
    """The real decider: the project's own per-function workspace and object comparison.

    `workspace.score` is passed `parent_attempt_id`, so every candidate is linked to the
    attempt it was refining -- and only when a repair was actually requested. An
    independent draw has no parent and records none.

    Every field of the generation receipt reaches durable storage: the full prompt, the raw
    response, the model and its digest, the sampling parameters, token cost, wall time,
    extraction status and stop reason. `record_attempt` already supported all of these; the
    old scorer simply passed none of them, which is why 29 factory attempts in the KB have
    no prompt and no model.
    """

    def __init__(self, repo: Path, kb: Path, strategy: str = "factory-refine",
                 run_id: str = ""):
        self.repo, self.kb, self.strategy = repo, kb, strategy
        self.run_id = run_id
        self.conn = sqlite3.connect(kb)
        self.workspaces: dict[str, Path] = {}
        self._signals = None
        self._iteration = 0

    def workspace(self, func: str) -> Path:
        if func not in self.workspaces:
            from solver import workspace as ws_mod
            self.workspaces[func] = ws_mod.bootstrap(self.repo, func)
        return self.workspaces[func]

    def score(self, func: str, proposal, parent_attempt_id: int | None = None) -> dict:
        """Score one proposal. Accepts a `Proposal` or a bare source string.

        The string form is retained for adapters that predate the receipt contract; it
        records a root-lineage attempt with no prompt, which is now a visible gap rather
        than the default.
        """
        from solver import signals, workspace as ws_mod
        if isinstance(proposal, str):
            proposal = Proposal(source=proposal)
        parent = (proposal.parent.attempt_id if proposal.parent is not None
                  else parent_attempt_id)
        relation = "refine" if parent is not None else ""
        prompt = proposal.prompt or ""
        receipt = proposal.receipt
        self._iteration += 1
        ws = self.workspace(func)
        att = ws_mod.score(
            ws, self.repo, func, proposal.source, conn=self.conn, func=func,
            strategy=self.strategy, parent_attempt_id=parent, relation=relation,
            model=(receipt.model if receipt is not None else ""),
            prompt=prompt,
            raw_response=(receipt.raw_response if receipt is not None else ""),
            extract_status=(receipt.extract_status if receipt is not None else ""),
            done_reason=(receipt.done_reason if receipt is not None else ""),
            token_cost=(receipt.token_cost if receipt is not None else 0),
            wall_ms=(receipt.wall_ms if receipt is not None else 0),
            iteration=self._iteration,
            run_id=(self.run_id or ""),
            generation=receipt,
            action=proposal.action, feedback=(proposal.parent.feedback()
                                              if proposal.parent is not None else ""),
            run_kind=self.strategy,
            run_config={"strategy": self.strategy, "game": "sbk1"})
        verdict = signals.analyse(att.diff or "", att.score, att.exact, att.compiled)
        return {"compiled": att.compiled, "score": att.score, "exact": att.exact,
                "diff": att.diff, "source": proposal.source, "attempt_id": att.receipt_id,
                "compiler_stderr": att.compiler_stderr,
                "prompt": prompt,
                "prompt_sha256": (hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                                  if prompt else None),
                "status": proposal.status,
                "faults": {axis: int(getattr(verdict, axis)) for axis in AXES}}


def make_context(spec: GameSpec):
    """The project's own prompt, assembled per function from the workspace.

    `solver/refine.FIRST_PROMPT` is reused verbatim rather than paraphrased. It states the C89 rule,
    the `do` ban with the sanctioned `for(;;)` form, the "write source not registers" rules that came
    out of confirmed IDO behaviour, and the two-stores-two-statements rule -- all of it earned across
    tens of thousands of attempts. A factory with its own prompt is a factory that has to relearn
    them, and the first one produced prose.
    """
    from solver import workspace as ws_mod
    from solver.refine import FIRST_PROMPT, hints_for_asm
    cache: dict[str, dict] = {}

    def context_for(func: str) -> dict:
        if func not in cache:
            ws = ws_mod.bootstrap(spec.repo, func)
            asm = ws_mod.target_asm(ws, func)
            draft = ws_mod.m2c_draft(ws)
            hints = hints_for_asm(asm)
            prompt = FIRST_PROMPT.format(asm=asm, draft=draft, kb="", hints=hints)
            # The project's own guard, reused: a prompt carrying the reference source would
            # contaminate every trajectory generated from it, and that is not recoverable later.
            ws_mod.assert_uncontaminated(prompt, spec.repo, func)
            # `asm` and `hints` are carried out, not just used: a REPAIR prompt needs the
            # target assembly too, and re-deriving it per repair would re-read the workspace
            # once per draw. The context is the one place both prompts are assembled.
            cache[func] = {"asm": asm, "draft": draft, "hints": hints, "prompt": prompt}
        return cache[func]
    return context_for


# --- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", default="sbk1", choices=sorted(GAMES))
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--functions", type=int, default=10)
    ap.add_argument("--function", default=None,
                    help="one function by name, for a run big enough to mean something")
    ap.add_argument("--objective", choices=["learn", "finish"], default="learn",
                    help="learn = room to move and a clean owned fault class; "
                         "finish = the completion campaign's tractability ranking")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--max-attempts", type=int, default=100)
    ap.add_argument("--max-seconds", type=int, default=3600)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run-id", default="",
                    help="groups attempt receipts; defaults to a timestamped model-tagged id")
    ap.add_argument("--endpoint", default=None, help="override the inference endpoint")
    ap.add_argument("--plan", action="store_true", help="print the ranked work list and stop")
    args = ap.parse_args(argv)

    spec = GAMES[args.game]
    if args.plan:
        sealed = sealed_functions()
        items = candidates(spec, args.functions, sealed=sealed, objective=args.objective,
                           only=args.function)
        print(json.dumps({"game": spec.name, "ready": spec.ready, "objective": args.objective,
                          "sealed_excluded": len(sealed), "work": items}, indent=2))
        return 0
    require_ready(spec)
    sealed = sealed_functions()
    items = candidates(spec, args.functions, sealed=sealed, objective=args.objective,
                       only=args.function)
    if not items:
        raise SystemExit("no work items: every candidate is solved, unsealed-free or unclassifiable")

    state = Path(str(args.out) + ".state")
    # Resume is by received PROPOSAL, not by finished function. A run interrupted mid-function
    # used to be restarted from the whole function, so its model calls were paid for twice --
    # and the checkpoint fired once per function, so an interruption lost the last one whole.
    proposals_path = Path(str(args.out) + ".proposals.jsonl")
    done_functions: set[str] = set()
    done_prompts: set[str] = set()
    if state.exists():
        done_functions = {json.loads(line)["func"]
                          for line in state.read_text().splitlines() if line.strip()}
    if proposals_path.exists():
        for line in proposals_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("prompt_sha256") and row.get("action") == "repair":
                    done_prompts.add(row["prompt_sha256"])
    todo = [item for item in items if item["name"] not in done_functions]
    if not todo:
        raise SystemExit(f"all {len(items)} work items already done; delete {state} to redo them")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or f"factory-{time.strftime('%Y%m%d-%H%M%S')}-{args.model.replace(':', '-')}"
    generator = OllamaGenerator(args.model, endpoint=args.endpoint)
    factory = Factory(generator=generator,
                      scorer=WorkspaceScorer(spec.repo, spec.kb, run_id=run_id),
                      context_for=make_context(spec), rounds=args.rounds, samples=args.samples,
                      temperature=args.temperature, game=spec.name, compiler=spec.compiler)

    def checkpoint(row: dict) -> None:
        with state.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    def on_proposal(row: dict) -> None:
        """Durable after EVERY scored proposal, so an interruption costs one candidate."""
        with proposals_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(json.dumps({"proposal": row}), flush=True)

    started = time.time()
    report = run(factory, todo, max_attempts=args.max_attempts,
                 max_seconds=args.max_seconds, checkpoint=checkpoint,
                 on_proposal=on_proposal)
    report["yield"] = yield_summary(report)
    report["messages"] = factory.log[-20:]
    report["run_id"] = run_id
    # The digest of the artifact that actually served the run. A tag can move; this cannot.
    report["model"] = generator.model
    report["model_digest"] = generator.model_digest()
    report["receipts"] = len(generator.receipts)
    report["resumed_functions_skipped"] = len(done_functions)
    report["wall_seconds"] = round(time.time() - started, 1)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"yield": report["yield"], "stopped": report["stopped"],
                      "model_digest": report["model_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
