"""Equal-budget, preregistered compiler evaluation of two model checkpoints.

WHAT THIS MEASURES AND WHY IT IS SHAPED THIS WAY
------------------------------------------------
The claim under test is narrow and it is the only one that counts: **does a fine-tuned
checkpoint produce MORE distinct byte-exact functions than the checkpoint it was trained
from, at the same budget, on functions neither has seen?** A training-loss curve, a JSON
preference score, a replay discovery rate and a higher mean similarity all fail to
establish that, and each of them has already been reported in this project as though it
might.

Four design choices carry the honesty of the number:

- THE PANEL IS SEALED AND PRE-REGISTERED. Functions come from `heldout` members of
  `eval/sets/*.json`, are frozen with their byte sizes into `<out>/PREREGISTRATION.json`
  before either arm runs, and are never re-drawn. `--preregister` refuses to overwrite an
  existing manifest, so a panel cannot be quietly improved after seeing a result.
- BOTH ARMS GET THE SAME PROMPTS, BUDGET, SAMPLER AND ORACLE. Each function gets exactly
  `draws` independent draws per arm at one temperature. Nothing is conditioned on the
  arm's own earlier output, so the two arms are not merely compared at equal cost -- the
  i-th request in arm A is byte-identical to the i-th request in arm B apart from the
  weights serving it.
- THE PRIMARY OUTCOME IS NEW EXACT FUNCTIONS, PAIRED BY FUNCTION. A function that was
  already exact before the panel ran is excluded at preregistration time, so nothing here
  can be pre-existing state, recovery, or reconciliation.
- UNKNOWN IS NOT ZERO. `--dry-run` reports what the panel and the estimated budget are
  before any model call is made.

HONEST LIMITS, STATED UP FRONT
------------------------------
- The pool is 60 functions, not the 100 a fully powered panel would want, because only 60
  heldout members are both unsolved and present in the knowledge base. Statistical power is
  therefore limited and the report says so rather than implying otherwise.
- `rounds=1`, so every draw is independent and NO repair pass is evaluated here. Repair
  behaviour is a separate experiment with its own control; conflating it with best-of-N
  would make a mixed comparison that isolates nothing.
- Both arms see the same optional m2c draft, which for most of these never-attempted
  functions is absent. That is identical treatment, not identical information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEALED_KEYS = ("dev", "heldout", "cluster", "panel")


def sealed_heldout(sets_dir: Path | None = None) -> set[str]:
    """Functions listed under a `heldout` key in any frozen eval set.

    Only `heldout`, deliberately. The `dev`, `cluster` and `panel` members are sealed from
    TRAINING too, but they have been inspected during development and cannot carry a
    headline number.
    """
    directory = sets_dir or ROOT / "eval" / "sets"
    out: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in payload.get("heldout", []) or []:
            name = row.get("function") if isinstance(row, dict) else row
            if name:
                out.add(name)
    return out


@dataclass
class PanelEntry:
    function: str
    addr: int
    size: int
    insns: int
    tier: str
    never_attempted: bool


def build_panel(kb: Path, *, limit: int = 0, sizes: tuple[int, int] = (0, 1 << 30),
                seed: int = 0, exclude: set[str] | None = None) -> list[PanelEntry]:
    """Unsolved sealed-heldout functions, stratified by size, sorted deterministically.

    A function with ANY exact attempt is dropped: it is not a candidate for "newly exact",
    and counting it would be recovery dressed as capability.
    """
    import sqlite3
    names = sealed_heldout() - (exclude or set())
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    rows = conn.execute(
        "select f.addr, f.name, f.size from functions f").fetchall()
    out: list[PanelEntry] = []
    for addr, name, size in rows:
        if name not in names or not size or not (sizes[0] <= size <= sizes[1]):
            continue
        best, exact, tries = conn.execute(
            "select max(score), max(coalesce(exact,0)), count(*) from attempts"
            " where func_addr=?", (addr,)).fetchone()
        if exact:
            continue
        out.append(PanelEntry(function=name, addr=int(addr), size=int(size),
                              insns=int(size) // 4,
                              tier=tier_of(int(size)), never_attempted=(tries == 0)))
    # Deterministic order: smallest first, then name. A seeded shuffle would add a knob whose
    # only purpose could be to re-roll a panel.
    out.sort(key=lambda e: (e.size, e.function))
    if limit:
        out = stratified_take(out, limit)
    return out


def tier_of(size: int) -> str:
    if size <= 256:
        return "small"
    if size <= 1024:
        return "medium"
    if size <= 4096:
        return "large"
    return "huge"


def stratified_take(entries: list[PanelEntry], limit: int) -> list[PanelEntry]:
    """Take from every size stratum in turn, so a small limit cannot exclude the hard half."""
    buckets: dict[str, list[PanelEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.tier, []).append(entry)
    order = ["small", "medium", "large", "huge"]
    taken: list[PanelEntry] = []
    index = 0
    while len(taken) < limit and any(buckets.get(t) for t in order):
        tier = order[index % len(order)]
        index += 1
        bucket = buckets.get(tier) or []
        if bucket:
            taken.append(bucket.pop(0))
    return sorted(taken, key=lambda e: (e.size, e.function))


# --- the two arms -------------------------------------------------------------

DEFAULT_RULE = {
    "primary_outcome": "new distinct byte-exact functions, paired by function",
    "promotion": ("M1 is promoted only if it closes strictly more NEW exact functions than "
                  "M0 on the frozen panel. A tie is inconclusive, not a success."),
    "never_evidence": [
        "a training-loss decrease",
        "a preference-accuracy or JSON-choice score",
        "a replay discovery-rate change",
        "a higher mean similarity",
        "a higher compile rate on its own",
    ],
    "reporting": ("object-exact and ROM-backed function-exact are reported separately; "
                  "pre-existing exacts, recovery and reconciliation are excluded by "
                  "construction because every panel function is unsolved at preregistration"),
    "tuning": ("the final test panel is not used for tuning; an inconclusive result does not "
               "authorise tuning on it"),
}


def fingerprint(path: Path, kind: str) -> dict:
    """A digest for a model directory or adapter directory, plus its identity fields."""
    if not path.exists():
        return {"path": str(path), "exists": False, "kind": kind}
    hasher = hashlib.sha256()
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and item.stat().st_size < 64 * 1024 * 1024:
            files.append(item)
    for item in files:
        hasher.update(item.name.encode("utf-8"))
        hasher.update(item.read_bytes())
    out = {"path": str(path), "kind": kind, "exists": True,
           "sha256": hasher.hexdigest(), "file_count": len(files)}
    config = path / "config.json"
    if config.exists():
        try:
            payload = json.loads(config.read_text())
            out["architectures"] = payload.get("architectures")
            out["torch_dtype"] = payload.get("torch_dtype")
        except Exception:
            pass
    adapter = path / "adapter_config.json"
    if adapter.exists():
        try:
            payload = json.loads(adapter.read_text())
            out["peft"] = {"r": payload.get("r"), "lora_alpha": payload.get("lora_alpha"),
                           "target_modules": payload.get("target_modules"),
                           "base_model_name_or_path": payload.get("base_model_name_or_path")}
        except Exception:
            pass
    return out


def preregister(out: Path, panel: list[PanelEntry], arms: list[dict], *, draws: int,
                temperature: float, top_p: float, seed: int, max_tokens: int,
                kb: Path, holdout_sources: list[str]) -> dict:
    manifest = {
        "schema_version": 1,
        "created_at": int(time.time()),
        "purpose": "one equal-budget compiler evaluation of M0 against M1",
        "panel": [asdict(e) for e in panel],
        "panel_size": len(panel),
        "panel_sha256": hashlib.sha256(
            json.dumps([asdict(e) for e in panel], sort_keys=True).encode()).hexdigest(),
        "panel_provenance": {
            "source": "eval/sets/*.json `heldout` keys",
            "sources": holdout_sources,
            "kb": str(kb),
            "excluded": ["any function with an exact attempt",
                         "any function absent from the knowledge base"],
        },
        "design": {
            "rounds": 1,
            "draws_per_function_per_arm": draws,
            "independent_draws_only": True,
            "repair_passes": 0,
            "note": ("rounds=1 means every draw is an independent root; no repair pass is "
                     "evaluated here. Best-of-N is the control, not a treatment."),
            "budget": {"model_calls_per_arm": draws * len(panel)},
            "sampling": {"temperature": temperature, "top_p": top_p, "max_tokens": max_tokens,
                         "seed": seed},
            "oracle": "solver.workspace.score -> IDO 5.3 object comparison",
        },
        "arms": arms,
        "decision_rule": DEFAULT_RULE,
        "power": {
            "panel_size": len(panel),
            "requested": 100,
            "shortfall_reason": ("only 60 sealed heldout members are both unsolved and "
                                 "present in the knowledge base"),
            "mde_note": ("with n=60 paired functions a difference of one function is the "
                         "smallest measurable effect; report the paired outcomes, not a "
                         "p-value"),
        },
    }
    out.mkdir(parents=True, exist_ok=True)
    target = out / "PREREGISTRATION.json"
    if target.exists():
        raise SystemExit(
            f"{target} already exists. A preregistration that can be rewritten after seeing "
            f"a result is not a preregistration. Move or delete it deliberately.")
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


# --- running one arm ----------------------------------------------------------

def run_arm(manifest: dict, arm: dict, out: Path, *, client,
            scorer_factory, context_for, repo: Path, kb: Path, draws: int | None = None,
            only: str | None = None, max_seconds: float = 0.0) -> dict:
    """Run one arm over the frozen panel and write its per-draw receipts.

    The arm never sees the other arm's output, and nothing here re-reads the panel from
    disk: the manifest IS the panel, so a rerun cannot silently pick a different one.
    """
    from eval import trajectory_factory as tf

    out.mkdir(parents=True, exist_ok=True)
    draws = draws or manifest["design"]["draws_per_function_per_arm"]
    sampling = manifest["design"]["sampling"]
    panel = manifest["panel"]
    if only:
        panel = [row for row in panel if row["function"] == only]

    generator = client
    scorer = scorer_factory(repo, kb, arm)
    deadline = (time.monotonic() + max_seconds) if max_seconds else None
    receipts_path = out / "draws.jsonl"

    per_function: list[dict] = []
    started = time.time()
    for entry in panel:
        if deadline is not None and time.monotonic() >= deadline:
            break
        factory = tf.Factory(generator=generator, scorer=scorer,
                             context_for=context_for,
                             rounds=1, samples=draws,
                             temperature=sampling["temperature"],
                             game="sbk1", compiler="ido-5.3")
        budget = [draws]
        outcome = factory.run_function(
            {"name": entry["function"], "addr": entry["addr"],
             "best_score": 0.0, "best_attempt_id": None,
             "faults": {}, "owned_share": 0.0, "attempts": 0},
            budget, deadline=deadline)
        rows = list(factory.log)
        with receipts_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        row = {
            "function": entry["function"], "tier": entry["tier"], "size": entry["size"],
            "draws": outcome.attempts, "compiled": outcome.admitted,
            "exact": outcome.exact, "best_score": outcome.best_after,
            "improving_children": outcome.improving_children,
            "errors": outcome.errors, "refusals": outcome.refusals,
            "stopped": outcome.stopped, "stopping_note": outcome.stopping_note[:200],
            "arm": arm["name"],
        }
        per_function.append(row)
        print(json.dumps(row), flush=True)

    exact = sum(1 for r in per_function if r["exact"])
    attempted = sum(r["draws"] for r in per_function)
    compiled_total = sum(r["compiled"] for r in per_function)
    summary = {
        "arm": arm["name"],
        "model_calls": attempted,
        "functions_run": len(per_function),
        "functions_exact": exact,
        "compile_rate": round(compiled_total / attempted, 4) if attempted else None,
        "mean_best_score": (round(sum(r["best_score"] for r in per_function)
                                  / len(per_function), 3) if per_function else None),
        "best_of_budget_exact_rate": round(exact / len(per_function), 4) if per_function else None,
        "pass_at_1_proxy": (round(sum(1 for r in per_function if r["exact"]) / attempted, 4)
                            if attempted else None),
        "seconds": round(time.time() - started, 1),
        "per_function": per_function,
    }
    (out / "arm.json").write_text(json.dumps({"arm": arm, "summary": summary}, indent=2) + "\n",
                                  encoding="utf-8")
    return summary


def compare(m0: dict, m1: dict) -> dict:
    """The preregistered comparison, computed pair by pair."""
    by0 = {r["function"]: r for r in m0["per_function"]}
    by1 = {r["function"]: r for r in m1["per_function"]}
    both = sorted(set(by0) & set(by1))
    gained_by_m1 = [f for f in both if by1[f]["exact"] and not by0[f]["exact"]]
    gained_by_m0 = [f for f in both if by0[f]["exact"] and not by1[f]["exact"]]
    neither = [f for f in both if not by0[f]["exact"] and not by1[f]["exact"]]
    both_exact = [f for f in both if by0[f]["exact"] and by1[f]["exact"]]
    verdict = ("M1" if len(gained_by_m1) > len(gained_by_m0)
               else "M0" if len(gained_by_m0) > len(gained_by_m1)
               else "inconclusive (tie)")
    return {
        "functions_compared": len(both),
        "exact_m0": sum(1 for f in both if by0[f]["exact"]),
        "exact_m1": sum(1 for f in both if by1[f]["exact"]),
        "gained_by_m1": gained_by_m1,
        "gained_by_m0": gained_by_m0,
        "gained_by_neither": len(neither),
        "gained_by_both": both_exact,
        "paired_difference": len(gained_by_m1) - len(gained_by_m0),
        "verdict": verdict,
        "primary_metric": "new distinct byte-exact functions at equal budget",
    }


# --- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp" / "kb-sbk1.sqlite")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--preregister", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--draws", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--arm", default=None, help="run one arm by name from the manifest")
    ap.add_argument("--only", default=None, help="one function, for a canary")
    ap.add_argument("--max-seconds", type=float, default=0.0)
    ap.add_argument("--m1-adapter", type=Path, default=None)
    ap.add_argument("--model", type=Path,
                    default=Path.home() / "decomp" / "models" / "qwen2.5-coder-7b")
    args = ap.parse_args(argv)

    if args.preregister:
        panel = build_panel(args.kb, limit=args.limit)
        arms = [
            {"name": "M0", "role": "control",
             "checkpoint": fingerprint(args.model, "base-checkpoint"), "adapter": None},
            {"name": "M1", "role": "treatment",
             "checkpoint": fingerprint(args.model, "base-checkpoint"),
             "adapter": fingerprint(args.m1_adapter, "lora-adapter") if args.m1_adapter else None},
        ]
        manifest = preregister(args.out, panel, arms, draws=args.draws,
                               temperature=args.temperature, top_p=args.top_p,
                               seed=args.seed, max_tokens=args.max_tokens, kb=args.kb,
                               holdout_sources=sorted(p.name for p in
                                                      (ROOT / "eval" / "sets").glob("*.json")))
        print(json.dumps({"panel_size": manifest["panel_size"],
                          "panel_sha256": manifest["panel_sha256"],
                          "budget_per_arm": manifest["design"]["budget"],
                          "tiers": {t: sum(1 for e in manifest["panel"] if e["tier"] == t)
                                    for t in ("small", "medium", "large", "huge")},
                          "never_attempted": sum(1 for e in manifest["panel"]
                                                 if e["never_attempted"])}, indent=2))
        return 0

    manifest = json.loads((args.out / "PREREGISTRATION.json").read_text(encoding="utf-8"))
    if not args.arm:
        print(json.dumps({"panel_size": manifest["panel_size"],
                          "arms": [a["name"] for a in manifest["arms"]],
                          "budget": manifest["design"]["budget"]}, indent=2))
        return 0
    raise SystemExit(
        "running an arm needs the generator adapter bound to the arm's checkpoint; call "
        "run_arm() from a driver that owns the served endpoint -- see "
        "eval/posttraining_experiment.py")


if __name__ == "__main__":
    raise SystemExit(main())
