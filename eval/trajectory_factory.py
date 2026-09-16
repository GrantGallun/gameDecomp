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
    """Function names in any frozen eval set. Never generated against, whatever the budget says."""
    directory = sets_dir or ROOT / "eval" / "sets"
    sealed: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("dev", "heldout"):
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


# --- the loop -----------------------------------------------------------------

class Generator(Protocol):
    def sample(self, prompt: str, n: int, temperature: float) -> list[str]: ...


class Scorer(Protocol):
    """Returns (compiled, score, exact, diff) and must record the attempt with its parent."""
    def score(self, func: str, source: str, parent_attempt_id: int | None) -> dict: ...


@dataclass
class Outcome:
    func: str
    attempts: int = 0
    admitted: int = 0            # reached the oracle: the build did not refuse it
    refusals: int = 0            # the model declined the target outright
    improving: int = 0
    best_before: float = 0.0
    best_after: float = 0.0
    stopping_note: str = ""
    exact: bool = False
    stopped: str = ""

    @property
    def improved(self) -> bool:
        return self.best_after > self.best_before + 1e-9


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
    normalizer: Callable[[str], tuple[str, list[str]]] = normalize
    state_path: Path | None = None
    log: list[dict] = field(default_factory=list)

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

    def run_function(self, item: dict, budget: list[int]) -> Outcome:
        """One function's trajectory: best-of-N over the project's own prompt.

        `budget` is a one-element list so a run can stop mid-function without losing the work.
        """
        outcome = Outcome(item["name"], best_before=item["best_score"], best_after=item["best_score"])
        context = self.context_for(item["name"])
        prompt = context["prompt"]
        parent = item.get("best_attempt_id")
        for _round in range(self.rounds):
            if budget[0] <= 0:
                outcome.stopped = "attempt budget"
                break
            wanted = min(self.samples, budget[0])
            before_refusals = getattr(self.generator, "refusals", 0)
            proposals = self.generator.sample(prompt, wanted, self.temperature)
            outcome.refusals += getattr(self.generator, "refusals", 0) - before_refusals
            if not proposals:
                # No extractable C at all is an extraction finding, not a model failure, and it is
                # the failure mode that made the first version of this module produce prose. The
                # generator's own reason is carried out with it.
                outcome.stopped = "no extractable C"
                outcome.stopping_note = self.generator_detail()
                break
            scored = []
            for source in proposals:
                if budget[0] <= 0:
                    break
                budget[0] -= 1
                outcome.attempts += 1
                lowered, applied = self.normalizer(source)
                result = self.scorer.score(item["name"], lowered, parent)
                result["normalized"] = applied
                scored.append(result)
                if result.get("compiled"):
                    outcome.admitted += 1
                self.log.append({"func": item["name"], "round": _round,
                                 "game": self.game, "compiler": self.compiler,
                                 "compiled": result.get("compiled"),
                                 "score": result.get("score"), "exact": result.get("exact"),
                                 "faults": result.get("faults"), "normalized": applied})
            if not scored:
                break
            best = max(scored, key=lambda r: (bool(r.get("exact")), r.get("score") or 0.0))
            if (best.get("score") or 0.0) > outcome.best_after + self.improvement_epsilon:
                outcome.best_after = best["score"]
                outcome.improving += 1
                if best.get("attempt_id") is not None:
                    parent = best["attempt_id"]
            if best.get("exact"):
                outcome.exact = True
                outcome.stopped = "exact"
                break
        return outcome


