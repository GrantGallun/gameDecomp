"""A repair STATE is what the model was actually asked, not which function it named.

WHY THIS IS NOT KEYED ON THE FUNCTION
-------------------------------------
The obvious key for grouping repair attempts is the function name, and it is wrong. Two records for
`syn_loop_for_0` are different tasks if the TARGET differs, if the PARENT candidate differs, if the
compiler FEEDBACK differs, if the BUILD differs, or if the PROMPT differs -- and a child verified
against one of them is evidence about that one only. Gluing such records together because the id
matches attaches a child to an unrelated parent, and it substitutes one task's prompt for another's.
That is the failure this module exists to make impossible: `state_id` is a hash of the whole
conditioning tuple, and `assert_states_are_not_functions` refuses a grouping that collapsed to ids.

THE FOUR OUTCOMES, KEPT APART
-----------------------------
The brief's first requirement, and the reason this file has more than a grouping function:

  1. `certified-alternative`  source that passes THIS target's exactness certificate. A full
     positive on its own, even when it differs from the recorded child -- compilation is many-to-one
     and the recorded child is one member of an equivalence class. Compiling, looking plausible, or
     scoring well on similarity is NOT this.
  2. `certified-trajectory`   an attempt that is not itself exact but has an exact DESCENDANT. The
     route reached a match; the node did not.
  3. `unresolved`             neither exact, nor known to lead anywhere. A branch that stopped.
  4. `failed-redundant`       DEMONSTRATED unsuccessful (it did not compile) or redundant (a
     cosmetic duplicate of a sibling with no exact descendant).

A DROP IN SIMILARITY IS NOT A FAILURE. An intermediate edit that lowers instruction similarity can
enable an eventual match, so `classify` puts a score decrease in `unresolved` unless something else
demonstrates the branch is dead. Nothing here turns a score drop into a rejected preference example.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from eval.repair_archive import novelty_key

# Bumped when the conditioning tuple changes. Two records with different prompt versions are not the
# same state even if every other field matches.
STATE_SCHEMA_VERSION = 1


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def state_key(record: dict) -> dict[str, str]:
    """The conditioning tuple: target, parent, feedback, build context, prompt context.

    Every field is hashed rather than referenced so a state identity cannot be changed by editing
    the record it came from.
    """
    target = record.get("target") or {}
    source = record.get("input") or {}
    recipe = ((record.get("provenance") or {}).get("compiler_recipe") or {})
    assembly = source.get("assembly") or ""
    feedback = source.get("feedback") or {}
    return {
        # The target's identity: the code image AND its disassembly, so a target whose relocations
        # differ is a different target (the certificate compares relocations, not just `.text`).
        "target": _sha(f"{target.get('code_sha256') or ''}\x00{target.get('asm_sha256') or ''}"),
        "parent": _sha(source.get("candidate") or ""),
        "feedback": _sha(f"{feedback.get('kind') or ''}\x00{feedback.get('text') or ''}"),
        "build": str(recipe.get("command_sha256") or ""),
        "prompt": _sha(f"v{STATE_SCHEMA_VERSION}\x00{assembly}"),
    }


def state_id(record: dict) -> str:
    """A stable identity for the repair state, over the whole conditioning tuple."""
    key = state_key(record)
    body = "\x00".join(f"{name}={key[name]}" for name in sorted(key))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def assert_states_are_not_functions(records: list[dict], states: dict[str, list[dict]]) -> None:
    """Refuse a grouping that is really "same function name".

    A state that contains records with DIFFERENT conditioning tuples is a bug, and the cheap way to
    catch it is to check that every state's members agree on their key. This is a real check, not a
    formality: a loader that grouped by `function` would produce exactly this shape and would look
    perfectly healthy from outside.
    """
    for sid, members in states.items():
        keys = {json.dumps(state_key(m), sort_keys=True) for m in members}
        if len(keys) > 1:
            functions = sorted({m.get("function") or "?" for m in members})
            raise ValueError(
                f"state {sid} holds {len(members)} records with {len(keys)} different conditioning "
                f"tuples (functions: {functions[:4]}). A child may not be attached to a parent it "
                f"was not verified against.")


def group_by_state(records: list[dict]) -> dict[str, list[dict]]:
    """Verified records grouped by the state they actually condition on."""
    states: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if not (record.get("child") or {}).get("exact"):
            continue
        states[state_id(record)].append(record)
    assert_states_are_not_functions(records, states)
    return dict(states)


def verified_children(state_records: list[dict], variants: list[dict] | None = None) -> list[dict]:
    """The certified alternatives of one state, deduplicated by NOVELTY, not by bytes.

    Two children that differ only in whitespace or identifier names are one approach spelled twice;
    they must not count as two, or a state with many cosmetic variants would outvote a state with a
    couple of genuinely different solutions.

    `variants` are additional certificate-verified spellings keyed by `task_id` -- the output of
    `eval.target_augment`. They are joined on the RECORD, never on the function name, so a variant
    can only ever be attached to the state whose task it was verified against. Note that on the
    synthetic dataset a state is otherwise a SINGLETON: the recorded child is one row, and the
    multiple-children case comes precisely from these joined variants.
    """
    wanted = {record.get("task_id") for record in state_records}
    candidate_rows: list[dict] = []
    for record in state_records:
        child = record.get("child") or {}
        source = child.get("source_c") or ""
        if not child.get("exact") or not source:
            continue
        candidate_rows.append({
            "task_id": record.get("task_id"),
            "function": record.get("function"),
            "source": source,
            "certificate": child.get("certificate") or {},
            "outcome": child.get("outcome"),
            "rewrite": "recorded-child",
            "build": (state_key(record) or {}).get("build"),
        })
    for row in variants or []:
        if row.get("task_id") in wanted and (row.get("source") or "").strip():
            candidate_rows.append({
                "task_id": row.get("task_id"),
                "function": row.get("function"),
                "source": row["source"],
                "certificate": row.get("certificate") or {},
                "outcome": "verified-variant",
                "rewrite": row.get("rewrite") or "unknown",
                "build": None,
            })

    seen: dict[str, dict] = {}
    for row in candidate_rows:
        key = novelty_key(row["source"])
        if key in seen:
            # Cosmetic duplicate. Recorded on the surviving entry so the receipt can show how much
            # of the apparent diversity was spelling rather than approach.
            seen[key].setdefault("cosmetic_duplicates", []).append(row["rewrite"])
            continue
        seen[key] = {**row, "novelty_key": key, "record_id": row["task_id"],
                     "cosmetic_duplicates": []}
    return list(seen.values())


# --- the four outcomes ---------------------------------------------------------

CERTIFIED_ALTERNATIVE = "certified-alternative"
CERTIFIED_TRAJECTORY = "certified-trajectory"
UNRESOLVED = "unresolved"
FAILED_REDUNDANT = "failed-redundant"


def classify(*, exact: bool, compiled: bool, has_exact_descendant: bool = False,
             duplicate_of_sibling: bool = False, demonstrated_dead: bool = False) -> str:
    """One attempt -> one of the four outcomes. Order matters and is deliberate.

    `exact` must come from a certificate verdict supplied by the caller. This function never
    compiles and never infers exactness from a score.
    """
    if exact:
        return CERTIFIED_ALTERNATIVE
    if has_exact_descendant:
        return CERTIFIED_TRAJECTORY
    # Failure must be DEMONSTRATED: a compiler refusal, an explicit dead-end marker, or a cosmetic
    # duplicate of a sibling that itself leads nowhere. A mere score drop is none of those.
    if not compiled or demonstrated_dead or duplicate_of_sibling:
        return FAILED_REDUNDANT
    return UNRESOLVED


def trajectory_labels(nodes: list[dict], edges: list[tuple[str, str]]) -> dict[str, dict]:
    """Label every node in a parent/child attempt graph. Evidence, not credit.

    A node with an exact descendant is `certified-trajectory` -- the ROUTE reached a match. That is
    an observation about the graph. It is NOT a claim that every edit on the path was necessary, that
    the intermediate steps were good, or that the ancestors are equivalent to the solution. Nothing
    here relabels an ancestor as an exact solution, and cycles cannot loop because the walk is
    memoised over the descendant set.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for parent, child in edges:
        children[parent].append(child)

    by_id = {node["id"]: node for node in nodes}
    memo: dict[str, bool] = {}

    def reaches_exact(node_id: str, seen: frozenset[str] = frozenset()) -> bool:
        if node_id in memo:
            return memo[node_id]
        if node_id in seen:                       # a cycle proves nothing; do not recurse forever
            return False
        node = by_id.get(node_id) or {}
        if node.get("exact"):
            memo[node_id] = True
            return True
        found = any(reaches_exact(child, seen | {node_id}) for child in children.get(node_id, []))
        memo[node_id] = found
        return found

    out: dict[str, dict] = {}
    for node in nodes:
        node_id = node["id"]
        exact = bool(node.get("exact"))
        descendant = reaches_exact(node_id) if not exact else False
        label = classify(exact=exact, compiled=bool(node.get("compiled", True)),
                         has_exact_descendant=descendant,
                         duplicate_of_sibling=bool(node.get("duplicate_of_sibling")),
                         demonstrated_dead=bool(node.get("demonstrated_dead")))
        out[node_id] = {
            "label": label,
            "score": node.get("score"),
            "exact_descendant": descendant,
            "note": ("the route reached a certified match; the node itself is not a solution"
                     if label == CERTIFIED_TRAJECTORY else ""),
        }
    return out


