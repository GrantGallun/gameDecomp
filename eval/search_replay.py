"""Bounded search policies evaluated against revealed compiler histories only.

This is a local DREAM-RSI adaptation, not the authors' implementation. The
environment owns hidden outcomes; policies receive immutable observations. A
missing edge invalidates a replay instead of pretending to exhaust that branch.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re


def digest(value) -> str:
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def validate_world(world: dict) -> None:
    """Fail closed on incomplete identities, ambiguous lineage or false exacts."""
    context = world["context"]
    if not context.get("task"):
        raise ValueError("missing task identity")
    for name in ("initial_sha256", "target_sha256", "compiler_sha256", "generator_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", context.get(name, "")):
            raise ValueError(f"invalid context {name}")
    depth_limit = world["max_depth"]
    allow_failed = world.get("allow_failed_parents", False)
    if type(allow_failed) is not bool or (allow_failed and world.get("history_kind") != "action-history"):
        raise ValueError("failed-parent expansion requires an explicit action history")
    if type(depth_limit) is not int or depth_limit < 0:
        raise ValueError("invalid depth limit")
    seen, counts = {}, {}
    for node in world["nodes"]:
        identity, parent = node["id"], node["parent"]
        if identity in seen:
            raise ValueError("duplicate node identity")
        if digest(node["source"]) != node["source_sha256"]:
            raise ValueError("source hash mismatch")
        ordinal = node["ordinal"]
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError("invalid child ordinal")
        if parent is None:
            if seen or identity != "root" or ordinal != 0:
                raise ValueError("world must have one initial root")
            if node["source_sha256"] != context["initial_sha256"]:
                raise ValueError("initial source mismatch")
        else:
            if parent not in seen or ordinal != counts.get(parent, 0) or identity != f"{parent}/{ordinal}":
                raise ValueError("missing parent or noncontiguous child stream")
            prior = seen[parent]["verdict"]
            if (not prior["compiled"] and not allow_failed) or prior.get("error") or prior["exact"] or identity.count("/") > depth_limit:
                raise ValueError("ineligible parent")
            counts[parent] = ordinal + 1
        verdict = node["verdict"]
        if type(verdict["compiled"]) is not bool or type(verdict["exact"]) is not bool:
            raise ValueError("invalid boolean verdict")
        score = verdict["score"]
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("invalid score")
        seconds = verdict.get("seconds", 0)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid compile cost")
        if verdict["exact"]:
            certificate = verdict.get("verification") or {}
            source_hash = certificate.get("candidate_source_sha256", certificate.get("source_sha256"))
            if (not verdict["compiled"] or certificate.get("exact") is not True
                    or source_hash != node["source_sha256"]
                    or certificate.get("schema_version") != 1
                    or certificate.get("kind") != "mips_object_section_certificate"
                    or certificate.get("status") != "object_sections_exact"
                    or certificate.get("target_sha256") != context["target_sha256"]
                    or not re.fullmatch(r"[0-9a-f]{64}", certificate.get("candidate_sha256", ""))
                    or (verdict.get("frontend") or {}).get("passed") is not True
                    or (verdict.get("frontend") or {}).get("source_sha256") != certificate.get("source_sha256")
                    or not re.fullmatch(r"[0-9a-f]{64}", certificate.get("source_sha256", ""))):
                raise ValueError("exact requires a source-bound certificate and frontend pass")
        seen[identity] = node
    closed = world["closed"]
    if len(set(closed)) != len(closed) or any(identity not in seen for identity in closed):
        raise ValueError("invalid exhausted stream")


def save_world(world: dict, path) -> None:
    validate_world(world)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "world": world, "sha256": digest(world)}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def load_world(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("sha256") != digest(data["world"]):
        raise ValueError("world checksum or schema mismatch")
    validate_world(data["world"])
    return data["world"]


def merge_worlds(worlds: list[dict]) -> dict:
    """Union compatible streams; receipt/timing differences retain first provenance.

    Original worlds remain the audit records for each actual run. A merged world's
    parent IDs identify source states, not a reconstruction of SQLite row order.
    """
    if not worlds:
        raise ValueError("no worlds to merge")
    merged = deepcopy(worlds[0])
    by_id, closed_counts = {}, {}
    for world in worlds:
        validate_world(world)
        if world.get("history_kind") == "action-history":
            raise ValueError("action history requires proposal-aware replay; streams cannot be merged")
        if world["context"] != merged["context"] or world["max_depth"] != merged["max_depth"]:
            raise ValueError("world context mismatch")
        for node in world["nodes"]:
            old = by_id.get(node["id"])
            if old is not None:
                keys = ("source_sha256", "parent", "ordinal", "label", "family")
                verdict_keys = ("compiled", "exact", "score", "diff")
                if (any(old[k] != node[k] for k in keys)
                        or any(old["verdict"].get(k) != node["verdict"].get(k) for k in verdict_keys)):
                    raise ValueError("conflicting observation for the same expansion")
            else:
                by_id[node["id"]] = deepcopy(node)
        for identity in world["closed"]:
            count = sum(n["parent"] == identity for n in world["nodes"])
            if identity in closed_counts and count != closed_counts[identity]:
                raise ValueError("conflicting exhausted stream")
            closed_counts[identity] = count
    for identity, count in closed_counts.items():
        if count != sum(n["parent"] == identity for n in by_id.values()):
            raise ValueError("conflicting continuation after exhaustion")
    merged["nodes"] = sorted(by_id.values(), key=lambda n: (n["id"].count("/"), tuple(map(int, n["id"].split("/")[1:]))))
    merged["closed"] = sorted(closed_counts)
    validate_world(merged)
    return merged


@dataclass(frozen=True)
class Observation:
    id: str
    depth: int
    score: float
    expanded: int


@dataclass(frozen=True)
class Policy:
    name: str
    mode: str
    quantum: float = 2.0

    def __post_init__(self):
        if self.mode not in {"breadth", "greedy", "balanced", "depth"}:
            raise ValueError("unknown policy mode")
        if not self.name or not math.isfinite(self.quantum) or self.quantum <= 0:
            raise ValueError("invalid policy identity or quantum")

    def choose(self, observations: tuple[Observation, ...]) -> str | None:
        if not observations:
            return None
        if self.mode == "breadth":
            key = lambda o: (o.depth, o.expanded)
        elif self.mode == "greedy":
            key = lambda o: (-o.score, o.expanded, o.depth)
        elif self.mode == "depth":
            # Descendants inherit a cost instead of resetting a visit penalty.
            # Small local score gains cannot monopolize increasingly deep work.
            key = lambda o: (-(o.score - self.quantum * o.depth), o.depth, o.expanded)
        else:
            key = lambda o: (-(o.score - self.quantum * o.expanded), o.depth)
        return min(observations, key=key).id


class MissingHistory(Exception):
    """The requested continuation was not observed during collection."""


class Replay:
    def __init__(self, world: dict):
        validate_world(world)
        if world.get("history_kind") == "action-history":
            raise ValueError("action history requires proposal-aware replay")
        self.world = deepcopy(world)
        self.max_depth = world["max_depth"]
        self.nodes = {n["id"]: n for n in self.world["nodes"]}
        self.positions = {}

    def start(self):
        if "root" not in self.nodes:
            raise MissingHistory("root")
        return self.nodes["root"]

    def expand(self, parent):
        ordinal = self.positions.get(parent, 0)
        identity = f"{parent}/{ordinal}"
        if identity not in self.nodes:
            if parent in self.world["closed"]:
                return None
            raise MissingHistory(identity)
        self.positions[parent] = ordinal + 1
        return self.nodes[identity]


def run(environment, policy, budget: int) -> dict:
    """One compile per revealed node, including the root and failed builds.

    Environments are single-use. Exhaustion probes do not cost a compiler call;
    absence of historical coverage stops the entire replay as incomplete.
    """
    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a nonnegative integer")
    result = {"compiles": 0, "exact": False, "best_score": 0.0, "baseline_score": 0.0,
              "best_id": None, "trace": [], "stop": "budget", "complete": True,
              "recorded_seconds": 0.0}
    if budget == 0:
        return result
    try:
        root = environment.start()
    except MissingHistory:
        return {**result, "stop": "unsupported", "complete": False}
    observed, expanded, closed = {}, {}, set()

    def reveal(node):
        identity, verdict = node["id"], node["verdict"]
        if identity in observed:
            raise ValueError("environment revealed a duplicate node")
        observed[identity] = node
        result["trace"].append(identity)
        result["compiles"] += 1
        result["recorded_seconds"] += verdict.get("seconds", 0)
        if result["best_id"] is None or verdict["exact"] or (
                verdict["compiled"] and verdict["score"] > result["best_score"]):
            result["best_id"], result["best_score"] = identity, verdict["score"]
        result["exact"] = result["exact"] or verdict["exact"]

    reveal(root)
    result["baseline_score"] = root["verdict"]["score"]
    while result["compiles"] < budget and not result["exact"]:
        visible = tuple(Observation(identity, identity.count("/"), node["verdict"]["score"], expanded.get(identity, 0))
                        for identity, node in observed.items()
                        if identity not in closed and node["verdict"]["compiled"]
                        and identity.count("/") < environment.max_depth)
        if not visible:
            result["stop"] = "exhausted"
            break
        parent = policy.choose(visible)
        if parent is None:
            result["stop"] = "policy-stop"
            break
        if parent not in {o.id for o in visible}:
            raise ValueError("policy selected an unobserved or ineligible parent")
        try:
            child = environment.expand(parent)
        except MissingHistory:
            result.update(stop="unsupported", complete=False)
            break
        if child is None:
            closed.add(parent)
        else:
            expanded[parent] = expanded.get(parent, 0) + 1
            reveal(child)
    if result["exact"]:
        result["stop"] = "exact"
    return result


def select_policy(worlds: list[dict], policies: list[Policy], incumbent: Policy, *, budget: int) -> dict:
    """Select from a finite policy family; ties and invalid baselines retain incumbent."""
    if not worlds or budget <= 0:
        raise ValueError("selection needs nonempty worlds and a positive budget")
    tasks = [w["context"]["task"] for w in worlds]
    if len(set(tasks)) != len(tasks):
        raise ValueError("calibration tasks must be unique")
    candidates = [incumbent] + [p for p in policies if p != incumbent]
    if len({p.name for p in candidates}) != len(candidates):
        raise ValueError("duplicate policy names")
    rows, best_key, selected = [], None, incumbent
    for index, policy in enumerate(candidates):
        results = [run(Replay(w), policy, budget) for w in worlds]
        eligible = all(r["complete"] for r in results)
        exacts = sum(r["exact"] for r in results)
        gain = round(sum(r["best_score"] - r["baseline_score"] for r in results), 9)
        compiles = sum(r["compiles"] for r in results)
        key = (exacts, gain, -compiles)
        if index == 0:
            best_key = key if eligible else None
        elif best_key is not None and eligible and key > best_key:
            best_key, selected = key, policy
        rows.append({**asdict(policy), "eligible": eligible, "exacts": exacts,
                     "score_gain_sum": gain, "compiles": compiles, "results": results})
    return {"selected": selected.name, "policy": asdict(selected), "budget": budget,
            "policy_implementation_sha256": digest(Path(__file__).read_text(encoding="utf-8")),
            "selection_valid": rows[0]["eligible"], "policies": rows,
            "world_sha256": [digest(w) for w in worlds],
            "objective": "exact count, then score gain sum, then fewer compiles; incumbent wins ties"}
