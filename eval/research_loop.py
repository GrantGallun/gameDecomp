"""Adaptive research search over decompilation attempts.

The loop this implements, and why it is shaped this way.

A model proposes C, the compiler and objdiff decide, and the verdict is a number plus a *kind* of
error (`solver/signals.py`). That makes this domain one of the few where a self-improving loop can be
studied honestly, because the hardest problem in the design literature -- "where does truth enter the
loop" -- is already answered by the binary. Nothing here asks a model to judge anything.

What the loop does NOT do: it does not start at the weights. The recorded evidence says the pipeline
always continues from the best candidate so far, and that of 4,412 score-lowering edits only 269 were
ever built on -- exactly the hard pruning the design warns about. So the first artifact is the
decision layer: where does the next unit of compute go?

    world        the target's real codegen behaviour, observed only through the compiler
    observation  (score, fault profile) for a candidate
    belief       a forest of branches; a node's value is its best DESCENDANT, not itself
    meta         plateau typing, then a controller that picks scale and branch
    memory       seed lenses with learned weights, updated from realized value

Two modes:

  replay   Off-policy evaluation over the logged forest in the knowledge base. Every policy faces the
           same recorded expansions under the same budget, so the comparison is between ALLOCATIONS
           of a fixed set of experiments, not between different models. This is the cheap experiment
           to run before spending GPU hours, and it uses data that already exists.

  live     The forward loop: the controller picks a branch and an action, a generator proposes
           candidates, the oracle scores them, the belief and the seed weights update. `LiveLoop`
           holds the adapters; tests drive it with a fake generator so the loop logic is exercised
           without a model.

Honest limits, stated up front:

- Replay can only reallocate expansions that were LOGGED. A policy that would have expanded a node
  nobody ever expanded gets nothing there, so replay measures allocation efficiency, not the value of
  branches that were never born. The live loop is the only way to answer that.
- The controller's weights are hand-set. The design's point is that they should eventually be learned
  from realized value; `SeedBook.credit` already updates by outcome, and it PERSISTS across episodes,
  which is the "learn research taste without changing weights" mechanism.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, Sequence

from solver import signals

# The axes a residual is classified on. `offset` and `width` roll into `layout` because that is how
# signals reports them, and `instr_delta` is kept out: it is signed and says whether the candidate is
# missing or has extra instructions, not how many faults it has.
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")
DEFAULT_BUDGET = 24

# Bumped whenever the stopping rule changes. A stored verdict is only as good as the rule it was
# computed under, so consumers compare this before reusing one. Version 1 tested `adaptive` against
# `fixed-seed` and `random` on discovery only -- a rule under which the Sept 16 replay passed while
# `adaptive` and `adaptive-nolearn` scored identically on both metrics.
GATE_RULE_VERSION = 2
PLATEAU_WINDOW = 4
PLATEAU_EPSILON = 0.5          # a score gain below this is not a gain
PLATEAU_TAU = 1.5              # L1 distance below this means "the same residual"
EXPLORE_FLOOR = 0.15           # never hard-prune: this share of picks is deliberately off-policy
PARETO_POOL = 40               # bound on the dominance comparison; see ParetoPolicy.choose

_VERDICTS: dict[int, signals.Signals] = {}


# --- observations -------------------------------------------------------------

def verdict_of(attempt_id: int, diff: str | None, score: float, exact: bool, compiled: bool):
    """Classify a residual once and cache it: this is the loop's only reading of the world."""
    cached = _VERDICTS.get(attempt_id)
    if cached is None:
        cached = signals.analyse(diff or "", score or 0.0, bool(exact), bool(compiled))
        _VERDICTS[attempt_id] = cached
    return cached


def profile_of(diff: str | None, score: float, exact: bool, compiled: bool,
               attempt_id: int | None = None) -> tuple[int, ...]:
    """The fault vector for one candidate, on `AXES`."""
    if attempt_id is not None:
        verdict = verdict_of(attempt_id, diff, score, exact, compiled)
    else:
        verdict = signals.analyse(diff or "", score or 0.0, bool(exact), bool(compiled))
    return tuple(int(getattr(verdict, axis)) for axis in AXES)


def fault_total(profile: Sequence[int]) -> int:
    return sum(profile)


@dataclass(frozen=True)
class Attempt:
    """One logged candidate. This is the only kind of evidence the loop is allowed to read."""
    id: int
    func: int
    parent: int | None
    iteration: int
    score: float
    exact: bool
    compiled: bool
    strategy: str | None
    model: str | None
    profile: tuple[int, ...]

    @property
    def faults(self) -> int:
        return fault_total(self.profile)

    @property
    def tractability(self) -> float:
        """Share of the residual that some implemented pass owns.

        The project's own triage idea, used here as the loop's estimate of UPSIDE: a candidate at 70%
        whose residual is two repairable faults can still reach 100, while one at 96% whose residual
        is thirty structural faults probably cannot. Without this term the controller climbs toward
        the plateau the project has already identified as a dead end.
        """
        verdict = _VERDICTS.get(self.id)
        if verdict is None or not verdict.compiled:
            return 0.0
        owned = verdict.repairable + verdict.conditional_repair
        total = owned + verdict.no_repair_implemented + verdict.unrepairable
        return owned / total if total else 0.0


@dataclass
class Forest:
    """Every logged attempt for one function, as a tree.

    `values` caches the delayed-credit value of every node. It is filled once per forest: computing
    it per candidate instead made the controller quadratic in subtree size, and a 31k-attempt replay
    never finished.
    """
    func: int
    attempts: dict[int, Attempt]
    children: dict[int, tuple[int, ...]]
    roots: tuple[int, ...]
    values: dict[int, tuple[float, bool]] = field(default_factory=dict)

    @property
    def best_score(self) -> float:
        scores = [a.score for a in self.attempts.values() if a.compiled]
        return max(scores) if scores else 0.0

    @property
    def solved(self) -> bool:
        return any(a.exact for a in self.attempts.values())

    def value(self, node: int) -> float:
        return self.best_of(node)[0]

    def best_of(self, node: int) -> tuple[float, bool]:
        if not self.values:
            for root in self.roots:
                subtree_value(self, root, self.values)
        here = self.attempts[node]
        return self.values.get(node, (here.score, here.exact))