# --- weighting -----------------------------------------------------------------

def balanced_weights(records: list[dict],
                     variants: list[dict] | None = None) -> dict[str, dict[str, float]]:
    """Per-child training weights: functions balanced, states balanced, novelty classes balanced.

    THE ACTUAL SCHEME, stated because "sums to one per state" alone does not say what happens across
    states:

      1. every FUNCTION gets 1 / n_functions. A function with ten verified children therefore counts
         the same as a function with one, which is the "does not disproportionately weight one
         function" requirement;
      2. within a function, the weight is split equally across that function's STATES, so a state is
         not drowned out by a sibling state for the same function;
      3. within a state, the weight is split equally across its NOVELTY CLASSES -- cosmetic
         duplicates share one class, so they cannot inflate weight at all;
      4. one representative source per novelty class carries the whole class weight, keyed by
         `(task_id, novelty_key)` so the trainer can match an example's completion to its weight.

    The result is a map `task_id -> {novelty_key: weight}` whose values sum to 1.0 over the dataset.
    Nothing here claims these weights make cross-entropy preserve diversity or reach solutions that
    are absent from the data; they only stop the data that IS present from being weighted by accident
    of how many cosmetic variants happened to be recorded.
    """
    states = group_by_state(records)
    if not states:
        return {}
    by_function: dict[str, list[str]] = defaultdict(list)
    for sid, members in states.items():
        by_function[members[0].get("function") or "?"].append(sid)

    out: dict[str, dict[str, float]] = {}
    n_functions = len(by_function)
    for function, state_ids in by_function.items():
        per_function = 1.0 / n_functions
        per_state = per_function / len(state_ids)
        for sid in state_ids:
            children = verified_children(states[sid], variants)
            if not children:
                continue
            per_child = per_state / len(children)
            for child in children:
                out.setdefault(child["task_id"], {})[child["novelty_key"]] = per_child
    return out


