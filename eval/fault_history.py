"""Track one frozen panel's fault histogram across rounds, so "a couple of problems" is falsifiable.

WHY THIS EXISTS. Every round of this work has ended the same way: a diagnostic list says a handful of
things are wrong, they get fixed, and the next round's diagnostic list says a handful of things are wrong.
That reads as no progress, and it is not evidence of any -- for four separate reasons, all of which are
properties of the measurement rather than of the repairs:

  1. A DIAGNOSTIC LIST IS A FRONTIER, NOT A DISTANCE. A parse error at line 10 means the compiler never
     reaches line 50; fix line 10 and line 50 appears. It was always there. The set of reported faults is
     the leading edge of the fault set and cannot be the fault set.
  2. THE SAME IS TRUE, AND WORSE, OF THE INSTRUCTION DIFF. A byte-exactness diff is one global constraint
     solution, not N independent faults to subtract: change one register assignment and every downstream
     assignment shifts. Fixing a register fault generates register faults.
  3. THE CLASSES ARE SYMPTOMS. One root cause -- a wrong field width -- sprays across width, offset and
     register-allocation classes; one class covers many unrelated causes. Class count and edit count are
     only loosely coupled.
  4. THE POPULATION MOVES BETWEEN REPORTS unless it is frozen. A round that draws a new cohort reports the
     new cohort's "couple of problems" whatever happened to the old one.

So the number that can be falsified is not the count of problems. It is, on a FIXED panel:
  * the distribution of class-set SIZES (a frame of one-class states is close; a frame of five-class
    states is not, and both look the same in a per-class total),
  * which classes were MASKED rather than created -- a class that was absent at step 0 and present later
    was hidden behind something the sequence repaired,
  * for each class, how many NEW classes appear once it is repaired: the masking fan-out. That is the
    class that "breaks everything else", and it is the one worth pausing for,
  * and, between two rounds, which states crossed to exact and whether the fault distribution changed
    basin at all -- `regalloc -> structural` is progress; a smaller regalloc count inside the same basin
    usually is not.

GUARD RAILS, because each of these has bitten this project already:
  * a class count is an OBSERVATION. Nothing here is a distance label and nothing here should become a
    reward.
  * a missing field is reported as missing, never as zero. Receipts written before `class_counts` existed
    are read for what they have and the gap is named.
  * the panel must be the same panel. Two receipts whose function sets differ are reported as incomparable
    rather than diffed, which is the mistake that produced a "22.5% -> 10.0% regression" that was a change
    of frame.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def states(payload: dict) -> dict:
    return {row["function"]: row for row in payload.get("rows") or []}


def final_classes(row: dict):
    """The state's class set at the end of its sequence, or None when this receipt never measured one.

    NOT `[]`. An older receipt has no `sequence.frontend_classes` and carries the same observation in its
    step trace instead; a receipt that has neither says nothing about the state, and reading "unmeasured" as
    "no faults" turns every unmeasured state into a clean one. The first version of this function returned
    `[]`, and the comparison below reported every class in the frame as ADDED by the later round -- the
    exact shape of error this project keeps paying for, caught here only because the numbers looked absurd.
    """
    sequence = row.get("sequence") or {}
    if sequence.get("frontend_classes") is not None:
        return sorted(set(sequence["frontend_classes"]))
    steps = trace(row)
    if steps and steps[-1].get("classes") is not None:
        return sorted(set(steps[-1]["classes"]))
    return None


def trace(row: dict) -> list:
    return row.get("diagnostic_trace") or []


def histogram(payload: dict) -> dict:
    """One frozen panel, described by its fault shape rather than by its fault total."""
    rows = list((payload.get("rows") or []))
    per_class = Counter()
    set_sizes = Counter()
    error_instances = Counter()
    revealed = Counter()
    has_counts, has_trace, unmeasured = 0, 0, 0
    masking_fanout: dict = {}
    masking_attempted: dict = {}
    for row in rows:
        final = final_classes(row)
        if final is None:
            # COUNTED, NOT ASSUMED AWAY. A histogram over the states a receipt measured is not a histogram
            # over the frame, and the difference is stated rather than silently divided out.
            unmeasured += 1
            continue
        set_sizes[len(final)] += 1
        for label in final:
            per_class[label] += 1
        steps = trace(row)
        if steps:
            has_trace += 1
            start = set(steps[0].get("classes") or [])
            appeared = set()
            for entry in steps[1:]:
                appeared |= set(entry.get("classes") or [])
            new = appeared - start
            for label in new:
                revealed[label] += 1
            # THE MASKING FAN-OUT: repairing a class that was there at the start let this many new classes
            # come into view. A class with a high fan-out is the one that hides the rest, and it is the one
            # worth pausing for.
            #
            # RECORDED TWO WAYS, because they answer different questions and a single number would hide one
            # of them. `cleared` counts states where the class is GONE from the final candidate -- the repair
            # worked and this is what it uncovered. `attempted` counts every state where the class was
            # present at the start, whatever became of it, because the repair ATTEMPT is what brings the
            # next fault into view: a class that hides four others and survives being repaired is still the
            # blocker, and dropping it from the ranking is how a tool reports "nothing to see" about the one
            # thing worth fixing.
            for label in start:
                masking_attempted.setdefault(label, []).append(len(new))
            for label in start - set(final):
                masking_fanout.setdefault(label, []).append(len(new))
        for entry in steps:
            if entry.get("class_counts"):
                has_counts += 1
                for label, count in entry["class_counts"].items():
                    error_instances[label] += int(count)
    one = set_sizes.get(1, 0)
    return {
        "states": len(rows),
        "states_measured": len(rows) - unmeasured,
        "states_with_no_class_set_in_this_receipt": unmeasured,
        "class_set_sizes": {str(size): set_sizes[size] for size in sorted(set_sizes)},
        "states_with_one_class": one,
        "states_with_two_classes": set_sizes.get(2, 0),
        "states_with_three_or_more": sum(count for size, count in set_sizes.items() if size >= 3),
        "states_with_no_class": set_sizes.get(0, 0),
        "states_per_class": dict(per_class.most_common()),
        "error_instances_per_class_across_the_trace": dict(error_instances.most_common()),
        "classes_masked_then_revealed": dict(revealed.most_common()),
        "masking_fanout": {label: {"states": len(values),
                                   "new_classes_revealed": sum(values),
                                   "mean": round(sum(values) / len(values), 2)}
                           for label, values in sorted(masking_fanout.items(),
                                                       key=lambda kv: -sum(kv[1]))},
        "masking_fanout_any_attempt": {label: {"states": len(values),
                                               "new_classes_revealed": sum(values),
                                               "mean": round(sum(values) / len(values), 2)}
                                       for label, values in sorted(masking_attempted.items(),
                                                                   key=lambda kv: -sum(kv[1]))},
        "coverage": {"states_with_a_step_trace": has_trace,
                     "trace_entries_carrying_class_counts": has_counts},
        "note": ("`error_instances` counts ERROR LINES classified, not independent faults; one root cause "
                 "sprays across classes and one class covers many causes. `states_with_one_class` is the "
                 "closest thing here to a distance and it is still only a frontier measure."),
    }


def compare(before: dict, after: dict) -> dict:
    """Two rounds on the same panel. Refuses to diff different panels."""
    b, a = states(before), states(after)
    if set(b) != set(a):
        return {"comparable": False,
                "reason": ("the two receipts do not describe the same panel: "
                           f"{len(set(b) ^ set(a))} function(s) differ. A rate across two frames carries "
                           f"membership drift, and a per-class delta across two frames is not a delta."),
                "only_before": sorted(set(b) - set(a))[:10],
                "only_after": sorted(set(a) - set(b))[:10]}
    moved, removed_total, added_total = [], Counter(), Counter()
    unmeasured = []
    for name in sorted(b):
        before_classes, after_classes = final_classes(b[name]), final_classes(a[name])
        if before_classes is None or after_classes is None:
            # ONE SIDE NEVER MEASURED IT. Diffing against a missing observation is how an unmeasured state
            # becomes a state that "lost every class", so it is reported instead of diffed.
            unmeasured.append(name)
            continue
        before_set, after_set = set(before_classes), set(after_classes)
        if before_set == after_set:
            continue
        gone, new = sorted(before_set - after_set), sorted(after_set - before_set)
        removed_total.update(gone)
        added_total.update(new)
        moved.append({"function": name,
                      "before_size": len(before_set), "after_size": len(after_set),
                      "classes_removed": gone, "classes_added": new,
                      # NAMED, because the two mean opposite things and look identical in a count.
                      "appeared_after_being_absent": new,
                      "basin_shift": _basin(gone, new)})
    before_ok = sum(1 for row in b.values() if (row.get("sequence") or {}).get("exact"))
    after_ok = sum(1 for row in a.values() if (row.get("sequence") or {}).get("exact"))
    return {"comparable": True, "states": len(b), "states_whose_class_set_moved": len(moved),
            "states_unchanged": len(b) - len(moved) - len(unmeasured),
            "states_this_comparison_could_not_measure": len(unmeasured),
            "unmeasured": unmeasured[:20],
            "classes_removed_in_total": dict(removed_total.most_common()),
            "classes_added_in_total": dict(added_total.most_common()),
            "exact_before": before_ok, "exact_after": after_ok,
            "moved": moved[:40],
            "note": ("a class in `classes_added` was not necessarily created by a repair: it may have been "
                     "masked by the fault that was fixed. Use the step trace to tell those apart.")}


# WHAT A BASIN SHIFT IS. The measured shape of this project's residuals: 91.8% of setbacks are
# regalloc -> regalloc and only 3.25% change class, with an escape rate of 0.375 against 0.036. So a repair
# that moves a state from an allocation residual to a structural one is progress even when the score barely
# moves, and a smaller allocation count inside the same basin usually is not.
_BASIN_OF = {
    "member-on-typed-pointer": "declaration",
    "undeclared-member": "declaration",
    "unknown-type-name": "declaration",
    "undeclared-identifier": "declaration",
    "undeclared-function": "declaration",
    "redeclaration/conflict": "declaration",
    "incompatible-pointer": "declaration",
    "parameter-declarator": "syntax",
    "expected-identifier": "syntax",
    "other-syntax": "syntax",
    "unclassified": "unclassified",
}


def _basin(gone: list, new: list) -> dict:
    gone_basins = {_BASIN_OF.get(label, "other") for label in gone}
    new_basins = {_BASIN_OF.get(label, "other") for label in new}
    return {"left": sorted(gone_basins), "entered": sorted(new_basins),
            "changed_basin": bool(gone_basins and new_basins and gone_basins != new_basins)}


def blocker_graph(payload: dict) -> dict:
    """WHICH BLOCKER CLEARS WHICH, and therefore what order to repair them in.

    A fan-out COUNT says how many faults a repair uncovers. It does not say which, so it cannot tell you
    that `unknown-type-name` gates `undeclared-member` while `undeclared-identifier` gates something else.
    The edges come from the step trace, and the evidence is deliberately narrow: a class CLEARED in the same
    step that another class appears FOR THE FIRST TIME. Anything weaker (end-to-end set difference) would
    attribute a fault to a repair that happened five steps earlier.

    Correlation, not proof: two classes can share a cause, or an action can clear one class and reveal
    another for unrelated reasons. The step granularity is what makes it useful rather than decorative --
    the edge carries the ACTION that did both, so "which action clears X and what does it uncover" is a
    measurement, and `depends_on`/`unlocks` are read off it.

    The repair ORDER is derived, not chosen: depth first (a class visible at step 0 has nothing in front of
    it), then unlock weight inside a depth. A class deeper in the trace cannot be the first thing to fix,
    because the states that carry it do not show it until something else is gone.
    """
    rows = list((payload.get("rows") or []))
    edges: Counter = Counter()
    by_action: dict = {}
    first_seen: dict = {}
    entry_states: Counter = Counter()
    cleared_states: Counter = Counter()
    for row in rows:
        steps = trace(row)
        if len(steps) < 2:
            continue
        seen = set(steps[0].get("classes") or [])
        for label in seen:
            entry_states[label] += 1
            first_seen.setdefault(label, 0)
        cleared_here = set()
        for index in range(len(steps) - 1):
            before = set(steps[index].get("classes") or [])
            after = set(steps[index + 1].get("classes") or [])
            action = str(steps[index + 1].get("after") or "?")
            cleared, fresh = before - after, (after - seen)
            cleared_here |= cleared
            for source in cleared:
                for target in fresh:
                    edges[(source, target)] += 1
                    by_action.setdefault(action, Counter())[(source, target)] += 1
            for target in fresh:
                # FIRST appearance wins: a class seen at step 0 is an entry point whatever happens later.
                first_seen.setdefault(target, index + 1)
            seen |= after
        # COUNTED PER STATE, not per step: a class cleared at step 2 and again at step 5 is one state where
        # this class was dealt with, and dividing by step-events would flatter it.
        for label in cleared_here:
            cleared_states[label] += 1

    unlocks: dict = {}
    depends_on: dict = {}
    for (source, target), weight in edges.items():
        entry = unlocks.setdefault(source, {"classes": set(), "weight": 0})
        entry["classes"].add(target)
        entry["weight"] += weight
        depends_on.setdefault(target, {})[source] = depends_on.setdefault(target, {}).get(source, 0) + weight

    # CYCLES ARE REPORTED, NOT SMOOTHED AWAY. Two classes can each reveal the other in different states, and
    # a strict topological order does not exist when they do. The depth layering below is still usable --
    # it comes from WHEN a class first appears, not from the edges -- but a reader has to know the graph is
    # not a DAG before reading an order off it.
    cycles = sorted({tuple(sorted((source, target))) for (source, target) in edges
                     if (target, source) in edges})

    def summarise(table: dict, key: str) -> dict:
        return {name: {"classes": len(value["classes"]), "weight": value["weight"],
                       "states_cleared_in": cleared_states.get(name, 0),
                       # THE PER-FIX YIELD. Raw weight rewards a class that is simply present in many
                       # states; this answers "when this class is actually cleared, how much opens up".
                       "unlocked_per_clear": (round(value["weight"] / cleared_states[name], 2)
                                              if cleared_states.get(name) else None),
                       "unlocks": sorted(value["classes"])}
                for name, value in sorted(table.items(), key=lambda kv: -kv[1]["weight"])}

    # THE ORDER. Depth is the step at which a class first becomes visible; inside a depth, the class that
    # unlocks the most goes first. A class with no depth (never seen in any trace) is left out rather than
    # placed at 0.
    depth_of: dict = dict(first_seen)
    layers: dict = {}
    for label, depth in depth_of.items():
        layers.setdefault(depth, []).append(label)
    order = []
    for depth in sorted(layers):
        ranked = sorted(layers[depth],
                        key=lambda name: (-(unlocks.get(name, {}).get("weight", 0)
                                            / max(1, cleared_states.get(name, 0))),
                                          -unlocks.get(name, {}).get("weight", 0),
                                          -entry_states.get(name, 0), name))
        order.append({"depth": depth,
                      "classes": [{"class": name,
                                   "unlocks_classes": len(unlocks.get(name, {}).get("classes", ())),
                                   "unlock_weight": unlocks.get(name, {}).get("weight", 0),
                                   "states_cleared_in": cleared_states.get(name, 0),
                                   "unlocked_per_clear": (
                                       round(unlocks[name]["weight"] / cleared_states[name], 2)
                                       if unlocks.get(name, {}).get("weight") and cleared_states.get(name)
                                       else None),
                                   "unlocks": sorted(unlocks.get(name, {}).get("classes", ())),
                                   "gated_by": sorted(depends_on.get(name, {})),
                                   "states_where_it_is_visible_at_step_0": entry_states.get(name, 0)}
                                  for name in ranked],
                      "why": ("visible from the first step, so nothing hides it"
                              if depth == 0 else
                              f"only becomes visible after {depth} step(s), so a repair deeper in the "
                              f"sequence cannot start here")})
    return {
        "states_with_a_trace": sum(1 for row in rows if len(trace(row)) >= 2),
        "edges": [{"clears": source, "reveals": target, "states": weight}
                  for (source, target), weight in edges.most_common(30)],
        "two_cycles": [list(pair) for pair in cycles],
        "unlocks": summarise(unlocks, "unlocks"),
        "depends_on": {name: dict(sorted(value.items(), key=lambda kv: -kv[1]))
                       for name, value in sorted(depends_on.items())},
        "visible_at_step_0": dict(entry_states.most_common()),
        "by_action": {action: [{"clears": s, "reveals": t, "states": weight}
                               for (s, t), weight in table.most_common(6)]
                      for action, table in sorted(by_action.items(), key=lambda kv: -sum(kv[1].values()))},
        "repair_order": order,
        "caveat": ("an edge means the two classes changed in the SAME STEP, with the action recorded beside "
                   "it. That is correlation at the finest granularity this data has, not proof of cause: "
                   "two classes can share one root cause, and an action can clear one thing and reveal "
                   "another for its own reasons. `two_cycles` lists the pairs where the relation runs both "
                   "ways, so the graph is not a DAG and the order below comes from first-appearance DEPTH, "
                   "not from a topological sort."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", type=Path, required=True,
                    help="the frozen panel receipt to describe")
    ap.add_argument("--compare", type=Path, default=None,
                    help="a second receipt for the SAME panel; only then is a delta reported")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    frame = load(args.frame)
    payload = {"schema_version": SCHEMA_VERSION, "kind": "fault-history",
               "frame": str(args.frame), "frame_schema": frame.get("schema_version"),
               "histogram": histogram(frame),
               "blocker_graph": blocker_graph(frame)}
    if args.compare:
        payload["comparison"] = {"before": str(args.frame), "after": str(args.compare),
                                 **compare(frame, load(args.compare))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["histogram"], indent=2))
    if "comparison" in payload:
        print(json.dumps({k: v for k, v in payload["comparison"].items() if k != "moved"}, indent=2))
    print("written", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
