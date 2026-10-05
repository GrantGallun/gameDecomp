"""Equal-budget evaluation of the baseline checkpoint against a trained adapter.

WHAT IS COMPARED, AND HOW FAIRNESS IS ENFORCED
----------------------------------------------
Both arms run in ONE loaded model, with the LoRA adapter disabled for the baseline and enabled
for the adapter arm. That removes the class of mistake two processes invite: two servers with
different settings, or an adapter accidentally left active in the control arm. Every row records
the adapter state it was produced under, so the claim is auditable rather than asserted.

What is identical across the arms, by construction rather than by convention:

  the frozen tasks        read from the frozen split manifest and bound to the CONTENT HASH the
                          freeze recorded for every record (not just its id)
  the prompt              one construction site (`eval.repair_prompts`), leakage-checked at the
                          model-input boundary on the rendered string
  the sampler             temperature, top_p, seed derivation
  the number of draws     fixed per task, and checked AFTER the run by the gate
  the oracle              the game's own IDO recipe; `solver.byte_certificate.certify` decides
                          exactness
  the budget              `--draws` per task per arm, and both arms get the same

WHAT THE PRIMARY OUTCOME IS
---------------------------
`exact`: a generated C file that compiles and whose OBJECT certifies against the target's.

The verdict is `solver.byte_certificate.certify`, and it is deliberately not `.text` equality.
`.text` carries no relocations, so a candidate that calls a different external function has
byte-identical `.text` -- measured: `return external_a(x);` and `return external_b(x);` both emit
`27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000` with a differing
`R_MIPS_26` at offset 0x8. An audit found that the `.text` oracle called those exact, so a
candidate that calls the wrong function could be counted as a verified repair. `.text` equality
is still recorded per draw as `text_identical`, as a DIAGNOSTIC, because it is exactly the signal
that was mistaken for a verdict.

`score` (differing `.text` bytes) is likewise a DIAGNOSTIC and is never the verdict. Compile
success is reported separately, because a candidate that does not build has no code to compare and
averaging it in as a zero is the failure mode `PIPELINE_MAP` already names.

WHAT THE RUN KEEPS, SO IT CAN BE REPLAYED
-----------------------------------------
Every draw -- exact ones included -- keeps the extracted C, the model's raw output, and the
object and certificate that judged it, copied beside the evaluation with a digest each. The
recorded run stored only a hash for its successes, which is why the audit could not re-derive a
single claimed repair through a corrected verifier.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It cannot see the training split. `--split` defaults to `test`, `train`/`dev` are rejected by the
loader and again by the promotion gate, and the leakage check runs on every rendered prompt
before it is sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = Path.home() / "decomp" / "sbk1"


# --- the frozen panel, and what has to be true before it can be evaluated -------
#
# THE BOUNDARY THIS ENFORCES. A manifest is the experiment's declaration. Before this, the loader
# checked ids, counts and `frozen_at` -- so an audit swapped one task's `input.candidate` for that
# task's own hidden answer AFTER the freeze, and the evaluation ran happily on a panel that no
# longer existed as frozen. Each check below closes one way that can happen, and each raises
# `SystemExit` with the reason, because a silently-accepted tampered panel is worse than a stopped
# run.
#
# THE IMPLEMENTATION IS `eval/frozen_manifest.py`, NOT A COPY HERE. It is re-exported so callers
# that reach it through this module keep working, but there is exactly one implementation: a second
# copy of a security check is a second thing to forget to fix, and the provider training path
# validates the same manifest. Whoever loads a frozen panel applies the same checks.
from eval.frozen_manifest import (  # noqa: F401  (re-exported for existing callers)
    load_frozen_tasks,
    manifest_content_sha256,
    task_prompt,
    verify_compiler_identity,
    verify_content_bound,
    verify_dataset_digest,
    verify_dataset_membership,
    verify_manifest_integrity,
    verify_record_hashes,
    verify_splits,
)

@dataclass
class ArmResult:
    arm: str
    rows: list[dict]
    seconds: float
    draws: int
    prompt_tokens: int
    output_tokens: int

    def as_dict(self) -> dict:
        return {"arm": self.arm, "seconds": round(self.seconds, 1), "draws": self.draws,
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
                "rows": self.rows}


def _draw_slug(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id)) or "task"


def _write_draw_artifacts(out_dir: Path, arm: str, task_id: str, draw: int, *,
                          source: str | None, raw: str | None, obj: Path | None,
                          certificate: dict | None) -> dict:
    """Copy one draw's evidence out of the temporary work directory, and describe it.

    WHY COPY. `evaluate_arm` compiles into a `mkdtemp` directory whose name is random and whose
    lifetime is the process. A path recorded from there is not a reference to anything a later
    reader can open, so every draw's evidence -- the extracted C, the model's RAW output, the
    object the compiler produced, and the certificate that judged it -- is written beside the
    evaluation and recorded as a relative path plus a digest. The digest is what makes the
    reference immutable in the sense that matters: a reader can tell whether the file is still
    the one the run produced.

    Written for EVERY draw, including exact ones. The recorded run stored a hash for its
    successes, which is why the audit could not replay a single claimed repair through a
    corrected verifier.
    """
    root = Path(out_dir).resolve()
    directory = root / "artifacts" / arm / _draw_slug(task_id) / f"draw-{draw}"
    directory.mkdir(parents=True, exist_ok=True)
    refs: dict[str, dict] = {}

    def put(name: str, data: bytes | None, path: Path | None = None) -> None:
        if data is None and path is None:
            return
        target = directory / name
        if path is not None:
            target.write_bytes(Path(path).read_bytes())
        else:
            target.write_bytes(data or b"")
        payload = target.read_bytes()
        refs[name] = {"path": str(target.relative_to(root)), "sha256": hashlib.sha256(payload).hexdigest(),
                      "bytes": len(payload)}

    put("candidate.c", (source or "").encode("utf-8") if source else None)
    put("model_output.txt", (raw or "").encode("utf-8") if raw else None)
    put("candidate.o", None, obj if obj is not None and Path(obj).exists() else None)
    if certificate:
        put("certificate.json", (json.dumps(certificate, indent=2, default=list) + "\n").encode())
    return {"directory": str(directory.relative_to(root)), "files": refs}


def evaluate_arm(*, model, tokenizer, tasks: list[dict], arm: str, adapter: bool,
                 draws: int, temperature: float, top_p: float, max_new_tokens: int,
                 seed: int, max_seconds: float, out_dir: Path,
                 repo: Path | None = None, resolved: dict | None = None,
                 recipe: dict | None = None,
                 deterministic_prepass: bool = False) -> ArmResult:
    """Generate, compile and score one arm. The model's adapter is set for the whole arm.

    `deterministic_prepass` runs the zero-model-call inverse repair first and records a certified
    result as an extra draw (`draw: -1`, `origin: deterministic`). The model draw count is left
    unchanged, so an arm with the pre-pass is compared to one without at the SAME inference budget;
    the pre-pass can add matches but can never trade a model draw away.
    """
    import torch
    from eval.repair_dataset_synth import (
        certify_exact, code_image, compile_unit, resolve_recipe, score_of, object_diff,
        DEFAULT_TARGET)
    from eval.repair_prompts import ASSISTANT_PREFILL
    from eval.deterministic_repair import repairs as deterministic_repairs

    repo = Path(repo) if repo is not None else DEFAULT_REPO
    if resolved is None:
        bundle = resolve_recipe(repo, DEFAULT_TARGET)
        resolved, recipe = bundle["resolved"], bundle["provenance"]
    # PEFT has no "disable the adapter" argument on `set_adapter`: it takes adapter NAMES, and
    # `set_adapter(False)` raises `Adapter False not found` -- it looks up an adapter literally
    # called "False". Turning the trained adapter OFF is `disable_adapter()`, and this is the
    # only thing that separates the control arm from the treatment arm, so getting it wrong
    # would have compared the adapter against itself.
    if adapter:
        model.enable_adapter_layers()
        model.set_adapter("trained")
    else:
        model.disable_adapter_layers()
    if hasattr(model, "eval"):
        model.eval()

    import tempfile
    work = Path(tempfile.mkdtemp(prefix=f"eval-{arm}-"))
    started = time.time()
    rows: list[dict] = []
    prompt_tokens = output_tokens = 0
    draws_made = 0
    deterministic_solved = 0
    deadline = started + max_seconds if max_seconds else None

    for task_index, task in enumerate(tasks):
        if deadline and time.time() >= deadline:
            break
        # THE MODEL INPUT, rendered and leakage-checked at the boundary (`task_prompt`). The
        # dataset-time check proved the RECORD was clean when it was built; only this call can
        # prove the string about to be sent is clean, because it is the string about to be sent.
        prompt = task_prompt(task)
        # `_ids_of`, not `list(...)`: transformers 5 returns a `BatchEncoding` from
        # `apply_chat_template(tokenize=True)`, and `list()` of it yields its KEY NAMES, which
        # reaches `torch.tensor` as strings. The trainer already learned this; the evaluator
        # reuses the same unwrapper so the two cannot disagree about what the prompt was.
        from eval.train_source_repair import _ids_of
        ids = _ids_of(tokenizer.apply_chat_template([{"role": "user", "content": prompt}],
                                                    tokenize=True, add_generation_prompt=True))
        prefill = _ids_of(tokenizer(ASSISTANT_PREFILL, add_special_tokens=False))
        input_ids = list(ids) + list(prefill)
        prompt_tokens += len(input_ids)
        best = None
        per_draw = []
        generated_here = 0
        # The deterministic pre-pass does NOT reduce the draw count. Leaving the budget alone keeps
        # the arm-to-arm comparison equal-budget, which is the only kind that can be reported; it
        # also means the pre-pass can only ever add, never trade a model draw away.

        # THE DETERMINISTIC PRE-PASS, and why it runs FIRST: it spends compiler calls and ZERO model
        # calls, so anything it certifies is a match bought for free and the inference budget is
        # available for the rest. Measured on this panel: it certifies 18 of the 27 frozen tasks on
        # its own, 16 of which the adapter also reaches, so the union is 21 rather than 19.
        #
        # It reads only `input.candidate` -- text the solver was already handed -- and tries every
        # registered inverse, keeping whatever certifies. It never reads `generator_source`, and a
        # repair that certifies is a certified match on exactly the same terms as a model's.
        if deterministic_prepass:
            candidate = (task.get("input") or {}).get("candidate") or ""
            for inverse_name, attempt in deterministic_repairs(candidate):
                case = work / "prepass" / task["task_id"].replace(":", "_") / inverse_name
                case.mkdir(parents=True, exist_ok=True)
                run = compile_unit(repo, resolved, task["function"], attempt, case)
                if not run["compiled"]:
                    continue
                verdict = certify_exact(_target_object(task, work, repo, resolved),
                                        case / f"{task['function']}.o", source=attempt)
                if not verdict.get("exact"):
                    continue
                per_draw.append({
                    "draw": -1, "origin": "deterministic", "inverse": inverse_name,
                    "status": "compiled", "exact": True, "score": 100.0,
                    "source": attempt, "source_sha256": _sha(attempt),
                    "certificate_status": verdict.get("status"),
                    "certificate": {k: verdict.get(k) for k in
                                    ("kind", "scope", "excluded", "error")},
                    "artifacts": _write_draw_artifacts(
                        out_dir, arm, task["task_id"], -1, source=attempt, raw="",
                        obj=case / f"{task['function']}.o", certificate=verdict),
                })
                deterministic_solved += 1
                break

        for draw in range(draws):
            if deadline and time.time() >= deadline:
                break
            # Per-draw seed, derived the same way for both arms so the arms see the same draws.
            material = f"{seed}:{task['task_id']}:{draw}".encode()
            draw_seed = int.from_bytes(hashlib.sha256(material).digest()[:4], "big") % (2 ** 31 - 1)
            torch.manual_seed(draw_seed)
            torch.cuda.manual_seed_all(draw_seed)
            tensor = torch.tensor([input_ids], device="cuda:0")
            try:
                with torch.no_grad():
                    gen = model.generate(
                        input_ids=tensor, attention_mask=torch.ones_like(tensor),
                        max_new_tokens=max_new_tokens, do_sample=True,
                        temperature=max(temperature, 1e-5), top_p=top_p, top_k=0,
                        pad_token_id=tokenizer.pad_token_id,
                        eos_token_id=tokenizer.eos_token_id)
            except torch.OutOfMemoryError:
                per_draw.append({"draw": draw, "status": "out-of-memory"})
                torch.cuda.empty_cache()
                continue
            draws_made += 1
            generated_here += 1
            new_ids = gen[0][len(input_ids):].tolist()
            output_tokens += len(new_ids)
            text = ASSISTANT_PREFILL + tokenizer.decode(new_ids, skip_special_tokens=True)
            source = _extract(text)
            if not source:
                # A draw that produced no usable C is still a draw, and its raw output is what
                # says WHY: a refusal, a truncated block and a syntax error all look identical
                # from a status field.
                per_draw.append({
                    "draw": draw, "status": "no-extract", "raw_head": text[:200],
                    "raw_response": text, "raw_response_sha256": _sha(text),
                    "artifacts": _write_draw_artifacts(
                        out_dir, arm, task["task_id"], draw, source=None, raw=text, obj=None,
                        certificate=None)})
                continue
            run = compile_unit(repo, resolved, task["function"], source, work)
            obj = work / f"{task['function']}.o"
            row = {"draw": draw, "status": "compiled" if run["compiled"] else "compile-error",
                   "exact": False, "score": 0.0, "source_sha256": _sha(source),
                   "stderr": run["stderr"][-400:] if not run["compiled"] else ""}
            certificate = None
            if run["compiled"]:
                # THE VERDICT IS THE CERTIFICATE, NOT `.text` EQUALITY. `.text` carries no
                # relocations, so a candidate that calls a DIFFERENT external function has
                # byte-identical `.text` and equality would certify it. Measured with the real
                # recipe: `return external_a(x);` and `return external_b(x);` both produce
                # 27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000 with a
                # differing R_MIPS_26 at 0x8. `certify_exact` compares allocated sections AND
                # relocation expressions, so it catches that while still tolerating the unstable
                # debug/ABI metadata that made whole-object comparison useless.
                verdict = certify_exact(_target_object(task, work, repo, resolved), obj,
                                        source=source)
                certificate = verdict
                row["exact"] = bool(verdict.get("exact"))
                row["certificate_status"] = verdict.get("status")
                row["certificate"] = {k: verdict.get(k) for k in
                                      ("kind", "scope", "excluded", "error")}
                # `.text` similarity stays as a DIAGNOSTIC for describing a failed attempt.
                target_code = code_image(_target_object(task, work, repo, resolved))
                candidate_code = code_image(obj)
                if target_code is not None and candidate_code is not None:
                    comparison = object_diff(target_code, candidate_code)
                    row["score"] = score_of(comparison, True)
                    row["differing_bytes"] = comparison["differing_bytes"]
                    row["text_identical"] = bool(comparison["identical"])
                if verdict.get("status") == "unverified":
                    row["status"] = "unverified"
            # REPLAY EVIDENCE FOR EVERY DRAW, exact ones included. `source` is the C, `text` is
            # the model's raw output, and the artifacts are the object and the certificate that
            # judged it -- copied out of the temporary directory, which is not a durable
            # reference. This is what the recorded run lacked: its successes kept a hash, so no
            # claimed repair could be replayed through a corrected verifier.
            row["artifacts"] = _write_draw_artifacts(
                out_dir, arm, task["task_id"], draw, source=source, raw=text,
                obj=obj if run["compiled"] else None,
                certificate=certificate if run["compiled"] else None)
            row["raw_response"] = text
            row["raw_response_sha256"] = _sha(text)
            # KEEP THE CANDIDATE TEXT ITSELF for EVERY draw, successes included. The first version
            # stored only `source_sha256` for a successful draw, so the claimed repairs could not
            # be replayed through a corrected verifier -- which is exactly what the audit then
            # needed and could not do. A verdict nobody can re-derive is not evidence.
            row["source"] = source
            row["raw_response_head"] = text[:600]
            if not row["exact"]:
                # WHY this draw failed, recorded beside the C that produced it. A failed branch is
                # the negative half of the dataset, and the three kinds below need different
                # curriculum responses: a compile error is a C89 problem, a relocation-only
                # residual is a wrong call target, and a code difference is a codegen problem.
                # The recorded run could not tell them apart, because a draw that compiled kept
                # only a hash, and a relocation-only failure would have been recorded as a
                # SUCCESS by the `.text` oracle.
                row["failure_kind"] = (
                    "compile-error" if not run["compiled"] else
                    "certificate-unverified" if row.get("certificate_status") == "unverified" else
                    "relocation-only" if row.get("text_identical") else
                    "code-differs")
            per_draw.append(row)
            if best is None or (row.get("exact"), row.get("score") or 0.0) > \
                    (best.get("exact"), best.get("score") or 0.0):
                best = row
        rows.append({"task_id": task["task_id"], "family": task["family"],
                     "mutation": task["mutation"], "arm": arm, "adapter_active": adapter,
                     "draws": len(per_draw), "draws_generated": generated_here,
                     "draws_declared": draws,
                     "prompt_sha256": task.get("prompt_sha256"),
                     "compiler_recipe_sha256": (recipe or {}).get("command_sha256"),
                     "per_draw": per_draw,
                     "exact": bool(best and best.get("exact")),
                     # The two ways a task can be exact are recorded separately, because they cost
                     # different things: a deterministic match spends compiler calls and NO model
                     # calls, and reporting only the sum would hide which one did the work.
                     "deterministic": any(d.get("origin") == "deterministic" for d in per_draw),
                     "model_exact": any(d.get("origin") != "deterministic" and d.get("exact")
                                        for d in per_draw),
                     "best_score": (best or {}).get("score"),
                     "compiled": any(d.get("status") == "compiled" for d in per_draw)})
        print(json.dumps({k: v for k, v in rows[-1].items() if k != "per_draw"}), flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{arm}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return ArmResult(arm=arm, rows=rows, seconds=time.time() - started, draws=draws_made,
                     prompt_tokens=prompt_tokens, output_tokens=output_tokens)


_TARGET_CACHE: dict[str, Path] = {}


def _target_object(task: dict, work: Path, repo: Path, resolved: dict) -> Path:
    """The target OBJECT, compiled from the generator's answer.

    An object, not `.text` bytes: the verdict is a certificate over sections and relocations, so
    it needs the object. Recompiled here rather than trusted from the dataset record, because a
    comparing a fresh candidate against a stored hash is not a comparison. Cached per task so the
    answer is compiled once per arm rather than once per draw.
    """
    key = task["task_id"]
    if key in _TARGET_CACHE:
        return _TARGET_CACHE[key]
    from eval.repair_dataset_synth import compile_unit
    answer_dir = work / "target"
    answer_dir.mkdir(parents=True, exist_ok=True)
    run = compile_unit(repo, resolved, task["function"], task["generator_source"], answer_dir)
    if not run["compiled"]:
        raise SystemExit(f"the answer for {task['task_id']} no longer compiles: "
                         f"{run['stderr'][-300:]}")
    path = answer_dir / f"{task['function']}.o"
    _TARGET_CACHE[key] = path
    return path


def _extract(text: str) -> str:
    from solver import llm
    if llm.is_refusal(text):
        return ""
    code = llm.extract_c(text)
    return code if code and llm.FUNC_DEF_RE.search(code) else ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(arm: ArmResult) -> dict:
    rows = arm.rows
    calls = sum(row["draws"] for row in rows)
    compiled = sum(1 for row in rows if row["compiled"])
    exact = sum(1 for row in rows if row["exact"])
    scored = [row["best_score"] for row in rows if row["best_score"] is not None]
    deterministic = sum(1 for row in rows if row.get("deterministic"))
    model_exact = sum(1 for row in rows if row.get("model_exact"))
    return {
        "arm": arm.arm,
        "tasks": len(rows),
        "model_calls": calls,
        "tasks_exact": exact,
        "best_of_budget_exact_rate": round(exact / len(rows), 4) if rows else None,
        # Reported separately from `tasks_exact`, because the two are not the same purchase: a
        # deterministic match costs compiler calls and no model calls. Summing them silently would
        # let a free inversion be read as model capability.
        "tasks_exact_deterministic": deterministic,
        "tasks_exact_by_model": model_exact,
        "tasks_exact_by_both": sum(1 for row in rows
                                   if row.get("deterministic") and row.get("model_exact")),
        "compile_success_tasks": compiled,
        "compile_rate": round(compiled / len(rows), 4) if rows else None,
        "mean_best_score": round(sum(scored) / len(scored), 3) if scored else None,
        "prompt_tokens": arm.prompt_tokens,
        "output_tokens": arm.output_tokens,
        "seconds": round(arm.seconds, 1),
        "exact_per_1000_calls": round(1000.0 * exact / calls, 3) if calls else None,
        "compiler_calls": calls,
        "adapter_active": bool(rows and rows[0].get("adapter_active")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--adapter", type=Path, required=True,
                    help="a PUBLISHED adapter directory (the PUBLISHED.json marker must exist)")
    ap.add_argument("--base", type=Path, default=Path.home() / "decomp" / "models" /
                    "qwen2.5-coder-7b")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--draws", type=int, default=2)
    ap.add_argument("--deterministic-prepair", action="store_true",
                    help="run the zero-model-call inverse repair first and record a certified "
                         "result as an extra draw; the model draw count is NOT reduced, so the "
                         "comparison against an arm without it stays equal-budget")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-new-tokens", type=int, default=512)
    ap.add_argument("--max-model-len", type=int, default=3072)
    ap.add_argument("--max-seconds-per-arm", type=float, default=1800.0)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO,
                    help="the game repository the frozen IDO recipe is resolved from")
    ap.add_argument("--seed", type=int, default=20260920)
    args = ap.parse_args(argv)

    from eval import resource_limits
    from eval import posttraining_gate as gate
    from eval import train_source_repair as tsr
    from eval.repair_dataset_synth import DEFAULT_TARGET, resolve_recipe

    published = tsr.published_adapter(args.adapter)
    if not published:
        raise SystemExit(
            f"{args.adapter} has no {tsr.PUBLISHED_MARKER}. An unpublished adapter is not "
            f"evidence: it may be the initialisation, or the product of an interrupted run.")

    # THE COMPILER IDENTITY IS RESOLVED ONCE, HERE, and then both checked against the freeze and
    # used for the compiles. Resolving it separately in the loader and in the arm would let the
    # thing that was verified and the thing that ran be two different recipes.
    bundle = resolve_recipe(args.repo, DEFAULT_TARGET)
    tasks, manifest = load_frozen_tasks(args.manifest, args.dataset, split=args.split,
                                        limit=args.limit, recipe=bundle["provenance"])
    if not tasks:
        raise SystemExit(f"no {args.split} tasks in the frozen manifest")
    for note in manifest.get("load_notes") or []:
        print(json.dumps({"manifest_note": note}), flush=True)
    print(json.dumps({"frozen_tasks": len(tasks), "manifest_sha256":
                      manifest["manifest_sha256"], "draws_per_task": args.draws,
                      "compiler": bundle["provenance"]["compiler"],
                      "command_sha256": bundle["provenance"]["command_sha256"]}), flush=True)

    limits = resource_limits.apply()
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    tokenizer = AutoTokenizer.from_pretrained(str(args.base))
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        str(args.base), dtype=torch.bfloat16, device_map="cuda:0",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16))
    model = PeftModel.from_pretrained(model, str(args.adapter), adapter_name="trained",
                                      is_trainable=False)

    args.out.mkdir(parents=True, exist_ok=True)
    common = dict(draws=args.draws, temperature=args.temperature, top_p=args.top_p,
                  max_new_tokens=args.max_new_tokens, seed=args.seed,
                  max_seconds=args.max_seconds_per_arm, repo=args.repo,
                  resolved=bundle["resolved"], recipe=bundle["provenance"],
                  deterministic_prepass=args.deterministic_prepass)
    baseline = evaluate_arm(model=model, tokenizer=tokenizer, tasks=tasks, arm="baseline",
                            adapter=False, out_dir=args.out, **common)
    trained = evaluate_arm(model=model, tokenizer=tokenizer, tasks=tasks, arm="adapter",
                           adapter=True, out_dir=args.out, **common)

    baseline_map = {row["task_id"]: row for row in baseline.rows}
    adapter_map = {row["task_id"]: row for row in trained.rows}
    # THE FROZEN SPECIFICATION, handed to the gate with the results. The gate cannot see the
    # manifest, so without this it has no panel to check coverage against and no budget to check
    # the draw counts against -- which is how a probe with 12 ids, 0 baseline draws and one
    # apparent gain used to return `promote`.
    all_frozen = list(manifest["task_ids"][args.split])
    spec = gate.EvaluationSpec(
        expected_task_ids=tuple(task["task_id"] for task in tasks),
        split=args.split,
        draws_per_task=args.draws,
        # A `--limit`ed run is a SUBSET: still useful to report, explicitly ineligible to promote.
        kind="frozen" if len(tasks) == len(all_frozen) else "subset",
        manifest_sha256=manifest["manifest_sha256"],
        dataset_sha256=hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest())
    outcome = gate.decide(baseline=baseline_map, adapter=adapter_map, spec=spec)
    payload = {
        "schema_version": 3,
        "stage": "compiler-verified-post-training/evaluation",
        "split": args.split,
        "manifest": {"path": str(args.manifest), "sha256": manifest["manifest_sha256"],
                     "frozen_at": manifest["frozen_at"],
                     "load_notes": manifest.get("load_notes") or []},
        "dataset": {"path": str(args.dataset),
                    "sha256": hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest()},
        "adapter": {"path": str(args.adapter), "published": published,
                    "dir_digest": tsr.dir_digest(args.adapter)},
        "base": tsr.dir_digest(args.base),
        "compiler": bundle["provenance"],
        "verifier": {
            "exactness": "solver.byte_certificate.certify",
            "scope": ("allocated text/data/BSS sections and relocation expressions under the "
                      "same link environment; not a final-link or whole-ROM claim"),
            "text_section_equality": ("diagnostic only (`text_identical` per draw): `.text` "
                                      "carries no relocations, so it called a changed callee "
                                      "exact and is not the verdict")},
        # Whether a free deterministic pass ran, and what it is worth. Without this in the payload a
        # reader cannot tell whether `tasks_exact` was bought with inference or with compiles.
        "deterministic_prepass": {
            "enabled": bool(args.deterministic_prepass),
            "model_calls_used": 0 if args.deterministic_prepass else None,
            "note": ("records a certified result as an extra draw; the model draw count is "
                     "unchanged so the arm comparison stays equal-budget"),
        },
        "budget": {"draws_per_task_per_arm": args.draws,
                   "max_new_tokens": args.max_new_tokens,
                   "model_calls_per_arm": args.draws * len(tasks)},
        "evaluation_spec": spec.as_dict(),
        "resource_limits": limits.as_dict(),
        "primary_outcome": ("object-exact tasks (solver.byte_certificate.certify over the game's "
                            "own IDO recipe: allocated sections AND relocation expressions)"),
        "baseline": summarize(baseline),
        "adapter_arm": summarize(trained),
        "gate": outcome.as_dict(),
        "baseline_rows": baseline_map,
        "adapter_rows": adapter_map,
    }
    (args.out / "evaluation.json").write_text(json.dumps(payload, indent=2) + "\n",
                                              encoding="utf-8")
    print(json.dumps({k: payload[k] for k in
                      ("primary_outcome", "baseline", "adapter_arm", "gate")}, indent=2))
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