def weight_summary(records: list[dict], variants: list[dict] | None = None) -> dict:
    """A receipt for the weighting: counts, totals and the checks an auditor would want."""
    states = group_by_state(records)
    weights = balanced_weights(records, variants)
    function_of = {m.get("task_id"): (m.get("function") or "?") for m in records}
    per_function: dict[str, float] = defaultdict(float)
    for task_id, classes in weights.items():
        per_function[function_of.get(task_id, "?")] += sum(classes.values())
    multi = {sid: len(verified_children(m, variants)) for sid, m in states.items()}
    collapsed = sum(len(c.get("cosmetic_duplicates") or [])
                    for m in states.values() for c in verified_children(m, variants))
    return {
        "records": len(records),
        "variants_supplied": len(variants or []),
        "states": len(states),
        "functions": len(set(function_of.values())),
        "states_with_multiple_children": sum(1 for n in multi.values() if n > 1),
        "max_children_per_state": max(multi.values()) if multi else 0,
        "distinct_children_total": sum(multi.values()),
        "cosmetic_duplicates_collapsed": collapsed,
        "total_weight": round(sum(sum(c.values()) for c in weights.values()), 6),
        "weight_per_function_min": round(min(per_function.values()), 6) if per_function else 0.0,
        "weight_per_function_max": round(max(per_function.values()), 6) if per_function else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", type=Path, required=True, help="a tasks.jsonl")
    ap.add_argument("--split", default="train")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    records = [json.loads(line) for line in
               args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = [r for r in records if args.split is None or r.get("split") == args.split]
    summary = weight_summary(records)
    states = group_by_state(records)
    print(json.dumps(summary, indent=2))
    for sid, members in sorted(states.items(), key=lambda kv: -len(kv[1]))[:5]:
        print(f"  state {sid[:12]}  function={members[0].get('function')}  "
              f"records={len(members)}  distinct_children={len(verified_children(members))}")
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "states": {
            sid: [c["task_id"] for c in verified_children(m)] for sid, m in states.items()}},
            indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
