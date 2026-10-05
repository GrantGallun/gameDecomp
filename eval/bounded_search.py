"""Bounded branching over the EXISTING actions, with the incumbent kept out of the beam.

THE QUESTION THIS ANSWERS. The intake route applies a fixed order and keeps the best candidate. On the
development set that leaves candidates that compile and pass the clang frontend without being byte-exact.
Two very different situations produce that, and only a search can tell them apart:

    a successful executable sequence exists and the FIXED ORDER missed it   -> a policy problem
    no single action gets there but a short COMPOSITION does                 -> a composition problem
    nothing in the catalog reaches it inside this budget                     -> unresolved, not impossible
    a tool, state, verifier or environment failed                            -> infrastructure, fix first

WHY BRANCHING AND NOT THE ONE-SHOT DIFF. An action can lower the similarity score and still be the step
that makes the next one possible (`opaque_variant` is the documented case: it types the parameter that
`or_address` needs, and it has never been credited with a conversion on its own). A greedy loop that keeps
only the best-scoring candidate throws that branch away. So the best INCUMBENT is tracked separately from
the beam, the beam is small, and the depth is 2-3.

NO FREE ORACLE. Every compile is counted against an explicit allowance; the search stops at the cap and
says so, and the KB's own attempt log is the accounting record. "Not found within N compiles" is reported
as exactly that -- never as "no sequence exists".

WHY IT REUSES THE EXISTING API. The operators are the ones the collection, training and evaluation paths
already use (`eval.tool_registry.ACTIONS` runners, resolved through the same `runner(namespace, params)`
contract the intake probe drives). A search that reached exactness with an operator the learner cannot
choose would prove nothing about the learner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1

# The branching catalog, in a FIXED order so a search is reproducible. `frontend_diagnostics` is absent
# because it is an observation (`changed` is always False) and branching on it would multiply the beam by
# an identity step.
CATALOG = ("eval.intake_runners.resolve_placeholders",
           "eval.intake_runners.negative_offset",
           "eval.intake_runners.source_type_declarations",
           "eval.intake_runners.undeclared_identifiers",
           "eval.intake_runners.header_variant",
           "eval.intake_runners.globals_variant",
           "eval.intake_runners.opaque_variant",
           "eval.intake_runners.or_address",
           "eval.intake_runners.rewrite_do_while")


def registry_catalog() -> tuple:
    """Every TRANSFORM the registry advertises, through the same state contract.

    THE INTAKE SEQUENCE IS NOT THE WHOLE REGISTRY. It has nine steps, all of them declaration repairs.
    `eval.tool_registry.ACTIONS` also carries `regalloc-search` (whose inputs -- `target_dump`, `workspace`,
    `compile_fn` -- are all populated by `run_agent_run.build_context`), `diffrepair`, `invert-mutations`
    and `redraft`. A search over the intake subset alone can only conclude something about that subset: the
    panel's residuals are dominated by register-allocation differences, which is precisely what the
    excluded operators exist for. Built from the registry rather than listed here, so an action that is
    added to the contract is searchable and one that is observed rather than transformed is not.
    """
    from eval.tool_registry import ACTIONS

    return tuple(action.runner for action in ACTIONS.values()
                 if action.kind == "transform" and action.runner)

DO_WHILE_DIAGNOSTIC = "contains a do-while loop"


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def rank(verdict: dict | None) -> tuple:
    """Best-first: exact, then compiles, then similarity. The project's existing ordering."""
    verdict = verdict or {}
    return (bool(verdict.get("exact")), bool(verdict.get("compiled")),
            float(verdict.get("score") or 0.0))


def applicable(label: str, stderr: str) -> bool:
    """The same gate the campaign uses: an action that owns a token waits for the compiler to name it."""
    if label.endswith("rewrite_do_while"):
        return DO_WHILE_DIAGNOSTIC in (stderr or "")
    return True