def load_forests(conn: sqlite3.Connection, limit: int | None = None,
                 min_attempts: int = 3) -> dict[int, Forest]:
    """Read the logged attempts into one forest per function.

    The function selection happens in SQL first. Parsing every `diff_summary` in the knowledge base
    costs minutes and the study only reads a few hundred forests, so the query narrows to those
    before any residual is classified.
    """
    params: tuple = ()
    where = ""
    if limit is not None:
        addresses = [row[0] for row in conn.execute(
            "select func_addr from attempts group by func_addr having count(*) >= ? "
            "order by count(*) desc, func_addr limit ?", (min_attempts, limit))]
        if not addresses:
            return {}
        where = f"where func_addr in ({','.join('?' * len(addresses))})"
        params = tuple(addresses)
    rows = conn.execute(
        "select id, func_addr, parent_attempt_id, iteration, coalesce(score,0), coalesce(exact,0), "
        f"compiled, strategy, model, diff_summary from attempts {where} "
        "order by func_addr, iteration, id", params).fetchall()
    grouped: dict[int, list[Attempt]] = collections.defaultdict(list)
    for rid, func, parent, iteration, score, exact, compiled, strategy, model, diff in rows:
        profile = profile_of(diff, score, exact, compiled, attempt_id=rid)
        grouped[func].append(Attempt(rid, func, parent, iteration, score, bool(exact),
                                     bool(compiled), strategy, model, profile))
    forests: dict[int, Forest] = {}
    for func, attempts in grouped.items():
        if len(attempts) < min_attempts:
            continue
        known = {a.id for a in attempts}
        children: dict[int, list[int]] = collections.defaultdict(list)
        roots = []
        for a in attempts:
            # A parent outside this function, or none, starts a root: the forest is per-function.
            if a.parent is None or a.parent not in known:
                roots.append(a.id)
            else:
                children[a.parent].append(a.id)
        forests[func] = Forest(func, {a.id: a for a in attempts},
                               {k: tuple(sorted(v)) for k, v in children.items()},
                               tuple(sorted(roots)))
    if limit is not None:
        ordered = sorted(forests, key=lambda f: (-len(forests[f].attempts), f))[:limit]
        forests = {f: forests[f] for f in ordered}
    return forests


def subtree_value(forest: Forest, node: int,
                  memo: dict[int, tuple[float, bool]] | None = None) -> tuple[float, bool]:
    """Best (score, exact) over a node and every logged descendant.

    This is the delayed-credit rule. An edit that LOWERS the score can still be the ancestor of the
    best result, so its value is not its own score. Reading value off the subtree is what makes
    keeping a setback alive rational rather than sentimental.
    """
    memo = {} if memo is None else memo
    if node in memo:
        return memo[node]
    here = forest.attempts[node]
    best = (here.score, here.exact)
    for child in forest.children.get(node, ()):
        score, exact = subtree_value(forest, child, memo)
        if (exact and not best[1]) or (exact == best[1] and score > best[0]):
            best = (score, exact)
    memo[node] = best
    return best


# --- plateau typing -----------------------------------------------------------

class Plateau(Enum):
    """Four plateaus that need different responses.

    The point is that "the score stopped improving" is not one state. Collapsing them into one is
    what makes a searcher either give up on a branch that is about to break through, or pour compute
    into one that is finished.
    """
    NONE = "none"
    EXHAUSTED = "exhausted"            # gains stopped AND the residual stopped changing
    UNRESOLVED = "unresolved"          # gains stopped but the residual keeps moving
    CONVERGENCE = "convergence"        # independent branches land on the same residual
    REPRESENTATION = "representation"  # many attempts share one residual -> the shape is wrong


def _distance(a: Sequence[int], b: Sequence[int]) -> float:
    return float(sum(abs(x - y) for x, y in zip(a, b)))


def classify(nodes: Sequence[Attempt], window: int = PLATEAU_WINDOW,
             epsilon: float = PLATEAU_EPSILON, tau: float = PLATEAU_TAU) -> tuple[Plateau, str]:
    """Which plateau a branch is on, with the evidence for the verdict."""
    if len(nodes) < window + 1:
        return Plateau.NONE, f"only {len(nodes)} attempts, need {window + 1}"
    recent = list(nodes)[-(window + 1):]
    gains = [b.score - a.score for a, b in zip(recent, recent[1:])]
    if any(g > epsilon for g in gains):
        return Plateau.NONE, f"still improving (+{max(gains):.2f} within the window)"
    drift = statistics.mean(_distance(a.profile, b.profile) for a, b in zip(recent, recent[1:]))
    profile, count = collections.Counter(a.profile for a in recent).most_common(1)[0]
    if count >= len(recent) - 1 and drift <= tau:
        if len(nodes) >= 2 * window:
            return Plateau.REPRESENTATION, (
                f"{count}/{len(recent)} attempts share residual {list(profile)} after "
                f"{len(nodes)} tries; the residual is invariant to the edits being made")
        return Plateau.EXHAUSTED, f"{count}/{len(recent)} attempts share residual {list(profile)}"
    if drift > tau:
        return Plateau.UNRESOLVED, f"score flat but the residual keeps moving (drift {drift:.2f})"
    return Plateau.EXHAUSTED, f"score flat, drift {drift:.2f}"


