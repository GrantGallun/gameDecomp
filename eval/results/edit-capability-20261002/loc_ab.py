"""Inference A/B: does the localization block (solver/line_map.localize) help repair? No training involved.

    python3 loc_ab.py build                         -> E/loc-ab/blocks.json (CPU: candidate-side compiles only)
    python3 loc_ab.py run --arm lora:mixed --variant plain|loc     (tools.lora_serve must be serving the adapter)
    python3 loc_ab.py run --arm ollama:gpt-oss:20b --variant plain|loc
    python3 loc_ab.py grade                         -> per arm x variant: compiled-to-target counts + paired gains

Items: the logic pilot's frozen exam explain tasks (logic-pilot-v3/exam.json). The loc variant appends the block to
the SAME prompt; everything else (model, decoding, grader) is identical. Grading compiles the answer's edit script
in the task's checked context (logic_grade), so any correct fix counts, not only the reference one.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

import public_plant as pp

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
P = Path.home() / "decomp/experiments/edit-capability-20261002"
L = P / "logic-pilot-v3"
OUT = P / "loc-ab"
CTX = P / "public/context-v3.jsonl"


def exam_tasks():
    ids = set(json.loads((L / "exam.json").read_text())["ids"])
    return [t for t in map(json.loads, open(L / "logic-v3/tasks.jsonl")) if t["id"] in ids and t["kind"] == "logic-explain"]


def build():
    from solver import line_map
    OUT.mkdir(parents=True, exist_ok=True)
    rows = {r["id"]: r for r in map(json.loads, open(CTX))}
    bmap = pp.builds()

    def one(t):
        row = rows[t["row_id"]]
        build_ = bmap[(f"{row['repository']}.{row['variant']}", row["file"])]
        obj = pp.compile_tu(build_, row["context"] + "\n" + row["perturbed_fn"], "loc")
        if obj is None:
            return t["id"], None
        listing = pp.listing(obj, row["function"])
        dump = subprocess.run([pp.ws_objdump.find_objdump_executable(), "-dl", *pp.ws_objdump.OBJDUMP_ARGS[1:],
                               str(obj)], capture_output=True, text=True, check=True).stdout

        def compile_listing(text):
            o = pp.compile_tu(build_, text, "locx")
            return pp.listing(o, row["function"]) if o else None
        try:
            loc = line_map.localize(row["context"], row["perturbed_fn"], row["function"], row["target"], listing,
                                    dump, (row["context"] + "\n").count("\n"), compile_listing)
        except ValueError:
            return t["id"], None
        return t["id"], {"loc": loc, "block": line_map.render_localization(loc)}

    tasks = exam_tasks()
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        blocks = dict(ex.map(one, tasks))
    (OUT / "blocks.json").write_text(json.dumps(blocks, indent=1))
    print(json.dumps({"tasks": len(tasks), "with_block": sum(1 for v in blocks.values() if v and v["block"]),
                      "skipped": dict(line_map.SKIPPED)}))


def prompt_for(t, blocks, variant):
    if variant == "plain":
        return t["prompt"]
    b = (blocks.get(t["id"]) or {}).get("block") or ""
    return t["prompt"] + ("\n" + b + "\n" if b else "")


def run(arm, variant):
    blocks = json.loads((OUT / "blocks.json").read_text())
    tasks = exam_tasks()
    out = OUT / f"answers_{arm.replace(':', '_')}_{variant}.jsonl"
    done = {json.loads(line)["id"] for line in out.read_text().splitlines()} if out.exists() else set()
    todo = [t for t in tasks if t["id"] not in done]
    if arm.startswith("lora:"):
        from tools.lora_serve.client import InferenceClient
        client = InferenceClient("http://127.0.0.1:8101", timeout=900.0)
        adapter = arm[5:]

        def ask(t):
            r = client.chat([{"role": "user", "content": prompt_for(t, blocks, variant)}],
                            model=None if adapter == "base" else adapter, temperature=0.0, seed=20261003,
                            max_tokens=512)
            return {"id": t["id"], "answer": r.text}
        jobs = 4
    else:
        from solver import llm
        endpoint, model = llm.host(), arm[len("ollama:"):]

        def ask(t):
            text, _meta = llm.generate(endpoint, model, prompt_for(t, blocks, variant), timeout=600, num_predict=4000,
                                       think="low", temperature=0.0, seed=20261003,
                                       cache_dir=str(OUT / "llm-cache"), cache_namespace=f"loc-ab-{variant}")
            return {"id": t["id"], "answer": text}
        jobs = 3
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex, open(out, "a") as f:
        for row in ex.map(ask, todo):
            f.write(json.dumps(row) + "\n")
            f.flush()
    print(out)


def grade():
    import logic_grade as lg
    tasks = {t["id"]: t for t in exam_tasks()}
    rows = {r["id"]: r for r in map(json.loads, open(CTX))}
    bmap = pp.builds()
    results = {}
    for path in sorted(OUT.glob("answers_*.jsonl")):
        answers = {r["id"]: r["answer"] for r in map(json.loads, open(path))}
        results[path.stem[len("answers_"):]] = {tid: bool(lg.grade(t, answers.get(tid, ""), rows, bmap)["rows"])
                                                for tid, t in tasks.items()}
    summary = {}
    for name, res in sorted(results.items()):
        summary[name] = sum(res.values())
        print(f"{name:32} {sum(res.values()):3} / {len(res)}")
    for name in results:
        if name.endswith("_loc") and name[:-4] + "_plain" in results:
            a, b = results[name[:-4] + "_plain"], results[name]
            gained = sum(1 for k in a if b[k] and not a[k])
            lost = sum(1 for k in a if a[k] and not b[k])
            print(f"{name[:-4]:32} plain -> loc: +{gained} -{lost}")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("build", "run", "grade"))
    ap.add_argument("--arm")
    ap.add_argument("--variant", choices=("plain", "loc"))
    a = ap.parse_args()
    {"build": build, "grade": grade}.get(a.cmd, lambda: run(a.arm, a.variant))()


if __name__ == "__main__":
    main()
