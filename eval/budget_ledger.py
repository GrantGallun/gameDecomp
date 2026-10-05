"""One experiment-wide ledger, shared by every stage and every generation.

THE FAILURE THIS PREVENTS. A per-run allowance multiplied by the number of stages is not a budget: the
research round, the training run, the transfer comparison and each generation's evaluation would each
get the full allowance, so a two-generation experiment would silently cost four times what was
declared. Here the caps belong to the EXPERIMENT, reservations are made before the work, and a stage
that cannot reserve does not start.

WHAT WAS STILL BROKEN, MEASURED ON THIS REPOSITORY'S OWN LEDGER. The ceiling was prose in three places.
`reserve()` compared `spent + amount` with the cap and ignored the reservations already outstanding, so
two stages could each reserve the whole remaining capacity. `spend()` never consulted a cap at all, so
`Caps(compiles=10)` plus two 8-charges produced `spent.compiles == 16` and `remaining()` of -6 with
nothing in the record saying the cap had been passed. And a spend had no operation identity: a
reservation is idempotent because it is keyed by stage, while re-reporting the same spend after a
restart charged it a second time.

THE SEMANTICS, STATED ONCE. Each method repeats the part that applies to it.

* IDENTITY. Every reservation, spend and overrun carries an operation id (`op`), defaulting to the
  stage name -- the identity reservations already used, so existing callers became resumable without
  changing. Amounts reported for an operation are its RUNNING TOTAL: re-reporting the same total
  charges nothing and comes back `already_charged=True`, and reporting a larger total charges only the
  increase, so a stage interrupted after a partial report can re-report its true total without losing
  what was already charged or charging it twice. A genuinely separate run of the same stage is a
  separate operation and needs its own `op` (e.g. `f"{stage}:round2"`); the ledger cannot guess that
  from the name, and guessing would either lose the second run's real usage or double-charge the first.
* RESERVATIONS REDUCE WHAT IS AVAILABLE. `reserve()` raises BudgetExceeded and records nothing when
  `spent + outstanding reservations + amount` would pass the cap, so the stage that cannot reserve does
  not start. A spend does not remove its stage's reservation -- `release()` does, and the existing
  receipt contract is that a held reservation survives the spend -- but the capacity that reservation
  CLAIMS shrinks by what the same operation has already spent, so a fully spent reservation stops
  holding capacity it no longer needs.
* SPENDING PAST A CAP IS REFUSED; WORK THAT ALREADY RAN IS STILL RECORDED. `spend()` raises
  BudgetExceeded BEFORE recording anything when the charge would take `spent` past a declared cap:
  work must not start without capacity. That would lose usage if a compile had already happened, so the
  already-happened path is explicit instead: `record_overrun()`, which requires a reason and writes a
  machine-readable `overrun` event carrying the cap, the excess, the spent-before/after and the reason.
  `snapshot()["overrun"]` and `snapshot()["overruns"]` expose it, so `spent` can only pass a cap
  together with an event that says so -- and the 10-cap/16-spend case is refused at the reservation,
  refused again at the spend, and recordable only as a named overrun.
* `remaining()` KEEPS ITS OLD KEYS AND THEIR MEANING. `remaining()[kind]` is still `cap - spent`
  (everything not yet charged), NOT reduced by reservations, because receipts already quote it and
  silently redefining it would make an old number mean something new. The two added figures are
  `remaining()["reserved"][kind]` (total outstanding reservations for that kind) and
  `remaining()["available"][kind]` (`cap - spent - reserved`: what a NEW reservation may still claim,
  which is the number to gate work on and which goes negative once a stage has spent against capacity
  it had reserved).
* EVALUATION IS A SEPARATE ACCOUNT, END TO END. `reserve_evaluation()` already charged the dedicated
  `evaluation_calls` / `evaluation_compiles` counters, but nothing ever spent them: `paired_transfer`'s
  research-shaped `compiles` charges landed on the research cap, so the reserved evaluation capacity was
  never touched and the declared evaluation budget was decoration. A stage that holds an evaluation
  reservation now routes its research-kind spends -- and its `<stage>-setup` companion, which is the
  shape `paired_transfer` writes -- to the evaluation counters, with `account` and `routed` recorded in
  the event and returned to the caller. `spend_evaluation()` is the explicit entry point for a caller
  that does not reserve first.

IDEMPOTENT ON RESUME. Every reservation carries the stage that asked for it, and every spend and
overrun carries its operation id, all of it persisted in the append-only JSONL. Replaying the file
rebuilds `spent`, `reservations`, the per-operation totals and the overrun records, so a restart cannot
reset usage, hand out the same capacity twice, or charge an operation twice unnoticed. History is
charged exactly as it was recorded: a ledger written by the older code keeps its numbers and gains a
`detected_on_replay` overrun record rather than being rewritten into a clean one.

EVALUATION IS RESERVED FIRST (spec §10). `reserve_evaluation()` must succeed before research begins,
because a research round that consumes the compiles the panel needed leaves the experiment unable to
measure anything -- which is how a run ends up reporting a result it cannot support.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2
KINDS = ("model_calls", "compiles", "train_steps", "seconds", "tokens",
         "evaluation_calls", "evaluation_compiles")

#: Floating-point slack for "is this over the cap". A cap of exactly 10 accepts a request for exactly
#: 10; it is a ceiling, not a margin.
_TOLERANCE = 1e-9


@dataclass
class Caps:
    """The declared ceilings.

    RESEARCH AND EVALUATION CAPS ARE SEPARATE (spec §10) and both belong to the experiment. The 12-call
    / 72-compile pair is the research allowance inherited from the local research runner; a paired
    evaluation needs its own, because two arms over a panel spend calls and compiles per decision and
    charging those to the research allowance would either starve the research or silently multiply the
    declared budget.
    """
    model_calls: int = 12
    compiles: int = 72
    train_steps: int = 0
    seconds: float = 1800.0
    tokens: int = 0
    evaluation_calls: int = 60
    evaluation_compiles: int = 144
    panel_functions: int = 6
    per_task_actions: int = 5

    def as_dict(self) -> dict:
        return asdict(self)


class BudgetExceeded(RuntimeError):
    pass


def _evaluation_amounts(amounts: dict) -> tuple[dict, dict]:
    """Map `calls=`/`compiles=` onto the dedicated evaluation counters, and say what was mapped.

    Same prefix rule the pre-existing `reserve_evaluation()` used, kept because callers name the
    research kind (`calls`, `compiles`) and expect the dedicated counter. A kind with no
    `evaluation_*` sibling fails `Ledger._validate` loudly rather than being silently charged to the
    research cap.
    """
    mapped, routed = {}, {}
    for kind, amount in amounts.items():
        target = kind if kind.startswith("evaluation_") else "evaluation_" + kind
        mapped[target] = mapped.get(target, 0) + amount
        if target != kind:
            routed[kind] = target
    return mapped, routed


@dataclass
class Ledger:
    path: Path
    caps: Caps
    spent: dict = field(default_factory=dict)
    reservations: dict = field(default_factory=dict)     # operation id -> {kind: n}
    events: list = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    operations: dict = field(default_factory=dict)       # operation id -> {kind: total charged}
    overruns: list = field(default_factory=list)         # recorded, and replay-detected, overruns
    evaluation_stages: set = field(default_factory=set)  # stages whose spends are evaluation work

    # --- construction -------------------------------------------------------

    @classmethod
    def open(cls, path: Path, caps: Caps) -> "Ledger":
        """Replay an existing ledger so resume continues rather than restarts."""
        path = Path(path)
        ledger = cls(path=path, caps=caps)
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                ledger.events.append(event)
                ledger._replay(event)
            if ledger.events:
                ledger.started_at = ledger.events[0].get("at", ledger.started_at)
            ledger._flag_replayed_overruns()
        return ledger

    def _replay(self, event: dict) -> None:
        """Rebuild the counters from one recorded event. History is charged as recorded, never re-judged.

        An event from schema version 1 carries no `op`; its stage name is used as the operation id,
        which is exactly the identity the old `reserve()` used and the identity a resumed v1 run will
        report again -- so replaying the audit's ledger does not make its charges chargeable twice.
        Charges are summed per event, because each recorded event is a fact about work that happened;
        the running-total rule applies only to live reports.
        """
        kind = event.get("type")
        stage = event.get("stage", "")
        op = event.get("op") or stage
        amounts = dict(event.get("amounts") or {})
        account = event.get("account") or ("evaluation" if _looks_like_evaluation(amounts) else
                                           "research")
        if account == "evaluation":
            self.evaluation_stages.add(stage)
        if kind == "reserve":
            self.reservations[op] = amounts
        elif kind == "release":
            self.reservations.pop(op, None)
        elif kind == "spend":
            self._charge(op, amounts)
        elif kind == "overrun":
            self._charge(op, amounts)
            self.overruns.append({
                "stage": stage, "op": op, "amounts": amounts, "reason": event.get("reason", ""),
                "cap": dict(event.get("cap") or {}), "overrun_by": dict(event.get("overrun_by") or {}),
                "spent_before": dict(event.get("spent_before") or {}),
                "spent_after": dict(event.get("spent_after") or {}), "at": event.get("at"),
                "recorded": True, "detected_on_replay": False})

    def _flag_replayed_overruns(self) -> None:
        """A ledger written before overrun events existed still has to SHOW its breach.

        The old code recorded the 10-cap/16-spend case as two ordinary spends, so replaying that file
        yields `spent > cap` with no event explaining it. The numbers are not rewritten -- they are the
        historical record -- but the breach is named here so `snapshot()["overruns"]` is never empty
        while `spent` is past a cap.
        """
        recorded = {kind for record in self.overruns for kind in record.get("amounts", {})}
        for kind, amount in self.spent.items():
            cap = getattr(self.caps, kind, 0)
            if cap and amount - cap > _TOLERANCE and kind not in recorded:
                self.overruns.append({
                    "stage": "", "op": "", "kind": kind, "amounts": {kind: round(amount, 3)},
                    "reason": ("spent is past the cap in a ledger written before overrun events existed "
                               "(schema_version 1): the file records the charges and no event explains "
                               "them"),
                    "cap": {kind: cap}, "overrun_by": {kind: round(amount - cap, 3)},
                    "spent_before": {}, "spent_after": {kind: round(amount, 3)}, "at": None,
                    "recorded": False, "detected_on_replay": True})

    def _append(self, event: dict) -> dict:
        event = {**event, "at": time.time(), "schema_version": SCHEMA_VERSION}
        self.events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    # --- accounting ---------------------------------------------------------

    def reserved_totals(self, *, outstanding_only: bool = True) -> dict:
        """What the outstanding reservations still claim, by kind.

        `outstanding_only` subtracts whatever has already been SPENT under the same operation id,
        because capacity a stage has already used is no longer capacity that stage is holding: after
        `reserve("one", compiles=8)` and `spend("one", compiles=8)` there is no outstanding claim on
        those 8, and a reader of `available()` should see the remaining 2, not -6. The reservation
        itself stays on the books -- `reservations` and `snapshot()["reservations"]` still show it, which
        is the receipt contract -- only its claim shrinks. Pass `outstanding_only=False` for the raw
        sum of what stages hold.
        """
        totals: dict = {}
        for op, amounts in self.reservations.items():
            charged = self.operations.get(op, {})
            for kind, amount in amounts.items():
                claim = amount - charged.get(kind, 0) if outstanding_only else amount
                if claim > 0:
                    totals[kind] = totals.get(kind, 0.0) + claim
        return totals

    def available(self) -> dict:
        """`cap - spent - outstanding reservations` per declared kind: what a new reservation may claim."""
        reserved = self.reserved_totals()
        return {kind: round(getattr(self.caps, kind) - self.spent.get(kind, 0.0)
                            - reserved.get(kind, 0.0), 3)
                for kind in KINDS if getattr(self.caps, kind)}

    def overrun_totals(self) -> dict:
        """By how much `spent` is past each cap. Empty when nothing is over."""
        out = {}
        for kind, amount in self.spent.items():
            cap = getattr(self.caps, kind, 0)
            if cap and amount - cap > _TOLERANCE:
                out[kind] = round(amount - cap, 3)
        return out

    def remaining(self) -> dict:
        """Headroom, the outstanding reservations, and what is genuinely available.

        `remaining()[kind]` is UNCHANGED: `cap - spent`, everything not yet charged, whether or not a
        stage has already laid claim to it. It is deliberately NOT reduced by reservations, because
        receipts and callers already quote it and a key that quietly started meaning "available" would
        make every previously reported number wrong.

        Added, without redefining anything:
          * `remaining()["reserved"][kind]`  -- the capacity still claimed by outstanding reservations:
            each reservation minus what has been spent under the same operation id (a fully spent
            reservation claims nothing, though it stays on the books in `reservations`).
          * `remaining()["available"][kind]` -- `cap - spent - reserved`: what a new reservation may
            claim. This is the figure to gate work on, and `available() == remaining()[kind] -
            remaining()["reserved"][kind]` for every kind.
          * `remaining()["seconds_wall"]`    -- unchanged wall-clock figure.
        """
        reserved = self.reserved_totals()
        out: dict = {}
        held: dict = {}
        for kind in KINDS:
            cap = getattr(self.caps, kind)
            if not cap:
                continue
            out[kind] = round(cap - self.spent.get(kind, 0.0), 3)
            if reserved.get(kind):
                held[kind] = round(reserved[kind], 3)
        out["reserved"] = held
        out["available"] = self.available()
        out["seconds_wall"] = round(self.caps.seconds - (time.time() - self.started_at), 1)
        return out

    # --- reservations -------------------------------------------------------

    def reserve(self, stage: str, *, op: str | None = None, **amounts: float) -> dict:
        """Reserve before the work. Re-entering the same operation returns the same reservation.

        Identity is the operation id (`op`, defaulting to `stage`), so re-entering a stage after an
        interruption is a lookup rather than a second charge -- the guarantee the old code already made
        for reservations. A NEW reservation is refused with BudgetExceeded, recording nothing, when
        `spent + outstanding reservations + amount` would pass the cap for any kind: outstanding
        reservations reduce what a later stage may ask for, which is what stops two stages reserving
        the same remaining capacity twice.

        A stage that holds an evaluation reservation (see `reserve_evaluation`) also routes a
        research-kind reservation to its evaluation counter, so the panel's setup reservation reduces
        the capacity the panel's setup spend will actually be charged to.
        """
        key = op or stage
        if key in self.reservations:
            return {"stage": stage, "amounts": self.reservations[key], "reused": True}
        mapped, routed = self._account(stage, amounts)
        return self._reserve(stage, key, mapped, account="evaluation" if routed else None,
                             routed=routed)

    def reserve_evaluation(self, stage: str, *, op: str | None = None, **amounts: float) -> dict:
        """Reserve evaluation capacity. Call this BEFORE any research spend.

        The docstring above claimed this ordering and no method existed to enforce it, so the guarantee
        was prose. It charges the dedicated `evaluation_*` caps, which is what makes the ordering real:
        research cannot consume the capacity the panel needs, because the panel's capacity is a
        different counter. The stage is remembered as an evaluation account, so its later `compiles=` /
        `model_calls=` spends are charged to those counters too (`spend_evaluation` is the explicit
        form of the same thing).
        """
        key = op or stage
        if key in self.reservations:
            return {"stage": stage, "amounts": self.reservations[key], "reused": True}
        mapped, routed = _evaluation_amounts(amounts)
        return self._reserve(stage, key, mapped, account="evaluation", routed=routed)

    def _reserve(self, stage: str, key: str, amounts: dict, *, account, routed) -> dict:
        """`amounts` are already in the counters they will be held against."""
        self._validate(amounts)
        self._check_reserve_capacity(f"{stage}[{key}]", amounts)
        self.reservations[key] = dict(amounts)
        if account == "evaluation":
            self.evaluation_stages.add(stage)
        event = {"type": "reserve", "stage": stage, "op": key, "amounts": dict(amounts)}
        if account:
            event["account"] = account
        if routed:
            event["routed"] = routed
        self._append(event)
        return {"stage": stage, "amounts": dict(amounts), "reused": False}

    def release(self, stage: str, *, op: str | None = None) -> None:
        """Give a reservation back. A SPEND does not release one; only this does."""
        key = op or stage
        if key in self.reservations:
            self.reservations.pop(key)
            self._append({"type": "release", "stage": stage, "op": key})

    # --- spends -------------------------------------------------------------

    def spend(self, stage: str, *, op: str | None = None, **amounts: float) -> dict:
        """Record ACTUAL usage for one operation, refusing any charge that would pass the cap.

        IDENTITY. `op` (default: `stage`) names the operation, and the amounts are its RUNNING TOTAL. A
        repeat report of the same total -- after a restart, or a retry -- charges nothing and returns
        `already_charged=True`. A larger total charges only the increase, so an interrupted stage can
        re-report its final usage without losing what the first report charged and without charging it
        twice. A genuinely separate run of the same stage is a separate operation: give it its own op
        (e.g. `f"{stage}:round2"`), because the ledger has no way to tell the two apart.

        THE CAP. If the charge would take `spent` past a declared cap, BudgetExceeded is raised BEFORE
        anything is recorded: the work must not start. (A spend is measured against the CAP, not against
        other stages' reservations: those claims were already checked when they were granted, and
        keeping them out of this check is what lets a stage spend what it reserved.) Work that has
        ALREADY happened is not lost and is not smuggled in as a normal spend either -- the caller
        reports it with `record_overrun(...)`, which requires a reason and writes the explicit event
        `snapshot()["overruns"]` shows. The returned dict says which happened: `charged` is the increase
        recorded now, `already_charged` says the reported total was already covered,
        `previously_charged` says this operation had been charged before, and `routed` names any counter
        the charge was moved to.
        """
        return self._spend(stage, op=op, amounts=amounts, account=None)

    def spend_evaluation(self, stage: str, *, op: str | None = None, **amounts: float) -> dict:
        """Charge the dedicated evaluation counters explicitly, without reserving first."""
        return self._spend(stage, op=op, amounts=amounts, account="evaluation")

    def _spend(self, stage: str, *, op, amounts: dict, account) -> dict:
        key = op or stage
        if account == "evaluation":
            mapped, routed = _evaluation_amounts(amounts)
        else:
            mapped, routed = self._account(stage, amounts)
            if routed:
                account = "evaluation"
        self._validate(mapped)
        previously = key in self.operations
        delta = self._increase(key, mapped)
        self._check_spend_capacity(f"{stage}[{key}]", delta)
        if not any(delta.values()):
            return {"stage": stage, "op": key, "account": account or "research", "charged": {},
                    "reported": dict(amounts), "total": dict(self.operations.get(key, {})),
                    "already_charged": previously, "previously_charged": previously, "routed": routed,
                    "overrun": False, "overrun_by": {}}
        self._charge(key, delta)
        if account == "evaluation":
            self.evaluation_stages.add(stage)
        event = {"type": "spend", "stage": stage, "op": key, "amounts": dict(delta)}
        if account == "evaluation":
            event["account"] = "evaluation"
        if routed:
            event["routed"] = routed
            event["reported"] = dict(amounts)
        self._append(event)
        return {"stage": stage, "op": key, "account": account or "research", "charged": dict(delta),
                "reported": dict(amounts), "total": dict(self.operations[key]),
                "already_charged": False, "previously_charged": previously, "routed": routed,
                "overrun": False, "overrun_by": {}}

    def record_overrun(self, stage: str, *, reason: str, op: str | None = None,
                       evaluation: bool = False, **amounts: float) -> dict:
        """Record work that REALLY HAPPENED even though it passes a cap. A reason is mandatory.

        This is the documented resolution of "exceeding the cap cannot start more work" against "a
        compile that really ran must not be lost": `spend()` refuses, and this method is the only path
        that can charge past a cap -- it writes an `overrun` event carrying the cap, the excess, the
        spent before and after, and the caller's reason, and appends the same record to
        `snapshot()["overruns"]`. A charge that turns out not to pass a cap is written as an ordinary
        spend event, so an overrun record always means an overrun happened.

        Identity and the running-total rule are the same as `spend()`. `evaluation=True` (or a stage
        that already holds an evaluation reservation) charges the evaluation counters.
        """
        if not str(reason).strip():
            raise ValueError("an overrun must state a reason; an unexplained cap breach is not "
                             "bookkeeping")
        key = op or stage
        if evaluation:
            mapped, routed = _evaluation_amounts(amounts)
        else:
            mapped, routed = self._account(stage, amounts)
            evaluation = bool(routed)
        self._validate(mapped)
        previously = key in self.operations
        delta = self._increase(key, mapped)
        if not any(delta.values()):
            return {"stage": stage, "op": key, "account": "evaluation" if evaluation else "research",
                    "charged": {}, "reported": dict(amounts), "total": dict(self.operations[key]),
                    "already_charged": previously, "previously_charged": previously, "routed": routed,
                    "overrun": False, "overrun_by": {}, "reason": reason}
        before = {kind: self.spent.get(kind, 0.0) for kind in delta}
        self._charge(key, delta)
        after = {kind: self.spent.get(kind, 0.0) for kind in delta}
        over = {kind: round(value - getattr(self.caps, kind), 3) for kind, value in after.items()
                if getattr(self.caps, kind) and value - getattr(self.caps, kind) > _TOLERANCE}
        if evaluation:
            self.evaluation_stages.add(stage)
        event = {"type": "overrun" if over else "spend", "stage": stage, "op": key,
                 "amounts": dict(delta)}
        if evaluation:
            event["account"] = "evaluation"
        if routed:
            event["routed"] = routed
            event["reported"] = dict(amounts)
        if over:
            event.update(reason=reason, overrun_by=over, cap={k: getattr(self.caps, k) for k in over},
                         spent_before=before, spent_after=after)
        recorded = self._append(event)
        if over:
            self.overruns.append({"stage": stage, "op": key, "amounts": dict(delta), "reason": reason,
                                  "cap": {k: getattr(self.caps, k) for k in over}, "overrun_by": over,
                                  "spent_before": before, "spent_after": after, "at": recorded["at"],
                                  "recorded": True, "detected_on_replay": False})
        return {"stage": stage, "op": key, "account": "evaluation" if evaluation else "research",
                "charged": dict(delta), "reported": dict(amounts), "total": dict(self.operations[key]),
                "already_charged": False, "previously_charged": previously, "routed": routed,
                "overrun": bool(over), "overrun_by": over, "reason": reason}

    def guard_seconds(self) -> None:
        if time.time() - self.started_at > self.caps.seconds:
            raise BudgetExceeded(
                f"the experiment-wide wall clock cap ({self.caps.seconds}s) is exhausted; stages stop "
                f"rather than silently running past the declared budget")

    # --- internals ----------------------------------------------------------

    def _account(self, stage: str, amounts: dict) -> tuple[dict, dict]:
        """Route a stage's amounts to the evaluation counters when the stage owns that account."""
        base = self._evaluation_base(stage)
        if base is None:
            return dict(amounts), {}
        mapped, routed = {}, {}
        for kind, amount in amounts.items():
            # Only kinds with a dedicated evaluation counter are moved; `seconds` and the rest stay
            # where they are, so routing can never turn a valid spend into an unknown kind.
            target = "evaluation_" + kind
            target = target if target in KINDS else kind
            mapped[target] = mapped.get(target, 0) + amount
            if target != kind:
                routed[kind] = target
        return mapped, routed

    def _evaluation_base(self, stage: str) -> str | None:
        """The registered evaluation stage a name belongs to, or None.

        `paired_transfer` charges `<stage>-setup` and `<stage>`, and both are the panel's work once
        `<stage>` was reserved through `reserve_evaluation`.
        """
        for base in sorted(self.evaluation_stages):
            if stage == base or stage.startswith(base + "-"):
                return base
        return None

    @staticmethod
    def _validate(amounts: dict) -> None:
        for kind, amount in amounts.items():
            if kind not in KINDS:
                raise ValueError(f"unknown budget kind {kind!r}")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
                raise ValueError(f"{kind} must be a non-negative number, got {amount!r}")

    def _check_reserve_capacity(self, label: str, amounts: dict) -> None:
        reserved = self.reserved_totals()
        for kind, amount in amounts.items():
            cap = getattr(self.caps, kind)
            if not cap or amount <= 0:
                continue
            spent = self.spent.get(kind, 0.0)
            held = reserved.get(kind, 0.0)
            committed = spent + held + amount
            if committed - cap > _TOLERANCE:
                raise BudgetExceeded(
                    f"{label}: {kind} would be committed to {committed:.3f} against a cap of {cap} "
                    f"(spent {spent:.3f} + already reserved {held:.3f} + this request {amount:.3f}); "
                    f"{cap - spent - held:.3f} is genuinely available, so this stage does not start")

    def _check_spend_capacity(self, label: str, amounts: dict) -> None:
        for kind, amount in amounts.items():
            cap = getattr(self.caps, kind)
            if not cap or amount <= 0:
                continue
            spent = self.spent.get(kind, 0.0)
            if spent + amount - cap > _TOLERANCE:
                raise BudgetExceeded(
                    f"{label}: charging {amount:.3f} {kind} would take spent to {spent + amount:.3f} "
                    f"against a cap of {cap} ({max(0.0, cap - spent):.3f} left). The work must not "
                    f"start; if it has ALREADY run, record it with record_overrun(stage, reason=..., "
                    f"{kind}=...) so the breach is an explicit event instead of an ordinary spend")

    def _increase(self, key: str, mapped: dict) -> dict:
        """The part of `mapped` not already charged to this operation. All zeros means a repeat report.

        `mapped` is the caller's running total for the operation, so what is charged now is the increase
        over what this operation already holds. That is what makes a retry idempotent AND keeps the real
        usage of a stage that was interrupted after a partial report.
        """
        total = self.operations.get(key, {})
        delta = {}
        for kind, amount in mapped.items():
            increase = amount - total.get(kind, 0)
            delta[kind] = 0 if increase <= _TOLERANCE else increase
        return delta

    def _charge(self, op: str, amounts: dict) -> None:
        """Add `amounts` to `spent` and to the operation's own total. No cap decision happens here."""
        total = self.operations.setdefault(op, {})
        for kind, amount in amounts.items():
            self.spent[kind] = self.spent.get(kind, 0) + amount
            total[kind] = total.get(kind, 0) + amount

    # --- reporting ----------------------------------------------------------

    def snapshot(self) -> dict:
        """Everything a receipt needs, with any overrun impossible to miss.

        `overrun` is the by-kind excess over each cap and `overruns` is the list of overrun records --
        both recorded ones and ones detected while replaying a ledger written before overrun events
        existed (`detected_on_replay: True`). A reader never has to compare `spent` against `caps` to
        discover that the ceiling was passed.
        """
        return {"caps": self.caps.as_dict(),
                "spent": {k: round(v, 3) for k, v in self.spent.items()},
                "remaining": self.remaining(),
                "reserved": {k: round(v, 3) for k, v in self.reserved_totals().items()},
                "available": self.available(),
                "reservations": self.reservations,
                "operations": {op: {k: round(v, 3) for k, v in amounts.items()}
                               for op, amounts in self.operations.items()},
                "overruns": self.overruns,
                "overrun": self.overrun_totals(),
                "events": len(self.events),
                "started_at": self.started_at}


def _looks_like_evaluation(amounts: dict) -> bool:
    """Replay aid: a schema-version-1 reservation whose kinds are all `evaluation_*` was one."""
    return bool(amounts) and all(kind.startswith("evaluation_") for kind in amounts)
