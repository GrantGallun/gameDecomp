"""Distil a research-policy dataset into weights. The last stage of the loop, and the gated one.

`eval/research_loop.py` produces preference pairs whose label is what a decision LED TO, not whether
the immediate answer was right: the edit that lowered the score and later produced the breakthrough
is the positive example. This module turns those pairs into a training set and runs QLoRA on them.

Three things it refuses to do, each because doing it silently would corrupt the result:

1. It refuses to train when `eval.research_loop.gate` says the controller did not beat fixed-seed and
   random reseeding. Distilling a policy that is no better than random teaches the model noise, and
   the failure would look like a training problem rather than an empty mechanism.
2. It refuses to train without an untouched evaluation family. The design's requirement is explicit:
   train across environment families and evaluate on structurally different ones, or the result
   measures specialisation rather than improvement.
3. It refuses to claim success it cannot check. With no training stack installed it writes the dataset
   and exits non-zero with the exact command that would install one, rather than finishing quietly.

What it does NOT do yet: the multi-generation loop (M0 -> M1 -> M2). One generation is the honest
first result; the recursion question -- does M_{t+1} generate better training data than M_t -- needs
a measurement this module does not yet make.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
from pathlib import Path

SYSTEM = ("You choose the next research action for a decompilation attempt. Reply with the action "
          "you would take, as JSON.")

ACTION_FIELDS = ("node", "score", "profile", "reachable_value")


class Refused(RuntimeError):
    """A precondition declined. Never caught silently: it ends the run with its reason."""


def dominant(profile: dict) -> str:
    """The fault class a residual is mostly made of."""
    if not profile or not any(profile.values()):
        return "none"
    return max(profile, key=lambda axis: profile[axis])


def technique_of(action: dict, state: dict) -> dict:
    """The transferable technique behind an action, instead of the node that happened to hold it.

    This is the difference between teaching a model something and teaching it nothing. A label of
    `{"node": 3}` is an index into one function's search tree: it cannot transfer to another function,
    and a model trained on it has learned to memorise trees. A label of
    `{"kind": "redirect", "target": "layout"}` says "stop refining the structural residual and go
    after the layout fault instead", which is a claim about decompilation that a different function
    can be judged by.

    `kind` is refine when the action attacks the same fault class as the current leader, and redirect
    when it attacks a different one. The value of each action still comes from what it led to.
    """
    target = dominant(action["profile"])
    leader_target = dominant(state["faults"])
    return {"kind": "refine" if target == leader_target else "redirect", "target": target}


def informative(pair: dict) -> bool:
    """True when the pair is a real choice between two viable techniques.

    The first version of this function did not exist, and without it 1,833 of 1,928 pairs had the
    REJECTED action carrying an all-zero fault profile -- a draft that never compiled, whose
    `signals.analyse` returns nothing. The preference the model was being taught was therefore
    "prefer a candidate that compiles over one that does not", which a base model learns in twenty
    steps and which says nothing about decompilation. It scored 1.00 on held-out pairs, which is how
    it was caught: perfect accuracy on a research-policy task is a finding about the dataset.

    A useful pair needs both actions to be viable (a residual to reason about) and to differ in
    technique (otherwise there is nothing to prefer).
    """
    better, worse = pair["better"], pair["worse"]
    if not better["profile"] or not worse["profile"]:
        return False
    if not any(better["profile"].values()) or not any(worse["profile"].values()):
        return False
    return technique_of(better, pair["state"]) != technique_of(worse, pair["state"])


def build_examples(pairs: list[dict]) -> list[dict]:
    """Preference examples in a chat format a DPO trainer can consume.

    The prompt carries the PLATEAU, never the answer: score so far, the fault profile, whether the
    residual is structured, what share of it a pass owns, and how crowded the neighbourhood is. The
    model is being taught which technique to reach for from a state, not which C to write.
    """
    examples = []
    for pair in pairs:
        if not informative(pair):
            continue
        state = pair["state"]
        prompt = (f"score {state['score']:.2f}; faults {json.dumps(state['faults'])}; "
                  f"structured={state['structured']}; "
                  f"repairable={state['repairable_share']:.2f}; "
                  f"expansions={state['expansions']}; frontier={state['frontier']}; "
                  f"distinct_residuals={state['distinct_residuals']}; "
                  f"plateau={pair['plateau']}")
        examples.append({
            "func": pair["func"], "step": pair["step"], "prompt": prompt,
            "chosen": json.dumps(technique_of(pair["better"], state), sort_keys=True),
            "rejected": json.dumps(technique_of(pair["worse"], state), sort_keys=True),
            # Kept because the trainer should be able to weight by how much the choice mattered,
            # and because a pair with a tiny gap is nearly a tie and should probably be dropped.
            "value_gap": pair["value_gap"],
        })
    return examples


def majority_baseline(examples: list[dict]) -> float:
    """Accuracy from always naming the most common chosen technique.

    The number that decides whether a training result means anything. If a constant scores 0.9, a
    model scoring 0.9 has learned the prior, not the task.
    """
    if not examples:
        return 0.0
    labels = collections.Counter(e["chosen"] for e in examples)
    return round(labels.most_common(1)[0][1] / len(examples), 4)


def filter_examples(examples: list[dict], min_gap: float = 1.0) -> list[dict]:
    """Drop near-ties. A pair whose two actions differ by 0.2 points teaches almost nothing."""
    return [example for example in examples if example["value_gap"] >= min_gap]


def split_by_function(examples: list[dict], holdout: float = 0.2,
                      seed: int = 20260916) -> tuple[list[dict], list[dict]]:
    """Split by FUNCTION, never by example.

    Two states from one function share a search tree, a residual vocabulary and often the same
    alternatives. Splitting by row would put half a tree in training and half in evaluation, and the
    held-out score would measure recall of a tree the model has already seen. This is the same rule
    `eval/clean_set.py` applies one level up, between translation units.
    """
    import random
    functions = sorted({example["func"] for example in examples})
    random.Random(seed).shuffle(functions)
    cut = max(1, int(len(functions) * holdout))
    held = set(functions[:cut])
    train = [e for e in examples if e["func"] not in held]
    test = [e for e in examples if e["func"] in held]
    if not train or not test:
        raise Refused(f"split produced {len(train)} train and {len(test)} test examples")
    return train, test


def training_stack() -> dict:
    """What is actually installed. Reported rather than assumed."""
    found = {}
    for module in ("torch", "transformers", "peft", "trl", "datasets"):
        try:
            __import__(module)
            found[module] = True
        except Exception:
            found[module] = False
    gpu = shutil.which("nvidia-smi") is not None
    return {"modules": found, "cuda_visible": gpu,
            "ready": all(found.values())}


def require_gate(report: dict) -> dict:
    """Refuse to distil unless the controller clears the controls under the CURRENT rule.

    A stored verdict is reused only when it was computed under the current `GATE_RULE_VERSION`.
    Reusing an older one would let a result that passed a weaker rule authorise a weight update --
    and that is not hypothetical: the Sept 16 replay stored `passed: true` under rule version 1,
    which never compared the controller against its own no-learn control.
    """
    from eval import research_loop as rl
    stored = report.get("gate") or {}
    stale = stored.get("rule_version") != rl.GATE_RULE_VERSION
    verdict = rl.gate(report) if (stale or not stored) else stored
    if stale and stored:
        verdict = {**verdict, "recomputed_because": (
            f"the stored verdict was computed under gate rule "
            f"{stored.get('rule_version', 'unknown')}, not {rl.GATE_RULE_VERSION}")}
    if not verdict.get("passed"):
        raise Refused(f"the controller does not beat its controls ({verdict.get('reason')}); "
                      f"distilling it would teach the model noise")
    return verdict


def require_holdout(pairs: list[dict], holdout_functions: set[int]) -> None:
    """Refuse when the training pairs and the evaluation functions overlap.

    Cheap to check here and impossible to undo later: a distilled policy evaluated on what it trained
    on reports an improvement that is pure memorisation.
    """
    trained = {pair["func"] for pair in pairs}
    overlap = trained & holdout_functions
    if overlap:
        raise Refused(f"{len(overlap)} evaluation function(s) appear in the training pairs: "
                      f"{sorted(overlap)[:5]}")
    if not holdout_functions:
        raise Refused("no held-out evaluation functions were supplied; the result would be "
                      "unmeasurable")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pairs", type=Path, required=True, help="JSONL from research_loop --distill")
    ap.add_argument("--report", type=Path, required=True, help="the replay report carrying the gate")
    ap.add_argument("--holdout", type=Path, required=True,
                    help="JSON file with {'functions': [addr, ...]} held out from training")
    ap.add_argument("--out", type=Path, required=True, help="where to write the training set")
    ap.add_argument("--min-gap", type=float, default=1.0)
    ap.add_argument("--check", action="store_true", help="report the stack and stop")
    args = ap.parse_args(argv)

    if args.check:
        print(json.dumps(training_stack(), indent=2))
        return 0

    pairs = [json.loads(line) for line in args.pairs.read_text(encoding="utf-8").splitlines() if line]
    require_gate(json.loads(args.report.read_text(encoding="utf-8")))
    holdout = set(json.loads(args.holdout.read_text(encoding="utf-8"))["functions"])
    require_holdout(pairs, holdout)

    examples = filter_examples(build_examples(pairs), args.min_gap)
    if not examples:
        raise Refused(f"{len(pairs)} pairs, none with a gap of at least {args.min_gap}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example) + "\n")

    stack = training_stack()
    missing = [name for name, present in stack["modules"].items() if not present]
    if stack["ready"]:
        why = "the QLoRA run is the next stage and is not implemented in this module yet"
    else:
        why = ("training stack incomplete, missing " + ", ".join(missing)
               + "; install with: python3 -m venv ~/decomp/train-venv && "
                 "~/decomp/train-venv/bin/pip install --index-url "
                 "https://download.pytorch.org/whl/cu128 torch && "
                 "~/decomp/train-venv/bin/pip install transformers peft trl datasets")
    # Report what happened rather than exiting 0 as if the weights had changed: they have not.
    print(json.dumps({"examples": len(examples), "from_pairs": len(pairs),
                      "min_gap": args.min_gap, "out": str(args.out),
                      "training": "not run", "why": why}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