def converging(tips: Sequence[Sequence[int]], tau: float = PLATEAU_TAU) -> bool:
    """True when independent branches have collapsed onto nearly the same residual."""
    live = [t for t in tips if t]
    for i, left in enumerate(live):
        for right in live[i + 1:]:
            if _distance(left, right) <= tau:
                return True
    return False


# --- seeds: the strategy memory -----------------------------------------------

SEED_LENSES: dict[str, dict[str, float]] = {
    "control-flow":      {"structural": 1.0},
    "field-layout":      {"layout": 1.0},
    "statement-order":   {"ordering": 1.0},
    "register-pressure": {"regalloc": 1.0},
    "literal-value":     {"immediate": 1.0},
    "symbol-binding":    {"reloc": 1.0},
    "contradiction":     {},   # adversarial: aims at the axis the others neglect
    "unexplored":        {},   # frontier: aims at the least-attempted axis
}


@dataclass
class SeedBook:
    """Seed weights learned from realized value, not from how good a seed sounds.

    Seeds are not answers; they are directions that change which neighbourhood gets searched. A seed
    that keeps appearing on branches whose descendants improved gains weight; one that keeps
    appearing on dead branches decays. This persists ACROSS episodes -- that persistence is the
    design's "can it learn research taste without changing its weights", and it is the only thing
    here that learns at all.

    `uses` counts EVERY credit and `gains` counts the ones that carried real value, because their
    RATIO is the only statistic that can separate one seed from another: ~99.5% of expansions are
    dead, so summed value is dominated by how often a seed appears rather than by how well it does.
    Keeping the two counters apart is what makes that visible instead of hidden inside a weight.
    """
    weights: dict[str, float] = field(default_factory=lambda: {s: 1.0 for s in SEED_LENSES})
    uses: collections.Counter = field(default_factory=collections.Counter)
    gains: collections.Counter = field(default_factory=collections.Counter)
    value: collections.Counter = field(default_factory=collections.Counter)

    def credit(self, seed: str, realized_gain: float) -> None:
        self.uses[seed] += 1
        self.value[seed] += realized_gain
        if realized_gain > 0:
            self.gains[seed] += 1
        # Exponential update, so a seed is judged by recent history rather than all of it.
        target = 1.0 + max(-0.5, min(2.0, realized_gain / 10.0))
        self.weights[seed] = 0.7 * self.weights[seed] + 0.3 * target

    def gain_rate(self, seed: str) -> float:
        """How often this seed's branch actually improved, out of every time it was credited."""
        return self.gains[seed] / self.uses[seed] if self.uses[seed] else 0.0

    def prefer(self, profile: Sequence[int]) -> str:
        """The seed whose lens is both valuable and pointed at this residual."""
        scored = []
        for name, lens in SEED_LENSES.items():
            if lens:
                focus = sum(weight * profile[AXES.index(axis)] for axis, weight in lens.items())
            elif name == "contradiction":
                focus = max(profile) if profile else 0.0
            else:
                focus = fault_total(profile) / len(AXES)
            scored.append((focus * self.weights[name], name))
        return max(scored)[1]

    def as_dict(self) -> dict:
        return {name: {"weight": round(self.weights[name], 4), "uses": self.uses[name],
                       "gains": self.gains[name], "gain_rate": round(self.gain_rate(name), 5),
                       "value": round(self.value[name], 3)} for name in SEED_LENSES}


# --- policies -----------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """What a policy chose and why, so a run can be explained after the fact."""
    node: int
    reason: str


@dataclass
class SearchState:
    """What every policy is allowed to see. Deliberately small, so policies differ in policy."""
    forest: Forest
    expanded: set[int] = field(default_factory=set)
    revealed: set[int] = field(default_factory=set)
    step: int = 0

    @property
    def best(self) -> Attempt:
        seen = [self.forest.attempts[i] for i in self.revealed]
        return max(seen, key=lambda a: (a.exact, a.score, -a.id))

    def profiles(self) -> list[tuple[int, ...]]:
        return [self.forest.attempts[i].profile for i in self.revealed]


class Policy(Protocol):
    name: str

    def reset(self, forest: Forest) -> None: ...
    def choose(self, frontier: Sequence[int], state: SearchState) -> Decision: ...
    def observe(self, chosen: int, revealed: Sequence[int], state: SearchState) -> None: ...


class Greedy:
    """What the pipeline does today: always continue from the best candidate so far."""
    name = "greedy"

    def reset(self, forest: Forest) -> None:
        pass

    def choose(self, frontier, state) -> Decision:
        best = max(frontier, key=lambda i: (state.forest.attempts[i].exact,
                                            state.forest.attempts[i].score, -i))
        return Decision(best, "highest score in the frontier")

    def observe(self, chosen, revealed, state) -> None:
        pass


class ParetoPolicy:
    """Keep every candidate that is best at anything, and expand those first.

    The project's own survivor rule (`Signals.dominates`) used as a search policy: the cheapest form
    of "don't prune on the score alone".
    """
    name = "pareto"

    def reset(self, forest: Forest) -> None:
        pass

    def choose(self, frontier, state) -> Decision:
        # Dominance is quadratic, so the pool is bounded: the frontier plus the best already-seen
        # candidates. A larger pool would only re-derive survivors that are never expanded.
        seen_best = sorted(state.revealed, key=lambda i: -state.forest.attempts[i].score)[:PARETO_POOL]
        pool = sorted(set(frontier) | set(seen_best))
        verdicts = [(_VERDICTS.get(i), i) for i in pool]
        survivors = []
        for verdict, node in verdicts:
            if verdict is None:
                survivors.append(node)
                continue
            if not any(other is not None and other is not verdict and other.dominates(verdict)
                       for other, _ in verdicts):
                survivors.append(node)
        candidates = [i for i in frontier if i in set(survivors)] or list(frontier)
        best = max(candidates, key=lambda i: (state.forest.attempts[i].score, -i))
        return Decision(best, f"{len(survivors)} Pareto survivors, expanding the best of them")

    def observe(self, chosen, revealed, state) -> None:
        pass