def run(factory: Factory, items: Sequence[dict], *, max_attempts: int = 100,
        max_seconds: int = 3600, checkpoint: Callable[[dict], None] | None = None) -> dict:
    """Drive the factory over work items under both budgets, checkpointing after each function."""
    budget = [max_attempts]
    started = time.time()
    outcomes: list[Outcome] = []
    stopped = "items exhausted"
    for item in items:
        if budget[0] <= 0:
            stopped = "attempt budget"
            break
        if time.time() - started >= max_seconds:
            stopped = "time budget"
            break
        outcome = factory.run_function(item, budget)
        outcomes.append(outcome)
        if checkpoint:
            checkpoint({"func": outcome.func, "attempts": outcome.attempts,
                        "admitted": outcome.admitted, "improving": outcome.improving,
                        "best_before": outcome.best_before, "best_after": outcome.best_after,
                        "exact": outcome.exact, "stopped": outcome.stopped, "game": factory.game})
    improving = sum(o.improving for o in outcomes)
    return {
        "game": factory.game, "compiler": factory.compiler,
        "functions": len(outcomes), "attempts": sum(o.attempts for o in outcomes),
        "admitted": sum(o.admitted for o in outcomes),
        "refusals": sum(o.refusals for o in outcomes),
        "improving_rounds": improving,
        "functions_improved": sum(1 for o in outcomes if o.improved),
        "exact": sum(1 for o in outcomes if o.exact),
        "gain": round(sum(o.best_after - o.best_before for o in outcomes), 3),
        "stopped": stopped, "seconds": round(time.time() - started, 1),
        "outcomes": [{"func": o.func, "attempts": o.attempts, "admitted": o.admitted,
                      "improving": o.improving, "before": o.best_before, "after": o.best_after,
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
    improving = report.get("improving_rounds", 0)
    refusals = report.get("refusals", 0)
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
        "P_compiles": round(admitted / attempts, 4) if attempts else None,
        "improving_rounds": improving,
        "P_improves_given_compiles": round(improving / admitted, 4) if admitted else None,
        "improving_edges_per_attempt": round(improving / attempts, 4) if attempts else None,
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

    def sample(self, prompt: str, n: int, temperature: float) -> list[str]:
        out = []
        for _ in range(n):
            try:
                text, _meta = self.llm.generate(self.endpoint, self.model, prompt,
                                                temperature=temperature, timeout=900,
                                                think=self.think, num_thread=self.num_thread,
                                                prefill=self.prefill)
            except Exception as exc:
                # Recorded, never swallowed: a generator that returns [] because every call raised
                # is indistinguishable from one that returned nothing to say, and the first run of
                # this size reported "no extractable C" in 2.8 seconds with no reason attached.
                self.errors.append(f"{type(exc).__name__}: {exc}")
                break
            self.raw_heads.append((text or "")[:200])
            # A refusal is its own population with its own cure. Admission is fixed by lowering,
            # yield is fixed by model quality, and a refusal is fixed by choosing another target --
            # a model asked to reconstruct one function of a commercial ROM will sometimes decline,
            # and counting that as a bad candidate would send the next hour after the wrong problem.
            if self.llm.is_refusal(text or ""):
                self.refusals += 1
                continue
            code = self.llm.extract_c(text or "")
            if not code or not self.llm.FUNC_DEF_RE.search(code):
                self.dropped += 1
                continue
            out.append(code)
        return out


class WorkspaceScorer:
    """The real decider: the project's own per-function workspace and object comparison.

    `workspace.score` is passed `parent_attempt_id`, so every candidate is linked to the attempt it
    was refining. That link is the trajectory; without it the factory would produce attempts and no
    edges, which is the state the knowledge base is already in.
    """

    def __init__(self, repo: Path, kb: Path, strategy: str = "factory-refine"):
        self.repo, self.kb, self.strategy = repo, kb, strategy
        self.conn = sqlite3.connect(kb)
        self.workspaces: dict[str, Path] = {}
        self._signals = None

    def workspace(self, func: str) -> Path:
        if func not in self.workspaces:
            from solver import workspace as ws_mod
            self.workspaces[func] = ws_mod.bootstrap(self.repo, func)
        return self.workspaces[func]

    def score(self, func: str, source: str, parent_attempt_id: int | None) -> dict:
        from solver import signals, workspace as ws_mod
        ws = self.workspace(func)
        att = ws_mod.score(ws, self.repo, func, source, conn=self.conn, func=func,
                           strategy=self.strategy, parent_attempt_id=parent_attempt_id,
                           relation="refine")
        verdict = signals.analyse(att.diff or "", att.score, att.exact, att.compiled)
        return {"compiled": att.compiled, "score": att.score, "exact": att.exact,
                "diff": att.diff, "source": source, "attempt_id": att.receipt_id,
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
            prompt = FIRST_PROMPT.format(asm=asm, draft=draft, kb="", hints=hints_for_asm(asm))
            # The project's own guard, reused: a prompt carrying the reference source would
            # contaminate every trajectory generated from it, and that is not recoverable later.
            ws_mod.assert_uncontaminated(prompt, spec.repo, func)
            cache[func] = {"asm": asm, "draft": draft, "prompt": prompt}
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
    seen = {json.loads(line)["func"] for line in state.read_text().splitlines()} if state.exists() else set()
    todo = [item for item in items if item["name"] not in seen]
    if not todo:
        raise SystemExit(f"all {len(items)} work items already done; delete {state} to redo them")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    factory = Factory(generator=OllamaGenerator(args.model),
                      scorer=WorkspaceScorer(spec.repo, spec.kb),
                      context_for=make_context(spec), rounds=args.rounds, samples=args.samples,
                      temperature=args.temperature, game=spec.name, compiler=spec.compiler)

    def checkpoint(row: dict) -> None:
        with state.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    report = run(factory, todo, max_attempts=args.max_attempts,
                 max_seconds=args.max_seconds, checkpoint=checkpoint)
    report["yield"] = yield_summary(report)
    report["messages"] = factory.log[-20:]
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"yield": report["yield"], "stopped": report["stopped"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
