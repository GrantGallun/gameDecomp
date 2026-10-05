"""What a confirmed finding is allowed to BECOME.

A finding is not a behaviour change. This module is the only path from a verified notebook row to
something a solver can use, and it deliberately offers just two intervention kinds (spec §5):

  MEMORY NOTE   a short, applicability-scoped statement retrieved into a policy's observation when the
                state is compatible. It carries the raw experiment's identity, so the note is an
                INFERENCE over immutable evidence and never a replacement for it.
  COMPOSITION   a declarative preference over EXISTING registered actions: "in states matching this
                predicate, try these actions in this order". No new action, no generated Python, no
                self-modifying code. The menu is validated against the registry, so a composition that
                names an action which does not exist is rejected at load time rather than at run time.

BOUNDED MEANS BOUNDED. A composition cannot add an action slot: it reorders what a policy would already
choose among, and the loop's own budget still bounds the episode. `ComposedPolicy` therefore wraps a
policy rather than replacing it, and `assert_bounded` checks the declared preferences are a subset of
the registry.

WHY NOTHING IS ENABLED AUTOMATICALLY. `intervention_from_finding` produces a PROPOSAL with status
`proposed`. Only `confirm()` flips it to `confirmed`, and only after the declared test fired -- a
hypothesis does not get to change behaviour (AGENTS.md), and this module is where that rule is enforced
for research findings rather than for patterns.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
ARCHIVE_CAP = 200


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class Note:
    """A retrievable, applicability-scoped statement backed by one experiment."""
    id: str
    text: str
    applicability: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    status: str = "proposed"          # proposed | confirmed | refuted | retired
    counterexamples: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Composition:
    """A declarative preference over existing actions, valid only in matching states."""
    id: str
    when: dict
    prefer: list
    reason: str
    provenance: dict = field(default_factory=dict)
    status: str = "proposed"
    created_at: float = field(default_factory=time.time)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def assert_bounded(composition: Composition) -> None:
    """A composition may only reorder actions that already exist. Checked at load, not at run."""
    from eval.tool_registry import ACTIONS
    unknown = [name for name in composition.prefer if name not in ACTIONS]
    if unknown:
        raise ValueError(f"composition {composition.id} names actions that do not exist: {unknown}")
    if not composition.prefer:
        raise ValueError(f"composition {composition.id} prefers nothing")
    if any(name in ("stop",) for name in composition.prefer[:-1]):
        raise ValueError(f"composition {composition.id} prefers `stop` before other actions; that is "
                         f"an early-stop rule and is not a bounded composition")


def matches(applicability: dict, state: dict) -> bool:
    """Does this applicability block describe the state in front of the policy?

    EVERY key must match, and an empty block matches nothing -- a note that applies everywhere is a
    global behaviour change wearing a finding's clothes.
    """
    if not applicability:
        return False
    for key, expected in applicability.items():
        actual = state.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def notes_for(notes: list[Note], state: dict, *, limit: int = 3) -> list[Note]:
    """Retrieve the confirmed notes compatible with a state, newest first."""
    usable = [note for note in notes if note.status == "confirmed" and matches(note.applicability, state)]
    usable.sort(key=lambda note: note.created_at, reverse=True)
    return usable[:limit]


def confirm_applicability(note: Note, cluster: dict, *, observed: dict | None = None) -> dict:
    """The declared test that enables a note. IT CONFIRMS APPLICABILITY, NOT EFFECT.

    WHAT WENT WRONG WITHOUT THIS. The module's docstring described a `confirm()` step and no such
    function existed anywhere in the repository, so every note stayed `proposed`, `candidate-frozen`
    could never freeze a child, and the evaluation filtered on `confirmed` and therefore always ran
    with zero notes enabled. The pilot's "the intervention was never applied" was not restraint; it was
    an unimplemented function, and reporting it as a null result would have been reporting my own dead
    code back as evidence.

    WHAT THE TEST ACTUALLY ESTABLISHES, stated so the status is not read as more than it is: the note's
    applicability block matches the feature signature of the cluster that motivated it, so the note WILL
    be retrieved for real states of the kind it was written for. It says nothing about whether the note
    helps -- that is exactly what the paired transfer comparison measures, and a note can be applicable
    and useless.

    A note whose applicability matches nothing in its own cluster is left `proposed`: it would never be
    retrieved, and enabling it would only add a prompt difference that cannot fire.
    """
    if not note.applicability:
        return {"confirmed": False, "reason": "an empty applicability block matches nothing"}
    signature = {"error_class": cluster.get("error_class"),
                 "residual_kind": cluster.get("residual_kind"),
                 "size_bucket": cluster.get("size_bucket")}
    mismatched = {key: (expected, signature.get(key))
                  for key, expected in note.applicability.items()
                  if key in signature and signature.get(key) != expected}
    if mismatched:
        return {"confirmed": False,
                "reason": f"applicability does not match the motivating cluster: {mismatched}"}
    matched = matches(note.applicability, signature)
    if not matched:
        return {"confirmed": False, "reason": "applicability matched no field of the cluster"}
    note.status = "confirmed"
    note.provenance = {**note.provenance,
                       "confirmation": {"test": "applicability_matches_motivating_cluster",
                                        "cluster_id": cluster.get("cluster_id"),
                                        "signature": signature, "observed": observed or {},
                                        "establishes": "retrieval for this state class, not effect",
                                        "confirmed_at": time.time()}}
    return {"confirmed": True, "reason": "applicability matches the motivating cluster",
            "signature": signature}


class NoteRetrievalPolicy:
    """Retrieves notes PER DECISION from the state in front of the policy.

    The first version set `policy.notes = <every note>` once, which is a global prompt change wearing a
    retrieval mechanism's clothes: the module already had `notes_for()` for compatible-state retrieval
    and nothing called it. This wrapper computes the observable state features at each decision, keeps
    only the notes whose applicability matches THAT state, and records what was retrieved so the receipt
    can show the intervention was actually active rather than assuming it.
    """

    def __init__(self, inner, notes: list[Note], *, limit: int = 3):
        self.inner, self.notes, self.limit = inner, notes, limit
        self.name = f"retrieval({getattr(inner, 'name', type(inner).__name__)})"
        self.history: list[dict] = []

    def __getattr__(self, item):
        return getattr(self.inner, item)

    def choose(self, context, history):
        features = state_features(context, history)
        retrieved = notes_for(self.notes, features, limit=self.limit)
        self.inner.notes = [note.as_dict() for note in retrieved]
        self.history.append({"function": getattr(context, "function", None),
                             "retrieved": [note.id for note in retrieved],
                             "features": {k: features.get(k) for k in
                                          ("error_class", "residual_kind", "verified")}})
        return self.inner.choose(context, history)


def compositions_for(compositions: list[Composition], state: dict) -> list[Composition]:
    usable = [c for c in compositions
              if c.status == "confirmed" and matches(c.when, state)]
    usable.sort(key=lambda c: c.created_at, reverse=True)
    return usable


class ComposedPolicy:
    """Wraps a policy and lets a matching composition reorder its preference.

    The wrapper NEVER invents an action: it watches what the inner policy chose, and if the inner
    choice is not in the composition's preference list while some preferred action is still untried,
    it substitutes the most preferred untried one. Budget accounting is untouched -- the same number of
    actions is taken, in a different order -- which is what makes the transfer comparison attributable
    to the composition rather than to extra work.
    """

    def __init__(self, inner, compositions: list[Composition], state_fn):
        self.inner = inner
        self.compositions = compositions
        self.state_fn = state_fn
        self.name = f"composed({getattr(inner, 'name', type(inner).__name__)})"
        self.substitutions: list[dict] = []

    def choose(self, context, history):
        action, params = self.inner.choose(context, history)
        state = self.state_fn(context, history)
        tried = {getattr(step, "action", None) for step in history}
        for composition in compositions_for(self.compositions, state):
            if action in composition.prefer:
                break
            for candidate in composition.prefer:
                if candidate not in tried and candidate != action:
                    self.substitutions.append({
                        "composition": composition.id, "state": state,
                        "chosen_by_inner": action, "substituted": candidate,
                        "reason": composition.reason})
                    from eval.tool_registry import ACTIONS
                    return candidate, {"composed": composition.id, "instead_of": action}
            break
        return action, params


def state_features(context, history) -> dict:
    """The observable features an applicability block may key on. Deliberately small and mechanical."""
    from eval.tool_agent_probe import compile_state, tried_since_change
    from eval.research_demand import normalize_error, residual_kind

    steps = list(history)
    last_error = ""
    for step in steps:
        detail = getattr(step, "detail", None) or {}
        if detail.get("stderr"):
            last_error = detail["stderr"]
    verification = compile_state(context.candidate, steps)
    return {
        "verified": verification["verified"],
        "exact": verification["exact"],
        "certificate_status": verification["certificate_status"],
        "error_class": normalize_error(last_error),
        "residual_kind": residual_kind(getattr(context, "diff", "") or ""),
        "has_diff": bool(getattr(context, "diff", None)),
        "has_target_dump": bool(getattr(context, "target_dump", None)),
        "no_effect_since_change": sorted(tried_since_change(steps)),
        "function": getattr(context, "function", None),
    }


def intervention_from_finding(finding: dict, *, kind: str, text: str = "", prefer: list | None = None,
                              applicability: dict | None = None, reason: str = "") -> Note | Composition:
    """Turn a confirmed notebook row into a PROPOSED intervention carrying its evidence identity."""
    provenance = {
        "finding_id": finding.get("id"),
        "experiment_id": finding.get("experiment_id"),
        "notebook_row_sha256": _sha(json.dumps(finding, sort_keys=True)),
        "source": finding.get("source", "local-research-notebook"),
        "measured": finding.get("measurements_sha256"),
    }
    if kind == "memory":
        return Note(id=f"note-{finding.get('id')}", text=text or finding.get("title", ""),
                    applicability=applicability or {}, provenance=provenance)
    if kind == "composition":
        composition = Composition(id=f"comp-{finding.get('id')}", when=applicability or {},
                                  prefer=list(prefer or []), reason=reason, provenance=provenance)
        assert_bounded(composition)
        return composition
    raise ValueError(f"unknown intervention kind {kind!r}")


def save(path: Path, items: list) -> dict:
    """Append-only archive with a hard cap; the oldest non-confirmed entries fall off first."""
    path = Path(path)
    rows = [item.as_dict() for item in items]
    if len(rows) > ARCHIVE_CAP:
        confirmed = [r for r in rows if r["status"] == "confirmed"]
        rest = [r for r in rows if r["status"] != "confirmed"]
        rows = confirmed + rest[:max(0, ARCHIVE_CAP - len(confirmed))]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return {"path": str(path), "records": len(rows), "sha256": _sha(path.read_text("utf-8"))}


def load(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(Note(**row) if "text" in row else Composition(**row))
    return out