class RandomReseed:
    """Ignore the score and pick uniformly: the control for 'is the policy doing anything'."""
    name = "random"

    def __init__(self, seed: int = 20260916):
        self.rng = random.Random(seed)

    def reset(self, forest: Forest) -> None:
        pass

    def choose(self, frontier, state) -> Decision:
        node = self.rng.choice(sorted(frontier))
        return Decision(node, "uniform reseed")

    def observe(self, chosen, revealed, state) -> None:
        pass


class FixedSeed:
    """Round-robin over the seed lenses, expanding whichever candidate's residual matches.

    This is "fixed diverse seeding": the baseline the controller has to beat before any of this is
    worth training on.
    """
    name = "fixed-seed"

    def __init__(self, order: Sequence[str] | None = None):
        self.order = list(order or SEED_LENSES)
        self.index = 0

    def reset(self, forest: Forest) -> None:
        self.index = 0

    def choose(self, frontier, state) -> Decision:
        seed = self.order[self.index % len(self.order)]
        self.index += 1
        lens = SEED_LENSES[seed]
        if not lens:
            best = max(frontier, key=lambda i: (state.forest.attempts[i].score, -i))
            return Decision(best, f"seed {seed}: no lens, take the best")
        def focus(node: int) -> float:
            profile = state.forest.attempts[node].profile
            return sum(w * profile[AXES.index(axis)] for axis, w in lens.items())
        best = max(frontier, key=lambda i: (focus(i), state.forest.attempts[i].score, -i))
        return Decision(best, f"seed {seed}")

    def observe(self, chosen, revealed, state) -> None:
        pass


class Evolutionary:
    """Best-of-N with an occasional random restart: selection, but no notion of plateau type."""
    name = "evolutionary"

    def __init__(self, patience: int = 3, seed: int = 20260916):
        self.patience = patience
        self.rng = random.Random(seed)
        self.since_gain = 0
        self.last_best = -1.0

    def reset(self, forest: Forest) -> None:
        self.since_gain = 0
        self.last_best = -1.0

    def choose(self, frontier, state) -> Decision:
        if state.best.score > self.last_best + PLATEAU_EPSILON:
            self.last_best = state.best.score
            self.since_gain = 0
        self.since_gain += 1
        if self.since_gain > self.patience:
            self.since_gain = 0
            node = self.rng.choice(sorted(frontier))
            return Decision(node, "restart after patience")
        best = max(frontier, key=lambda i: (state.forest.attempts[i].exact,
                                            state.forest.attempts[i].score, -i))
        return Decision(best, "selection")

    def observe(self, chosen, revealed, state) -> None:
        pass


# Weights for the adaptive priority. Hand-set: the design's point is that these should be learned
# from realized value, and `SeedBook` is the part that already is. W_SEED is what makes the strategy
# memory act rather than decorate: without it the learned seed weights never reach a decision, and
# `adaptive` scores exactly the same as its no-learn control.
W_VALUE, W_UNCERTAIN, W_UPSIDE, W_NOVELTY, W_COST, W_SEED = 0.45, 0.15, 0.25, 0.05, 0.05, 0.15
SEED_WEIGHT_SCALE = 2.0


