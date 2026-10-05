"""Throwaway, offline FP8 kernel experiment; outputs are not training examples.

Run one variant per process. No solver, KB, campaign, or production settings change.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import traceback

ROOT = Path('/mnt/c/Code/gameDecomp')
MODEL = Path('/home/grant/decomp/models/qwen2.5-coder-7b')
ASM = Path('/home/grant/decomp/sbk1/nonmatchings/updateCourseSelectCourseList/target_object_dump.s')


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def gpu():
    p = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.used,utilization.gpu,power.draw,clocks.sm,clocks.mem,temperature.gpu', '--format=csv,noheader'], capture_output=True, text=True)
    return p.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--graphs', action='store_true')
    parser.add_argument('--graphs-only', action='store_true')
    parser.add_argument('--attention', default=None)
    parser.add_argument('--linear', default='auto')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--tokens', type=int, default=512)
    parser.add_argument('--cases', nargs='+', default=['short:1', 'long:1', 'long:4'])
    parser.add_argument('--max-batched-tokens', type=int, default=8192)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(VLLM_USE_V2_MODEL_RUNNER='0', VLLM_USE_FLASHINFER_SAMPLER='0',
                      HF_HOME='/home/grant/decomp/hf-home', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                      CUDA_HOME='/home/grant/decomp/serve-venv/lib/python3.12/site-packages/nvidia/cu13')
    os.environ['PATH'] = os.environ['CUDA_HOME'] + '/bin:' + os.environ['PATH']
    record = {'schema': 'kernel-probe/1', 'variant': vars(args), 'ok': False,
              'training_eligible': False, 'quality_evaluation': False,
              'model': str(MODEL), 'model_config_sha256': sha(MODEL.joinpath('config.json').read_text()),
              'versions': {p: importlib.metadata.version(p) for p in ['vllm', 'torch', 'flashinfer-python']},
              'gpu_before': gpu(), 'runs': []}
    def save():
        (out / (args.name + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    save()
    try:
        import torch
        from vllm import LLM, SamplingParams
        from vllm.config import KernelConfig
        kwargs = dict(model=str(MODEL), quantization='fp8', dtype='bfloat16',
                      gpu_memory_utilization=0.72, max_model_len=12288, max_num_seqs=6,
                      max_num_batched_tokens=args.max_batched_tokens,
                      enforce_eager=not args.graphs, enable_prefix_caching=False,
                      disable_log_stats=False, seed=0, trust_remote_code=False,
                      kernel_config=KernelConfig(linear_backend=args.linear))
        if args.graphs_only:
            kwargs['enforce_eager'] = False
            kwargs['compilation_config'] = {'mode': 0, 'cudagraph_mode': 'FULL_DECODE_ONLY'}
        if args.attention:
            kwargs['attention_config'] = {'backend': args.attention}
        record['config'] = {**kwargs, 'kernel_config': {'linear_backend': args.linear}}
        started = time.perf_counter()
        llm = LLM(**kwargs)
        record['load_seconds'] = time.perf_counter() - started
        record['gpu_after_load'] = gpu()
        save()
        tokenizer = llm.get_tokenizer()
        assembly = ASM.read_text()
        record['assembly_sha256'] = hashlib.sha256(assembly.encode()).hexdigest()
        record['assembly_path'] = str(ASM)
        prompts = {}
        for label, limit in [('short', 24), ('long', 190)]:
            body = ('Translate this MIPS assembly excerpt to C. Use explicit byte offsets for unknown layouts. '
                    'Output C only. This is an excerpt, so preserve unknown names.\n\n' + '\n'.join(assembly.splitlines()[:limit]))
            rendered = tokenizer.apply_chat_template([{'role': 'user', 'content': body}], tokenize=False, add_generation_prompt=True) + '```c\n'
            ids = tokenizer.encode(rendered, add_special_tokens=False)
            prompts[label] = ids
            (out / (label + '_prompt.txt')).write_text(rendered)
        record['prompts'] = {k: {'tokens': len(v), 'token_ids_sha256': sha(v)} for k, v in prompts.items()}
        record['sampling'] = dict(temperature=0.0, top_p=1.0, top_k=-1, repetition_penalty=1.0,
                                  seed=1000, max_tokens=args.tokens, ignore_eos=True)
        save()

        counter = 0
        def run(label, concurrency, repeat, warmup=False):
            nonlocal counter
            counter += 1
            sampling = SamplingParams(**record['sampling'])
            engine = llm.llm_engine
            ids = [f'{args.name}-{counter}-{i}' for i in range(concurrency)]
            first = {}
            finished = {}
            outputs = {}
            step_events = []
            torch.cuda.synchronize()
            started = time.perf_counter()
            for request_id in ids:
                engine.add_request(request_id, {'prompt_token_ids': prompts[label]}, sampling)
            while engine.has_unfinished_requests():
                batch = engine.step()
                now = time.perf_counter()
                step_events.append(now - started)
                for output in batch:
                    if output.outputs and output.outputs[0].token_ids:
                        first.setdefault(output.request_id, now)
                        outputs[output.request_id] = output
                    if output.finished:
                        finished[output.request_id] = now
            torch.cuda.synchronize()
            wall = time.perf_counter() - started
            rows = []
            for request_id in ids:
                output = outputs[request_id].outputs[0]
                tokens = list(output.token_ids)
                assert len(tokens) == args.tokens, (len(tokens), args.tokens)
                decode_s = finished[request_id] - first[request_id]
                rows.append({'id': request_id, 'tokens': len(tokens), 'ttft_s': first[request_id] - started,
                             'decode_s': decode_s, 'decode_tps': (len(tokens)-1)/decode_s,
                             'token_ids_sha256': sha(tokens), 'token_ids': tokens,
                             'text': output.text, 'finish_reason': output.finish_reason})
            row = {'prompt': label, 'concurrency': concurrency, 'repeat': repeat, 'warmup': warmup,
                   'wall_s': wall, 'output_tps_including_prefill': sum(r['tokens'] for r in rows)/wall,
                   'decode_tps_per_request_median': statistics.median(r['decode_tps'] for r in rows),
                   'step_elapsed_s': step_events, 'requests': rows, 'gpu_after': gpu()}
            record['runs'].append(row)
            save()
            print(json.dumps({k:v for k,v in row.items() if k not in ('requests', 'step_elapsed_s')}), flush=True)

        for case in args.cases:
            label, raw_concurrency = case.split(':')
            concurrency = int(raw_concurrency)
            run(label, concurrency, -1, warmup=True)
            for rep in range(args.repeats):
                run(label, concurrency, rep)
        record['ok'] = True
    except Exception as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
        record['traceback'] = traceback.format_exc()
        print(record['traceback'], flush=True)
    finally:
        record['gpu_final'] = gpu()
        save()
    return 0 if record['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
