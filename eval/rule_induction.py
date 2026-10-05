"""Can a local model INDUCE a compiler rule by experiment? (step 1 of the general-purpose plan)

The model gets a real residual and a probe tool -- compile two variants of a minimal function with the target's
recipe, see same/differ and the instruction diff -- and must state a rule with conditions. It never sees the
catalog or eval/rule_probes' receipt. The rule is graded by PREDICTION on unseen validation pairs whose truth the
compiler supplied (eval/results/rule-probes-20261002/probes.json):

    prior    no probes, no rule: the model's own guesses
    induced  the model's rule after probing (pairs it probed verbatim are excluded from its score)
    catalog  the hand-derived catalog rule, as a reference ceiling

    python3 -m eval.rule_induction [--seeds 2] [--topics a,b]   -> E/induction.jsonl, summary on stdout

Every prompt and reply is kept: induction traces (hypothesis -> probe -> verdict -> rule) are the training data
for the METHOD, which transfers across compilers, unlike the rules themselves.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import re
import tempfile
import threading
from pathlib import Path

from eval import rule_probes

ROOT = Path(__file__).resolve().parents[1]
E = Path.home() / "decomp/experiments/edit-capability-20261002"
RECEIPT = ROOT / "eval/results/rule-probes-20261002/probes.json"
MODEL = "gpt-oss:20b"
MAX_PROBES = 8

# topic -> (development case whose residual starts the investigation, validation probe family, catalog rule ids)
TOPICS = {
    "result_temporary": ("temp_return:osSpTaskYield", "return_temp", ["ido53-o1-result-temporary-has-a-stack-home"]),
    "commutative_operands": ("commute:drawEndingCreditsCharacterLoopingSparkle", "commute_operands",
                             ["ido53-commutative-operand-materialisation-order"]),
    "store_order": ("stmt_swap:initRaceSplitscreenSelectCornerSprites", "store_order",
                    ["ido53-adjacent-store-order-is-preserved"]),
    "empty_arm": ("if_invert:updateRacePlayerRecoverySparkle", "empty_then_arm", ["ido53-empty-arm-layout-conditions"]),
    "operator_spellings": ("arith_op:updateRaceItemProjectileTrailEffect", "operator_spellings", []),
}

QUESTION = ("Investigate the compiler behaviour behind this residual. Which source spelling choice produces this "
            "kind of difference, and in which contexts does that choice change the compiled code versus compile "
            "identically? Consider both optimisation levels.")

SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["probe", "rule"]},
    "hypothesis": {"type": "string"}, "a": {"type": "string"}, "b": {"type": "string"},
    "opt": {"type": "string", "enum": ["O1", "O2"]}, "rule": {"type": "string"}}, "required": ["action"]}
PRED_SCHEMA = {"type": "object", "properties": {"predictions": {"type": "array", "items": {
    "type": "object", "properties": {"i": {"type": "integer"}, "verdict": {"type": "string", "enum": ["same", "differ"]}},
    "required": ["i", "verdict"]}}}, "required": ["predictions"]}

_RECIPES = None
_LOCK = threading.Lock()


def recipes():
    global _RECIPES
    with _LOCK:
        if _RECIPES is None:
            from eval import repair_dataset_synth as rds
            _RECIPES = {opt: rds.resolve_recipe(rule_probes.REPO, t)["resolved"] for opt, t in rule_probes.RECIPES.items()}
        return _RECIPES


def probe(a: str, b: str, opt: str, tag: str) -> dict:
    """The model's tool: compile both bodies of `s32 probe(Obj *o, s32 p, s32 q)` with the target's recipe."""
    bad = [side for side, body in (("a", a), ("b", b)) if not body.strip().startswith("{") or not body.strip().endswith("}")]
    if bad:
        return {"verdict": "invalid-probe",
                "diff": f"{', '.join(bad)}: each body must be brace-enclosed, e.g. `{{ return p + q; }}`, and may use "
                        "only the declarations listed above (not the game function's own names)"}
    with tempfile.TemporaryDirectory() as tmp:
        la, ea = rule_probes.compile_listing_or_error(recipes()[opt], f"{rule_probes.PRELUDE}\n{rule_probes.SIG}\n{a}\n",
                                                      Path(tmp), f"{tag}a")
        lb, eb = rule_probes.compile_listing_or_error(recipes()[opt], f"{rule_probes.PRELUDE}\n{rule_probes.SIG}\n{b}\n",
                                                      Path(tmp), f"{tag}b")
    if la is None or lb is None:
        # The compiler's own message goes back to the model. A bare "does-not-compile" taught it nothing: 17 of 43
        # probes in the 2026-10-03 induction runs failed, eight in a row in two runs (audit 2026-10-03).
        errors = "\n".join(f"{side}: {e.strip()}" for side, e in (("a", ea), ("b", eb)) if e)
        return {"verdict": "does-not-compile", "diff": errors or "(no compiler message)"}
    if la == lb:
        return {"verdict": "same", "diff": ""}
    diff = list(difflib.unified_diff(la, lb, "a", "b", lineterm="", n=1))[2:]
    return {"verdict": "differ", "diff": "\n".join(diff[:30])}


def _norm(body: str) -> str:
    return re.sub(r"\s+", "", body)


def _gen(endpoint, prompt, schema, seed, temperature=0.4):
    # To share the machine, set SOLVER_GAP_MS (solver.llm._throttle): an idle gap after each call that never
    # changes what is generated.
    from solver import llm
    text, _meta = llm.generate(endpoint, MODEL, prompt, timeout=600, num_predict=6000, think="low",
                               temperature=temperature, seed=seed, response_schema=schema,
                               cache_dir=str(E / "llm-cache"), cache_namespace="rule-induction-v1")
    return text


def induce(topic: str, case: dict, endpoint: str, seed: int) -> dict:
    header = ("You study how a C compiler (IDO 5.3, MIPS, used for an N64 game) translates source to machine code. "
              "You have a PROBE tool: give two bodies `a` and `b` for the function below and an optimisation level "
              "(O1 or O2); the tool compiles both with the game's real recipe and tells you whether the machine code "
              "is identical (`same`) or not (`differ`, with the instruction diff).\n\n"
              f"Available declarations:\n```c\n{rule_probes.PRELUDE}```\nEach body is the brace-enclosed body of "
              f"`{rule_probes.SIG}`, e.g. `{{ return p + q; }}`.\n\n"
              f"A residual from a real function (the C below compiles, but differently from the original):\n"
              f"```c\n{case['perturbed_def']}\n```\nTarget (-) versus this C (+):\n```\n{case['diff'][:3000]}\n```\n\n"
              f"{QUESTION}\n")
    history, trace = [], []
    rule = ""
    for step in range(MAX_PROBES + 1):
        done = "\n".join(f"Probe {i + 1}: hypothesis: {h['hypothesis']}\n  a: {h['a']}\n  b: {h['b']}\n  opt: "
                         f"{h['opt']} -> {h['verdict']}\n{h['diff']}" for i, h in enumerate(history)) or "(none yet)"
        last = step == MAX_PROBES
        ask = ("You have used all probes: answer with action `rule` now." if last else
               f"You may run {MAX_PROBES - step} more probe(s), each testing one hypothesis in a new context, or "
               "stop with action `rule`.")
        prompt = (f"{header}\nProbes so far:\n{done}\n\n{ask} A rule states WHEN the spelling choice changes the "
                  "compiled code and WHEN it does not, as precisely as your probes support, and how to fix such a "
                  'residual. Answer with JSON: {"action": "probe", "hypothesis": ..., "a": ..., "b": ..., "opt": '
                  '"O1"|"O2"} or {"action": "rule", "rule": ...}.')
        try:
            reply = json.loads(_gen(endpoint, prompt, SCHEMA, seed * 100 + step))
        except Exception as exc:
            trace.append({"step": step, "error": repr(exc)[-200:]})
            continue
        trace.append({"step": step, "prompt_chars": len(prompt), "reply": reply})
        if reply.get("action") == "rule" or last:
            rule = reply.get("rule") or ""
            if rule:
                break
            continue
        a, b, opt = reply.get("a", ""), reply.get("b", ""), reply.get("opt", "O2")
        opt = opt if opt in ("O1", "O2") else "O2"
        key = (_norm(a), _norm(b), opt)
        earlier = next((h for h in history if (_norm(h["a"]), _norm(h["b"]), h["opt"]) == key), None)
        if earlier:
            # Measured on the first run: 4 of 6 probes were the identical pair under different hypotheses.
            result = {"verdict": earlier["verdict"], "diff": "(REPEAT: you already ran exactly this probe; a "
                      "different hypothesis needs different code in a or b)", "repeat": True}
        else:
            result = probe(a, b, opt, f"i{seed}_{step}_")
        history.append({"hypothesis": reply.get("hypothesis", ""), "a": a, "b": b, "opt": opt, **result})
    return {"rule": rule, "probes": history, "trace": trace}


def predict(rule: str, pairs: list[dict], endpoint: str, seed: int) -> list[str | None]:
    listing = "\n".join(f"{i}. opt {p['opt']}\n   a: {p['a']}\n   b: {p['b']}" for i, p in enumerate(pairs))
    basis = (f"Use this rule about the compiler:\n{rule}\n\n" if rule else
             "You have no measurements of this compiler; use your best judgement.\n\n")
    prompt = ("A C compiler (IDO 5.3, MIPS) compiles two bodies `a` and `b` of "
              f"`{rule_probes.SIG}` with these declarations:\n```c\n{rule_probes.PRELUDE}```\n{basis}"
              "For each numbered pair, predict whether the machine code is identical (`same`) or not (`differ`).\n\n"
              f"{listing}\n\nAnswer with JSON: {{\"predictions\": [{{\"i\": <n>, \"verdict\": \"same\"|\"differ\"}}, ...]}}.")
    try:
        got = {int(x["i"]): x["verdict"] for x in json.loads(_gen(endpoint, prompt, PRED_SCHEMA, seed, 0.2))["predictions"]}
    except Exception:
        got = {}
    return [got.get(i) for i in range(len(pairs))]


def validation(family: str) -> list[dict]:
    rows = json.loads(RECEIPT.read_text())["families"][family]
    out = []
    for r in rows:
        if r["verdict"] in ("same", "differ"):
            a, b = r["pair"]
            out.append({"a": r["sources"][a], "b": r["sources"][b], "opt": r["opt"], "truth": r["verdict"],
                        "context": r["context"]})
    return out


def catalog_text(ids: list[str]) -> str:
    from patterns.catalog import CATALOG
    return "\n".join(f"{CATALOG[i].name}. {CATALOG[i].means} {CATALOG[i].prescription}" for i in ids)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--topics", default=",".join(TOPICS))
    ap.add_argument("--summary", action="store_true", help="table over every saved row (catalog arm from the cache)")
    a = ap.parse_args(argv)
    from solver import llm
    endpoint = llm.host()
    if a.summary:
        return summarize(endpoint)
    cases = {c["id"]: c for c in map(json.loads, open(E / "cases.jsonl"))}
    out = E / "induction.jsonl"
    # Resumable: (topic, seed) rows already written are skipped (prior/catalog predictions come from the LLM cache).
    finished = {(r["topic"], r["seed"]) for r in map(json.loads, open(out))} if out.exists() else set()
    summary = collections.defaultdict(lambda: collections.defaultdict(list))
    with open(out, "a") as f:
        for topic in a.topics.split(","):
            case_id, family, ids = TOPICS[topic]
            pairs = validation(family)
            truth = [p["truth"] for p in pairs]

            def score(preds, exclude=frozenset()):
                kept = [(p, t) for i, (p, t) in enumerate(zip(preds, truth)) if i not in exclude]
                return sum(p == t for p, t in kept), len(kept)

            prior = predict("", pairs, endpoint, 7)
            summary[topic]["prior"].append(score(prior))
            if ids:
                summary[topic]["catalog"].append(score(predict(catalog_text(ids), pairs, endpoint, 7)))
            for seed in range(1, a.seeds + 1):
                if (topic, seed) in finished:
                    continue
                run = induce(topic, cases[case_id], endpoint, seed)
                probed = {(_norm(h["a"]), _norm(h["b"]), h["opt"]) for h in run["probes"]} | \
                         {(_norm(h["b"]), _norm(h["a"]), h["opt"]) for h in run["probes"]}
                overlap = {i for i, p in enumerate(pairs) if (_norm(p["a"]), _norm(p["b"]), p["opt"]) in probed}
                preds = predict(run["rule"], pairs, endpoint, 7) if run["rule"] else [None] * len(pairs)
                summary[topic]["induced"].append(score(preds, overlap))
                f.write(json.dumps({"topic": topic, "seed": seed, "case": case_id, "rule": run["rule"],
                                    "probes": run["probes"], "trace": run["trace"], "overlap": sorted(overlap),
                                    "predictions": preds, "truth": truth, "prior": prior}) + "\n")
                f.flush()
                distinct = len({(_norm(h["a"]), _norm(h["b"]), h["opt"]) for h in run["probes"]})
                summary[topic]["distinct"].append((distinct, len(run["probes"])))
                print(f"{topic:22} seed {seed}: {distinct} distinct of {len(run['probes'])} probes, rule {len(run['rule'])} chars, "
                      f"induced {score(preds, overlap)} prior {score(prior)}", flush=True)
    print(f"\n{'topic':22} {'prior':>10} {'induced (per seed)':>24} {'catalog':>10} {'distinct probes':>18}")
    for topic, s in summary.items():
        fmt = lambda xs: " ".join(f"{c}/{n}" for c, n in xs) or "-"
        print(f"{topic:22} {fmt(s['prior']):>10} {fmt(s['induced']):>24} {fmt(s['catalog']):>10} "
              f"{fmt(s['distinct']):>18}")
    return 0


def summarize(endpoint) -> int:
    rows = [json.loads(l) for l in open(E / "induction.jsonl")]
    print(f"{'topic':22} {'n':>3} {'prior':>7} {'induced seeds':>16} {'catalog':>8} {'probes (distinct/used)':>24}")
    total = collections.Counter()
    for topic, (_case, family, ids) in TOPICS.items():
        mine = [r for r in rows if r["topic"] == topic]
        if not mine:
            continue
        pairs = validation(family)
        truth = [p["truth"] for p in pairs]
        hit = lambda preds, ex=(): sum(p == t for i, (p, t) in enumerate(zip(preds, truth)) if i not in ex)
        prior = hit(mine[0]["prior"])
        induced = [f"{hit(r['predictions'], set(r['overlap']))}/{len(truth) - len(r['overlap'])}" for r in mine]
        cat = hit(predict(catalog_text(ids), pairs, endpoint, 7)) if ids else None
        probes = [f"{len({(_norm(h['a']), _norm(h['b']), h['opt']) for h in r['probes']})}/{len(r['probes'])}"
                  for r in mine]
        print(f"{topic:22} {len(truth):>3} {prior:>7} {' '.join(induced):>16} {str(cat) if cat is not None else '-':>8} "
              f"{' '.join(probes):>24}")
        total["n"] += len(truth)
        total["prior"] += prior
        total["catalog"] += cat or 0
        total["catalog_n"] += len(truth) if ids else 0
        for r in mine:
            total["induced"] += hit(r["predictions"], set(r["overlap"]))
            total["induced_n"] += len(truth) - len(r["overlap"])
            total["no_probe_runs"] += not r["probes"]
            total["runs"] += 1
    print(f"\nprior {total['prior']}/{total['n']} ({total['prior'] / total['n']:.0%}); induced {total['induced']}/"
          f"{total['induced_n']} ({total['induced'] / max(total['induced_n'], 1):.0%}) over {total['runs']} runs; "
          f"catalog {total['catalog']}/{total['catalog_n']}; runs that wrote a rule without probing: "
          f"{total['no_probe_runs']}/{total['runs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