class Adaptive:
    """The controller: pick where the next unit of compute goes, and at what scale.

    Priority is the design's `P(b) = Q + lU + mO + nN - hC`:

      Q  observed value   -- best DESCENDANT score, not the node's own score
      U  uncertainty      -- how little its neighbourhood has been expanded
      O  option value     -- (headroom) x (share of the residual a pass actually owns)
      N  novelty          -- distance from the residuals already seen
      C  cost             -- what the branch has already consumed

    Each term stops one failure. Without O it climbs toward the 96%-with-thirty-structural-faults
    plateau. Without N it re-tests the same idea. Without U it abandons a branch the moment a sibling
    looks better. No branch is ever deleted.
    """
    name = "adaptive"

    def __init__(self, seed: int = 20260916, floor: float = EXPLORE_FLOOR,
                 seedbook: SeedBook | None = None, learn: bool = True):
        self.rng = random.Random(seed)
        self.base_floor = floor
        self.floor = floor
        self.learn = learn
        self.seedbook = seedbook if seedbook is not None else SeedBook()
        self.reset(None)                     # type: ignore[arg-type]

    def reset(self, forest: Forest | None) -> None:
        self.floor = self.base_floor
        self.branches: dict[int, Branch] = {}
        self.next_branch = 0
        self.plateau = Plateau.NONE
        self.plateau_detail = ""

    # -- branch bookkeeping --

    def branch_of(self, node: int) -> "Branch | None":
        for branch in self.branches.values():
            if node in branch.nodes:
                return branch
        return None

    def open_branch(self, node: int, state: SearchState) -> "Branch":
        seed = self.seedbook.prefer(state.forest.attempts[node].profile)
        branch = Branch(self.next_branch, [node], seed, spawned_at=state.step,
                        by_preference=True)
        self.next_branch += 1
        self.branches[branch.id] = branch
        return branch

    # -- the controller --

    def choose(self, frontier, state: SearchState) -> Decision:
        if self.rng.random() < self.floor:
            node = self.rng.choice(sorted(frontier))
            return Decision(node, "exploration floor")
        seen = state.profiles()
        # Novelty is measured against the MEAN residual rather than against every residual seen:
        # the pairwise form is O(frontier x revealed) per step and dominated the whole replay.
        centre = ([statistics.mean(p[i] for p in seen) for i in range(len(AXES))] if seen else None)
        scored = []
        for node in sorted(frontier):
            attempt = state.forest.attempts[node]
            branch = self.branch_of(node)
            value, _exact = state.forest.best_of(node)
            uncertain = 1.0 / (1.0 + len(state.expanded))
            upside = (1.0 - min(value, 100.0) / 100.0) * attempt.tractability
            novelty = (_distance(attempt.profile, centre) if centre is not None
                       else float(fault_total(attempt.profile)))
            cost = (branch.spent if branch else 0) / max(1, state.step + 1)
            learned = (self.seedbook.weights.get(branch.seed, 1.0) / SEED_WEIGHT_SCALE
                       if branch else 0.5)
            priority = (W_VALUE * (value / 100.0) + W_UNCERTAIN * uncertain
                        + W_UPSIDE * upside + W_NOVELTY * min(novelty / 6.0, 1.0)
                        + W_SEED * learned - W_COST * cost)
            scored.append((priority, -node, node))
        _, _, node = max(scored)
        if self.plateau is Plateau.EXHAUSTED:
            # Stop refining and change the neighbourhood. The floor keeps the old branch reachable, so
            # this raises exploration without destroying the branch that might be one edit from done.
            self.floor = min(0.5, self.floor + 0.1)
        return Decision(node, f"priority {max(scored)[0]:.3f} (plateau {self.plateau.value})")

    def observe(self, chosen: int, revealed: Sequence[int], state: SearchState) -> None:
        branch = self.branch_of(chosen) or self.open_branch(chosen, state)
        before = max((state.forest.attempts[n].score for n in branch.nodes), default=0.0)
        branch.nodes.extend(n for n in revealed if n not in branch.nodes)
        branch.spent += 1 + len(revealed)
        after = max((state.forest.attempts[n].score for n in branch.nodes), default=0.0)
        if after > before + PLATEAU_EPSILON:
            branch.last_gain_at = state.step
            if self.learn and branch.by_preference:
                self.seedbook.credit(branch.seed, after - before)
        elif self.learn and branch.by_preference:
            # THE DOCUMENTED DECAY, which was never wired. `SeedBook`'s docstring says "one that keeps
            # appearing on dead branches decays", but `credit` had exactly ONE call site, inside a
            # GAIN test -- so the argument was always positive, `target` was always > 1.0, weights
            # were monotonically non-decreasing from 1.0, and the `max(-0.5, ...)` branch was
            # unreachable. Crediting 0.0 on a non-gain is the neutral pull the docstring describes:
            # `w <- 0.7w + 0.3*1.0`, so a seed earns above 1.0 only by its RATE of gains.
            self.seedbook.credit(branch.seed, 0.0)
        self.plateau, self.plateau_detail = classify([state.forest.attempts[n] for n in branch.nodes])
        # Convergence is a claim about INDEPENDENT branches. A branch and the tip it was seeded from
        # share a node by construction, so counting them would call every reseed a convergence.
        others = [state.forest.attempts[b.tip].profile for b in self.branches.values()
                  if b.nodes and b.tip != branch.tip]
        if others and converging([state.forest.attempts[branch.tip].profile] + others):
            self.plateau = Plateau.CONVERGENCE
            self.plateau_detail = f"{len(others) + 1} branches share a residual; open a distant seed"
        self._reseed(branch, state)

    def _reseed(self, branch: "Branch", state: SearchState) -> None:
        """Multi-scale reseeding. The parent branch is NOT killed.

        The design's correction to a single "plateau -> jump" rule: keep the branch that might be one
        experiment from breaking through, and open another at a different scale.
        """
        if self.plateau is Plateau.NONE:
            return
        # `by_preference` records WHERE the seed came from. Only a preference-chosen seed can teach
        # the preference anything, so the three rule-assigned cases are marked False and are not
        # credited.
        by_preference = False
        if self.plateau is Plateau.REPRESENTATION:
            seed = "contradiction"
        elif self.plateau is Plateau.CONVERGENCE:
            seed = "unexplored"
        elif self.plateau is Plateau.UNRESOLVED:
            seed = branch.seed                       # keep digging, change the experiment
        else:
            seed = self.seedbook.prefer(state.forest.attempts[branch.tip].profile)
            by_preference = True
        if any(b is not branch and b.seed == seed for b in self.branches.values()):
            return
        fresh = Branch(self.next_branch, [branch.tip], seed, spawned_at=state.step,
                       by_preference=by_preference)
        self.next_branch += 1
        self.branches[fresh.id] = fresh


class AdaptiveNoLearn(Adaptive):
    """The same priority function with the cross-episode seed learning switched off.

    This is the control that says WHERE the gain comes from. If it matches `Adaptive`, the priority
    terms are doing the work and the strategy memory is decoration; if `Adaptive` wins, the loop is
    learning research taste without touching any weights, which is the mechanism worth distilling.
    """
    name = "adaptive-nolearn"

    def __init__(self, **kwargs):
        kwargs.setdefault("learn", False)
        super().__init__(**kwargs)


@dataclass
class Branch:
    """A line of attack. Never deleted: a stalled branch may still hold the breakthrough."""
    id: int
    nodes: list[int]
    seed: str
    spent: int = 0
    spawned_at: int = 0
    last_gain_at: int = 0
    # WHETHER `SeedBook.prefer` CHOSE THIS SEED, or a rule handed it out.
    #
    # This field is the difference between a working strategy memory and a confounded one. `credit`
    # updates a PREFERENCE weight, but `_reseed` assigns `contradiction` and `unexplored` by plateau
    # RULE rather than by preference. Crediting those taught the memory that whatever the rule
    # already hands out is valuable -- which is circular. It is why `unexplored` reached its ceiling
    # weight 3.0 on a mean credited gain of 75.8 per use while the decisions its weight could
    # actually influence changed 12 times in 400 forests and no outcome.
    by_preference: bool = False

    @property
    def tip(self) -> int:
        return self.nodes[-1]


# --- episodes -----------------------------------------------------------------