class Search:
    """One state's branching search. Every compile goes through `_score`, which counts it."""

    def __init__(self, *, context, runners: dict, depth: int, beam: int, compiles: int,
                 context_by_hash: dict | None = None, catalog: tuple | None = None):
        self.context = context
        self.runners = runners
        self.depth = max(1, depth)
        self.beam = max(1, beam)
        self.allowance = max(0, compiles)
        self.catalog = tuple(catalog or CATALOG)
        self.used = 0
        self.nodes: list[dict] = []
        self.context_by_hash = context_by_hash if context_by_hash is not None else {}
        # AN ACTION THAT TURNS A COMPILING CANDIDATE INTO AN UNCOMPILABLE ONE IS A SAFETY EVENT, not a
        # score event. Every caller ranks candidates so the damage is normally contained, but a catalog
        # whose members can break a working candidate is not safe to hand to a policy, and counting
        # "nodes that do not compile" hides it -- most nodes descend from a draft that never compiled.
        self.destructive: list[dict] = []
        # AN ACTION MAY COMPILE INTERNALLY. `regalloc-search` runs a beam whose default budget is 64
        # compiles per call, so counting one outer compile per action under-reports the work by up to 64x
        # and lets a search spend far past its declared allowance -- the "hidden internal compile budget"
        # the brief forbids. Internal compiles are charged here and named separately in the receipt.
        self.internal_compiles = 0
        # Set when an action was not attempted because the allowance could not both pay for it and
        # certify its result. Distinguishes "the catalog ran out" from "the budget ran out".
        self.starved = False

    # --- accounting ---------------------------------------------------------

    def _bounded_params(self, label: str, remaining: int) -> dict:
        """Clamp an action's own compile budget to what this search can still afford.

        The registry declares the bounds (`regalloc-search`: `budget` 1..2048, default 64). Passing the
        default unchanged would hand an internal beam 64 compiles out of a 40-compile search allowance.
        """
        from eval.tool_registry import ACTIONS

        action = ACTIONS.get(label) or next(
            (item for item in ACTIONS.values() if item.runner == label), None)
        if action is None:
            return {}
        params = {}
        for param in action.params:
            if param.kind == "int" and param.name in ("budget", "compiles"):
                floor = param.minimum if param.minimum is not None else 1
                params[param.name] = max(floor, min(int(param.default or remaining), remaining))
        return params

    def _score(self, source: str) -> dict | None:
        """One compile, counted. Returns None when the allowance is spent -- never a silent zero."""
        if self.used >= self.allowance:
            return None
        self.used += 1
        self.context.candidate = source
        return self.context.compile_fn(source)

    # --- the search ---------------------------------------------------------

    def run(self, start: str, start_verdict: dict, incumbent: dict | None = None) -> dict:
        """Branch from the FROZEN DRAFT, with the fixed policy's own output as the incumbent to beat.

        THE STARTING POINT IS THE WHOLE EXPERIMENT. The first version of this searched from the policy's
        FINAL candidate, and on the first state measured it made zero moves: every action had already been
        applied once by the fixed order, and re-applying it changes nothing. That search cannot answer the
        question, because the only states it can reach are ones the fixed order already reached.

        So the start is the frozen m2c draft -- the same bytes the fixed policy started from -- the
        branches are DIFFERENT ORDERS AND SUBSETS of the same catalog, and the incumbent is the policy's
        own result, handed in already scored. A branch that lowers the score is still explored, because
        that is what branching is for; the incumbent is never evicted by it.
        """
        node = _node(0, None, "start", "", start, start_verdict, None)
        self.nodes.append(node)
        if incumbent is None:
            incumbent = {"source": start, "verdict": start_verdict, "path": [],
                         "sha256": sha256_text(start)}
        frontier = [{"source": start, "verdict": start_verdict, "path": [],
                     "sha256": node["sha256"]}]
        exact_path, allowance_hit = None, False
        for layer in range(1, self.depth + 1):
            if allowance_hit:
                break
            candidates = []
            for parent in frontier:
                for label in self.catalog:
                    if self.used >= self.allowance:
                        allowance_hit = True
                        break
                    if not applicable(label, parent["verdict"].get("stderr") or ""):
                        continue
                    result = self._apply(label, parent)
                    if result is None:
                        continue
                    source, verdict, detail = result
                    node = _node(layer, parent["sha256"], label.split(".")[-1], "", source, verdict,
                                 detail)
                    self.nodes.append(node)
                    branch = {"source": source, "verdict": verdict,
                              "path": [*parent["path"], label.split(".")[-1]],
                              "sha256": node["sha256"]}
                    if parent["verdict"].get("compiled") and not verdict.get("compiled"):
                        self.destructive.append({"action": label.split(".")[-1],
                                                 "parent_sha256": parent["sha256"],
                                                 "child_sha256": node["sha256"],
                                                 "parent_score": parent["verdict"].get("score"),
                                                 "child_score": verdict.get("score"),
                                                 "child_stderr_head": (verdict.get("stderr") or "")[:200]})
                    if verdict.get("exact"):
                        # A branch that reaches exact wins outright, whatever the incumbent was.
                        return self._report(branch, allowance_hit,
                                            [*parent["path"], label.split(".")[-1]], layer)
                    candidates.append(branch)
                    if rank(verdict) > rank(incumbent["verdict"]):
                        incumbent = branch
                if allowance_hit:
                    break
            candidates.sort(key=lambda item: rank(item["verdict"]), reverse=True)
            frontier = candidates[:self.beam]
            if not frontier:
                # A FRONTIER THAT DIED OF STARVATION IS NOT AN EXHAUSTED CATALOG. Without this the search
                # that ran out of allowance reported "the catalog was exhausted at this depth", which is a
                # different and much stronger claim.
                allowance_hit = allowance_hit or self.starved
                break
        return self._report(incumbent, allowance_hit, exact_path, None)

    def _apply(self, label: str, parent: dict):
        runner = self.runners.get(label)
        if runner is None:
            # AN ACTION WITH NO RUNNER IS RECORDED, NOT SKIPPED SILENTLY. The catalog and the registry
            # are two lists that have to agree, and a name in one and not the other is exactly how the
            # unwired-action defect looked from outside: an action that never fires and never says why.
            self.nodes.append({"depth": None, "action": label.split(".")[-1], "status": "unavailable",
                               "reason": "the catalog names this action but no runner is registered",
                               "parent_sha256": parent["sha256"]})
            return None
        namespace = {**self.context.__dict__, "candidate": parent["source"],
                     "diff": parent["verdict"].get("diff"),
                     "initial_verdict": parent["verdict"]}
        # ONE COMPILE IS HELD BACK to certify whatever this action produces: an internal search that
        # consumes the whole remaining allowance and returns a candidate nobody can score has spent the
        # budget and answered nothing.
        remaining = self.allowance - self.used
        if remaining <= 1:
            self.starved = True
            return None
        params = self._bounded_params(label, remaining - 1)
        try:
            result = runner(namespace, params)
        except Exception as exc:                                # noqa: BLE001
            self.nodes.append({"depth": None, "action": label.split(".")[-1], "status": "crashed",
                               "error": f"{type(exc).__name__}: {exc}",
                               "parent_sha256": parent["sha256"]})
            return None
        # THE ACTION'S OWN COMPILES ARE CHARGED. Reported as `compiles` by the runner; 0 for the actions
        # that do no internal compiling, so the total stays exact either way.
        internal = 0
        try:
            internal = max(0, int(result.get("compiles") or 0))
        except (TypeError, ValueError):
            internal = 0
        self.internal_compiles += internal
        self.used += internal
        if not result.get("changed") or not isinstance(result.get("source"), str):
            return None
        verdict = self._score(result["source"])
        if verdict is None:
            return None
        detail = {"status": result.get("status"), "compiles": self.used,
                  "internal_compiles": internal, "params": params or None}
        if result.get("detail"):
            detail["runner_detail_keys"] = sorted(result["detail"])
        return result["source"], verdict, detail

    def _report(self, incumbent: dict, allowance_hit: bool, exact_path, depth_found) -> dict:
        return {"exact": bool(incumbent["verdict"].get("exact")),
                "final_sha256": incumbent["sha256"], "final_score": incumbent["verdict"].get("score"),
                "solution_path": exact_path, "depth_found": depth_found,
                "compiles_used": self.used, "allowance": self.allowance,
                "internal_compiles": self.internal_compiles,
                "compiles_used_certifying": self.used - self.internal_compiles,
                "allowance_exhausted": allowance_hit,
                # STOPPED BY THE ALLOWANCE vs SPENT THE ALLOWANCE. A search can reach its depth bound with
                # every compile used: it stopped for the depth, and the budget is gone. Reporting only the
                # first would make "the depth bound ended this" and "the budget ended this" look alike.
                "allowance_spent": self.used >= self.allowance,
                "nodes": len(self.nodes),
                # THE SAFETY MEASUREMENT, kept apart from the score: how many moves broke a candidate
                # that compiled. Zero is the only acceptable number for a catalog a policy may choose
                # from freely.
                "destructive_moves": len(self.destructive),
                "destructive_detail": self.destructive[:5],
                "source": incumbent["source"]}