@dataclass
class Episode:
    func: int
    policy: str
    budget: int
    best: float
    solved: bool
    reached_known_best: bool
    known_best: float
    expanded: int
    dead_expansions: int
    reasons: list[str] = field(default_factory=list)

    @property
    def regret(self) -> float:
        return round(max(0.0, self.known_best - self.best), 3)


def run_episode(forest: Forest, policy: Policy, budget: int = DEFAULT_BUDGET) -> Episode:
    """Reveal the forest one expansion at a time and let `policy` choose which node to expand.

    Expanding a node means looking at the children the log already recorded for it. A node with no
    logged children costs an expansion and returns nothing, exactly as it did in reality. Every
    policy therefore faces the same experiments; only the allocation differs.
    """
    policy.reset(forest)
    state = SearchState(forest)
    state.revealed.update(forest.roots)
    frontier = set(forest.roots)
    known = forest.best_score
    dead = 0
    reasons: list[str] = []
    while frontier and state.step < budget:
        decision = policy.choose(sorted(frontier), state)
        frontier.discard(decision.node)
        state.expanded.add(decision.node)
        state.step += 1
        before = state.best.score
        kids = forest.children.get(decision.node, ())
        state.revealed.update(kids)
        frontier.update(kids)
        if not any(forest.attempts[k].score > before + PLATEAU_EPSILON for k in kids):
            dead += 1
        policy.observe(decision.node, kids, state)
        if len(reasons) < 8:
            reasons.append(decision.reason)
    final = state.best
    return Episode(forest.func, policy.name, state.step, final.score, final.exact,
                   final.score >= known - 1e-9, known, state.step, dead, reasons)


def evaluate(forests: dict[int, Forest], policies: Sequence[Policy],
             budget: int = DEFAULT_BUDGET) -> dict:
    """Every policy on every forest under one budget. The comparison is between allocations.

    `solved_functions` and `discovered_functions` are kept alongside the rates on purpose. Two
    rounded rates cannot show WHICH functions a policy won, and the question the gate has to answer
    -- does the learning component contribute anything? -- is a paired question. A rate difference
    can hide a wash (adaptive wins five, loses five) or dress up a single coin-flip as a gain.
    """
    report: dict = {"budget": budget, "functions": len(forests), "policies": {}}
    for policy in policies:
        episodes = [run_episode(f, policy, budget) for f in forests.values()]
        if not episodes:
            continue
        report["policies"][policy.name] = {
            "P_discovery": round(sum(1 for e in episodes if e.reached_known_best) / len(episodes), 4),
            "P_solved": round(sum(1 for e in episodes if e.solved) / len(episodes), 4),
            "mean_best": round(statistics.mean(e.best for e in episodes), 3),
            "mean_regret": round(statistics.mean(e.regret for e in episodes), 3),
            "mean_dead_share": round(statistics.mean(
                e.dead_expansions / max(1, e.expanded) for e in episodes), 4),
            "solved_functions": sorted(e.func for e in episodes if e.solved),
            "discovered_functions": sorted(e.func for e in episodes if e.reached_known_best),
        }
    return report


def curve(forests: dict[int, Forest], make_policy: Callable[[], Policy],
          budgets: Sequence[int]) -> list[dict]:
    """`B -> P(discovery)`: the primary curve the design asks for. A fresh policy per budget, so a
    longer run cannot be helped by what a shorter one learned."""
    rows = []
    for budget in budgets:
        policy = make_policy()
        episodes = [run_episode(f, policy, budget) for f in forests.values()]
        rows.append({"budget": budget,
                     "P_discovery": round(sum(1 for e in episodes if e.reached_known_best)
                                          / max(1, len(episodes)), 4),
                     "mean_best": round(statistics.mean(e.best for e in episodes), 3)})
    return rows


# --- the live loop ------------------------------------------------------------

class Generator(Protocol):
    """Whatever proposes C: a model, a replayer, or a test double."""
    def sample(self, prompt: str, n: int, temperature: float) -> list[str]: ...


class Scorer(Protocol):
    """Whatever decides. In this project that is always the compiler plus objdiff."""
    def score(self, func: int, source: str) -> tuple[bool, float, bool, str]: ...


class LiveLoop:
    """The forward loop: propose, compile, compare, then move the compute.

    The controller is the same object the replay study evaluates; only the environment differs. That
    is deliberate. If the controller cannot beat fixed seeding in replay, the design says stop before
    spending GPU hours, and this class is what would have spent them.
    """

    def __init__(self, generator: Generator, scorer: Scorer,
                 controller: Adaptive | None = None):
        self.generator = generator
        self.scorer = scorer
        self.controller = controller or Adaptive()
        self.log: list[dict] = []

    @staticmethod
    def action_for(plateau: Plateau) -> str:
        """Map plateau type to the scale of the next move."""
        return {Plateau.NONE: "continue", Plateau.EXHAUSTED: "reseed-partial",
                Plateau.UNRESOLVED: "local-mutation", Plateau.CONVERGENCE: "reseed-full",
                Plateau.REPRESENTATION: "deepen"}[plateau]

    def step(self, forest: Forest, state: SearchState, n: int = 4,
             temperature: float = 0.8) -> dict:
        decision = self.controller.choose(sorted(state.revealed), state)
        branch = self.controller.branch_of(decision.node) or self.controller.open_branch(
            decision.node, state)
        action = self.action_for(self.controller.plateau)
        attempt = forest.attempts[decision.node]
        prompt = (f"# scale={action} seed={branch.seed}\n"
                  f"# current score {attempt.score:.2f} "
                  f"faults={dict(zip(AXES, attempt.profile))}\n")
        results = []
        for source in self.generator.sample(prompt, n, temperature):
            compiled, score, exact, diff = self.scorer.score(forest.func, source)
            results.append({"compiled": compiled, "score": score, "exact": exact,
                            "profile": profile_of(diff, score, exact, compiled)})
        record = {"func": forest.func, "branch": branch.id, "action": action, "seed": branch.seed,
                  "plateau": self.controller.plateau.value, "reason": decision.reason,
                  "results": results}
        self.log.append(record)
        return record


# --- curriculum: what to fabricate and what to fetch next ---------------------

def weakness(forests: dict[int, Forest]) -> dict:
    """Where the solver is losing, per fault class, over the functions it has not solved.

    Deliberately measured on residuals, not self-reported by a model. This is the design's step 1,
    and it is the reason the loop does not need the model to know what it is bad at.
    """
    totals: collections.Counter = collections.Counter()
    unsolved = solved = 0
    for forest in forests.values():
        if forest.solved:
            solved += 1
            continue
        unsolved += 1
        best = max((a for a in forest.attempts.values() if a.compiled),
                   key=lambda a: a.score, default=None)
        if best is None:
            continue
        for axis, count in zip(AXES, best.profile):
            totals[axis] += count
    grand = sum(totals.values()) or 1
    return {"functions": len(forests), "unsolved": unsolved, "solved": solved,
            "by_class": {axis: {"faults": totals[axis], "share": round(totals[axis] / grand, 4)}
                         for axis in AXES},
            "dominant": totals.most_common(1)[0][0] if totals else None}


def curriculum(forests: dict[int, Forest]) -> dict:
    """Turn the weakness measurement into a data plan: what to generate, what to go and fetch.

    The three data sources are kept visibly separate because they are not interchangeable. Missing
    SYNTHETIC coverage means no generator produces that residual shape. A missing FETCH means the
    class exists in real code but no repository is wired to supply it. Register-allocation faults are
    called out separately: they have no shape, so no shape-based generator can target them at all.
    """
    weak = weakness(forests)
    try:
        from tools import synthetic_corpus as sc
        families: dict[str, list[str]] = collections.defaultdict(list)
        for family in sc.FAMILIES.values():
            families[family.capability].append(family.name)
    except Exception:                                    # the toolchain-free import path only
        families = {}
    covered_axes = {"structural", "regalloc", "offset", "width"}
    plan = []
    for axis, row in sorted(weak["by_class"].items(), key=lambda kv: -kv[1]["faults"]):
        generators = families.get(axis, [])
        if axis == "regalloc":
            source = "none-by-construction"
            note = ("register allocation has no instruction shape, so no synthetic family can target "
                    "it; the shape gate cannot validate data for it either")
        elif generators:
            source = "synthetic"
            note = f"families: {', '.join(sorted(generators))}"
        elif axis in covered_axes:
            source = "synthetic-gap"
            note = "a class the families claim to cover but no family names it"
        else:
            source = "fetch"
            note = "no generator produces this shape; it has to come from real matched code"
        plan.append({"class": axis, "faults": row["faults"], "share": row["share"],
                     "source": source, "note": note})
    return {"weakness": weak, "plan": plan,
            "observed": "the ordering above is measured, not chosen: it re-derives the design's "
                        "'a model writing generators will make the same mistakes, faster' from data"}


# --- distillation: the step that changes weights ------------------------------

def distillation_pairs(forests: dict[int, Forest], budget: int = DEFAULT_BUDGET) -> list[dict]:
    """Preference pairs for the weight step: `(state, better action, worse action)`.

    This is the part that is NOT the existing self-training loop. Ordinary synthetic-data training
    labels an answer right or wrong. Here the label is what the decision LED TO: each candidate action
    from a state is scored by the best score anywhere in its logged subtree, so an edit that lowers
    the score and later produces the breakthrough is a POSITIVE example and the tidy local
    improvement that goes nowhere is the negative one.

    The state is described the way the distilled model would see it -- the plateau it is in, how long
    it has been there, whether the residual is structured, and how crowded the neighbourhood is --
    never the answer.
    """
    pairs = []
    for forest in forests.values():
        controller = Adaptive()
        controller.reset(forest)
        state = SearchState(forest)
        state.revealed.update(forest.roots)
        frontier = set(forest.roots)
        while frontier and state.step < budget:
            decision = controller.choose(sorted(frontier), state)
            candidates = [(forest.best_of(n)[0], n) for n in sorted(frontier)]
            if len(candidates) >= 2:
                best_value, best_node = max(candidates)
                worst_value, worst_node = min(candidates)
                if best_value - worst_value > PLATEAU_EPSILON:
                    pairs.append({
                        "func": forest.func, "step": state.step,
                        "state": _describe(forest, state, decision.node),
                        "better": _action_of(forest, best_node),
                        "worse": _action_of(forest, worst_node),
                        "value_gap": round(best_value - worst_value, 3),
                        "plateau": controller.plateau.value,
                    })
            frontier.discard(decision.node)
            state.expanded.add(decision.node)
            state.step += 1
            kids = forest.children.get(decision.node, ())
            state.revealed.update(kids)
            frontier.update(kids)
            controller.observe(decision.node, kids, state)
    return pairs


def _describe(forest: Forest, state: SearchState, node: int) -> dict:
    """A plateau description, not a solution. This is what the distilled policy would condition on."""
    best = state.best
    return {
        "score": round(best.score, 2),
        "faults": dict(zip(AXES, best.profile)),
        "structured": bool(best.profile[AXES.index("structural")]),
        "repairable_share": round(best.tractability, 3),
        "expansions": state.step,
        "frontier": len(state.revealed) - len(state.expanded),
        "distinct_residuals": len({forest.attempts[i].profile for i in state.revealed}),
    }


def _action_of(forest: Forest, node: int) -> dict:
    attempt = forest.attempts[node]
    return {"node": node, "score": round(attempt.score, 2), "exact": attempt.exact,
            "profile": dict(zip(AXES, attempt.profile)),
            "reachable_value": round(forest.best_of(node)[0], 2)}