def _node(depth, parent_sha256, action, params, source, verdict, detail) -> dict:
    return {"depth": depth, "parent_sha256": parent_sha256, "action": action, "params": params,
            "sha256": sha256_text(source), "chars": len(source),
            "compiled": bool(verdict.get("compiled")), "exact": bool(verdict.get("exact")),
            "score": verdict.get("score"), "detail": detail}


def classify(result: dict, *, single_action_solution: bool, policy_sha256: str | None = None) -> dict:
    """The four outcomes the brief names, kept apart, plus what the search actually did.

    `sub_outcome` exists because "the catalog reached nothing" and "the catalog reached exactly what the
    fixed order already reached" are the same top-level verdict and completely different findings: the
    first says the catalog is not enough here, the second says the fixed order is already optimal inside
    it. Folding them together would hide the second, which is the more informative of the two.
    """
    if result.get("infrastructure_error"):
        return {"outcome": "infrastructure",
                "why": "a tool, state, verifier or environment failed; fix and rebaseline before "
                       "inferring anything about the policy",
                "detail": result["infrastructure_error"]}
    if result.get("exact"):
        base = {"outcome": "policy-learning-opportunity" if (result.get("depth_found") == 1
                                                            or single_action_solution)
                else "composition-learning-opportunity",
                "path": result.get("solution_path"),
                "sub_outcome": "exact-reached"}
        base["why"] = ("a single action from the frozen draft reaches exact and the fixed order did not "
                       "take it" if base["outcome"] == "policy-learning-opportunity" else
                       "no single action reaches exact; a sequence of the same catalog does")
        return base
    improved = bool(policy_sha256) and result.get("final_sha256") != policy_sha256
    if result.get("allowance_exhausted"):
        why = ("the catalog did not reach exact inside the declared compile allowance; this is not proof "
               "that no sequence exists")
    elif result.get("allowance_spent"):
        why = ("the depth bound ended the search and the compile allowance is fully spent; the next thing "
               "this state needs is a deeper search with a bigger allowance, not a different catalog")
    else:
        why = "the catalog was exhausted at this depth without reaching exact"
    return {"outcome": "unresolved-within-budget", "why": why,
            "compiles_used": result.get("compiles_used"),
            "sub_outcome": "improved-over-the-fixed-order" if improved else "no-improvement",
            "note": ("the best branch is a different candidate from the one the fixed order produced"
                     if improved else
                     "the best branch IS the candidate the fixed order produced: inside this catalog and "
                     "depth, the fixed order is already optimal for this state")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dev-set", type=Path,
                    default=ROOT / "eval/results/dev-set-20260921/dev-set.json")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/dev-set-20260921/search.json")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--beam", type=int, default=3)
    ap.add_argument("--compiles", type=int, default=40,
                    help="GLOBAL compile allowance per state; the search stops at it and says so")
    ap.add_argument("--limit", type=int, default=0, help="0 = every selected state")
    ap.add_argument("--start", choices=("draft", "policy"), default="draft",
                    help="branch from the frozen m2c draft (does another short sequence beat the fixed "
                         "order?) or from the fixed order's own candidate (is it a fixed point of the "
                         "catalog, or one step from better?)")
    # WHICH ACTIONS ARE IN THE SEARCH, which is part of the result rather than a detail of it: a null over
    # nine declaration repairs says nothing about the four operators the registry also advertises.
    ap.add_argument("--catalog", choices=("intake", "registry"), default="intake",
                    help="`intake` = the nine-step intake sequence; `registry` = every transform "
                         "`eval.tool_registry.ACTIONS` advertises, including regalloc-search")
    # WHERE THE PANEL'S SOURCES ARE. It defaulted to `<dev-set dir>/sources`, which is a naming convention
    # rather than a contract: a panel built into `finish-line-sources/` failed all seven states with
    # `FileNotFoundError` and every one of them was reported as an infrastructure result -- the honest
    # outcome, and a wasted run.
    ap.add_argument("--sources", type=Path, default=None,
                    help="directory of <function>.c for the panel; default = the dev-set's own `sources/`")
    args = ap.parse_args(argv)

    from eval.intake_runners import RUNNERS
    from eval.tool_agent_run import build_context
    from eval.tool_registry import ACTIONS

    # THE RUNNER MAP MUST COVER THE CATALOG, OR A WIRING BUG IS REPORTED AS A CAPABILITY RESULT. The first
    # `--catalog registry` run resolved its runners from the intake dict alone, so five of the ten registry
    # actions were recorded `unavailable` on every state and the whole run reported ZERO compiles over
    # seventeen states -- which reads exactly like "the registry has nothing to offer here". Resolved
    # through the registry, the contract the collection, training and evaluation paths use.
    runners = {action.runner: action.resolve() for action in ACTIONS.values() if action.runner}
    runners.update({name: runner for name, runner in RUNNERS.items() if name not in runners})

    dev = json.loads(args.dev_set.read_text(encoding="utf-8"))
    catalog = registry_catalog() if args.catalog == "registry" else CATALOG
    sources = args.sources or (args.dev_set.parent / "sources")
    entries = dev["entries"][: args.limit or len(dev["entries"])]
    results, started = [], time.time()
    for entry in entries:
        name = entry["function"]
        record = {"function": name, "sha256": entry["sha256"],
                  "assistance": entry.get("assistance"), "recorded": entry.get("recorded")}
        try:
            source = (sources / f"{name}.c").read_text(encoding="utf-8")
            if sha256_text(source) != entry["sha256"]:
                record["infrastructure_error"] = "the exported source does not hash to the recorded digest"
                record["classification"] = classify(record, single_action_solution=False)
                results.append(record)
                continue
            context, why = build_context(args.repo, name)
            if context is None:
                record["infrastructure_error"] = f"no context: {why}"
                record["classification"] = classify(record, single_action_solution=False)
                results.append(record)
                continue
            # THE FROZEN START IS REPRODUCED, NOT ASSUMED. The frame recorded the draft's hash; the
            # context's own draft must hash to it, or the search is branching from different bytes and
            # every number after this point describes a state that was never measured.
            draft = context.candidate or ""
            if row_draft := entry.get("baseline_draft_sha256"):
                if sha256_text(draft) != row_draft:
                    record["infrastructure_error"] = (
                        "the context's m2c draft does not hash to the frozen frame's draft_sha256 "
                        f"({sha256_text(draft)[:16]}... != {row_draft[:16]}...)")
                    record["classification"] = classify(record, single_action_solution=False)
                    results.append(record)
                    continue
            start_verdict = context.compile_fn(draft)
            # THE INCUMBENT IS THE FIXED POLICY'S OWN OUTPUT. The search has to beat the thing it is
            # being compared against, and that thing is not the draft.
            policy_source = source
            policy_verdict = context.compile_fn(policy_source)
            # WHICH STATE THE BRANCHES LEAVE FROM, which is a different question either way. From the
            # draft: does another ordering beat the fixed order? From the policy's own candidate: is that
            # candidate a FIXED POINT of the catalog, or is one more step enough? The second is the one
            # that explains a negative result instead of only reporting it.
            branch_from = draft if args.start == "draft" else policy_source
            branch_verdict = start_verdict if args.start == "draft" else policy_verdict
            search = Search(context=context, runners=runners, depth=args.depth, beam=args.beam,
                            compiles=args.compiles, catalog=catalog)
            found = search.run(branch_from, branch_verdict,
                               incumbent={"source": policy_source, "verdict": policy_verdict, "path": [],
                                          "sha256": sha256_text(policy_source)})
            single = _single_action_probe(context, branch_from, branch_verdict, runners, args, catalog)
            record.update({"start": {"sha256": sha256_text(draft),
                                     "compiled": bool(start_verdict.get("compiled")),
                                     "exact": bool(start_verdict.get("exact")),
                                     "score": start_verdict.get("score"),
                                     "stderr_head": (start_verdict.get("stderr") or "")[:160]},
                           "policy": {"sha256": sha256_text(policy_source),
                                      "compiled": bool(policy_verdict.get("compiled")),
                                      "exact": bool(policy_verdict.get("exact")),
                                      "score": policy_verdict.get("score")},
                           "single_action_solutions": single["solutions"],
                           "search": {key: value for key, value in found.items() if key != "source"},
                           "nodes": search.nodes})
            record["classification"] = classify(
                found, single_action_solution=bool(single["solutions"]),
                policy_sha256=record["policy"]["sha256"])
            if found.get("exact"):
                record["solution_source"] = found["source"]
                record["solution_sha256"] = found["final_sha256"]
        except Exception as exc:                                # noqa: BLE001
            record["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
            record["classification"] = classify(record, single_action_solution=False)
        results.append(record)
        print(json.dumps({"function": name, "outcome": record["classification"]["outcome"],
                          "compiles": (record.get("search") or {}).get("compiles_used"),
                          "score": (record.get("search") or {}).get("final_score")}), flush=True)

    summary: dict = {}
    for record in results:
        outcome = record["classification"]["outcome"]
        summary[outcome] = summary.get(outcome, 0) + 1
    payload = {"schema_version": SCHEMA_VERSION, "kind": "bounded-branching-search",
               "catalog_name": args.catalog, "catalog": list(catalog),
               "depth": args.depth, "beam": args.beam,
               "branch_from": args.start,
               "compile_allowance_per_state": args.compiles,
               "dev_set": str(args.dev_set),
               "regime": ("DEVELOPMENT DATA. The panel is the exposed 200-state frame, never a sealed "
                          "test set. A state with no exact result inside this catalog and allowance is "
                          "UNRESOLVED, not impossible."),
               "summary": summary, "states": len(results),
               "compiles_used": sum((r.get("search") or {}).get("compiles_used", 0) for r in results),
               # ACROSS THE WHOLE PANEL, because this is the property the catalog has to hold jointly.
               "destructive_moves": sum((r.get("search") or {}).get("destructive_moves", 0)
                                        for r in results),
               "seconds": round(time.time() - started, 1), "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"states": len(results), "summary": summary,
                      "compiles_used": payload["compiles_used"], "seconds": payload["seconds"],
                      "out": str(args.out)}, indent=2))
    return 0


def _single_action_probe(context, source: str, start_verdict: dict, runners: dict, args,
                         catalog: tuple = CATALOG) -> dict:
    """Which SINGLE actions reach exact from the frozen state, if any.

    This is what separates "the policy missed an available move" from "only a composition works", and it
    cannot be read off the search: the search returns the first solution it finds at the shallowest depth,
    which is a fact about the order it explored rather than about what was available.
    """
    solutions = []
    used = 0
    for label in catalog:
        if used >= args.compiles:
            break
        if not applicable(label, start_verdict.get("stderr") or ""):
            continue
        namespace = {**context.__dict__, "candidate": source, "diff": start_verdict.get("diff"),
                     "initial_verdict": start_verdict}
        try:
            result = runners[label](namespace, {})
        except Exception:                                       # noqa: BLE001
            continue
        if not result.get("changed") or not isinstance(result.get("source"), str):
            continue
        used += 1
        context.candidate = result["source"]
        verdict = context.compile_fn(result["source"])
        if verdict.get("exact"):
            solutions.append({"action": label.split(".")[-1], "sha256": sha256_text(result["source"]),
                              "score": verdict.get("score")})
    return {"solutions": solutions, "compiles": used}


if __name__ == "__main__":
    sys.exit(main())