def gate(report: dict, budget: int | None = None) -> dict:
    """The stopping condition, checked rather than assumed.

    Before any weight update the controller must clear THREE controls, and the third is the one
    that decides whether there is anything to distil at all:

      R1  beat fixed diverse seeding on discovery;
      R2  beat random reseeding on discovery;
      R3  beat `adaptive-nolearn` on SOLVED functions, as a PAIRED comparison.

    R3 exists because R1 and R2 can both pass while the part of the controller that would be
    distilled contributes nothing. `adaptive-nolearn` is the same priority terms with the learned
    seed weights switched off, and the class docstring states the criterion outright: if it matches
    `Adaptive`, "the priority terms are doing the work and the strategy memory is decoration".
    R1/R2 only compare against policies that lack the priority terms too, so they cannot separate
    the allocation from the learning inside it.

    R3 is paired rather than a rate difference because a rate difference cannot distinguish a real
    gain from a wash: over n=400 functions one function is ~0.0025 of a rate, so an unpaired margin
    can be a single coin-flip. `n01` (solved only by `adaptive`) must exceed `n10` (solved only by
    `adaptive-nolearn`). The counts are always reported, so a one-function win reads as one function.

    `rule_version` is bumped when the rule changes, and consumers must refuse a stored verdict from
    an older version rather than reuse it: a verdict is only as good as the rule it was computed
    under. `passed` is the whole answer; `reason` names every comparison that failed.
    """
    rows = report.get("policies", {})
    budget = budget or report.get("budget")
    need = ("fixed-seed", "random", "adaptive-nolearn")
    missing = [name for name in need + ("adaptive",) if name not in rows]
    if missing:
        return {"passed": False, "budget": budget, "rule_version": GATE_RULE_VERSION,
                "reason": f"missing policies: {missing}", "conditions": {}}
    control = rows["adaptive"]
    failures = []
    for name in ("fixed-seed", "random"):
        if control["P_discovery"] <= rows[name]["P_discovery"]:
            failures.append(f"adaptive {control['P_discovery']} <= {name} {rows[name]['P_discovery']}"
                            f" on discovery")

    # R3. The per-function sets are required; a report that carries only rates cannot answer the
    # paired question, and answering it from rates is exactly the mistake this rule exists to stop.
    paired: dict = {"n01": None, "n10": None}
    solved = control.get("solved_functions")
    nolearn_solved = rows["adaptive-nolearn"].get("solved_functions")
    if solved is None or nolearn_solved is None:
        failures.append("the report has no per-function outcomes, so the no-learn control cannot be "
                        "compared as a paired test; re-run the replay to record them")
    else:
        mine, theirs = set(solved), set(nolearn_solved)
        paired = {"n01": len(mine - theirs), "n10": len(theirs - mine),
                  "both": len(mine & theirs)}
        if paired["n01"] <= paired["n10"]:
            failures.append(
                f"adaptive does not beat its no-learn control on solved functions "
                f"(only-adaptive {paired['n01']}, only-nolearn {paired['n10']}): the priority terms "
                f"are doing the work and the strategy memory is decoration")

    return {"passed": not failures, "budget": budget, "rule_version": GATE_RULE_VERSION,
            "reason": ("beats fixed seeding and random reseeding on discovery, and beats its "
                       "no-learn control on solved functions" if not failures
                       else "; ".join(failures)),
            "conditions": {
                "R1_beats_fixed_seed_discovery":
                    control["P_discovery"] > rows["fixed-seed"]["P_discovery"],
                "R2_beats_random_discovery":
                    control["P_discovery"] > rows["random"]["P_discovery"],
                "R3_beats_no_learn_on_solved":
                    paired["n01"] is not None and paired["n01"] > paired["n10"],
            },
            "adaptive": control["P_discovery"],
            "fixed_seed": rows["fixed-seed"]["P_discovery"],
            "random": rows["random"]["P_discovery"],
            "adaptive_solved": control.get("P_solved"),
            "no_learn_solved": rows["adaptive-nolearn"].get("P_solved"),
            "paired_solved": paired}


# --- CLI ----------------------------------------------------------------------

def _open_kb(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", type=Path, required=True)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    ap.add_argument("--functions", type=int, default=400, help="largest forests to study")
    ap.add_argument("--min-attempts", type=int, default=3)
    ap.add_argument("--curve", action="store_true",
                    help="also print B -> P(discovery) for greedy and adaptive")
    ap.add_argument("--curriculum", action="store_true",
                    help="print the measured weakness and the data plan that follows from it")
    ap.add_argument("--distill", type=Path, default=None,
                    help="write preference pairs for the weight step to this JSONL path")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    forests = load_forests(_open_kb(args.kb.expanduser()), limit=args.functions,
                           min_attempts=args.min_attempts)
    if not forests:
        raise SystemExit("no forests with enough attempts; nothing to study")
    policies = [Greedy(), ParetoPolicy(), RandomReseed(), FixedSeed(), Evolutionary(),
                AdaptiveNoLearn(), Adaptive()]
    report = evaluate(forests, policies, args.budget)
    report["generated_at"] = int(time.time())
    report["source"] = str(args.kb)
    if args.curve:
        budgets = [1, 2, 4, 8, 16, 24]
        report["curve"] = {"greedy": curve(forests, Greedy, budgets),
                           "adaptive": curve(forests, Adaptive, budgets)}
    if args.curriculum:
        report["curriculum"] = curriculum(forests)
    report["gate"] = gate(report, args.budget)
    if args.distill:
        pairs = distillation_pairs(forests, args.budget)
        args.distill.parent.mkdir(parents=True, exist_ok=True)
        with args.distill.open("w", encoding="utf-8") as handle:
            for pair in pairs:
                handle.write(json.dumps(pair) + "\n")
        report["distillation"] = {"pairs": len(pairs), "path": str(args.distill),
                                  "gate_passed": report["gate"]["passed"]}
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
