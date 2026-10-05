"""Real local-model/IDO smoke for the tool-learning boundary (no game source)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from eval.research_tools import inspect_model, request
from eval.tool_learning import Limits, run_experiment, write_json
from eval.tool_learning_oracle import Oracle


def synthetic_cases():
    """Freeze templates before authoring; reference C is held by target creation only."""
    rows = []
    # v5 (2026-10-04): four development SHAPES of the residual. With two copies of `return v >> k;` the v4 author
    # (gpt-oss:20b) repaired both on its first try with a tool that only matched that statement, and declined every
    # evaluation case. None of these shapes uses an operator the evaluation expressions use with a constant.
    for index, body in enumerate(('return value >> 3;', 'return value >> 7;', 'return (value >> 2) * 3;',
                                  'int t; t = value >> 6; return t + t;')):
        name = f'syn_dev_{index}'
        before = f'int {name}(int value) {{ {body} }}'
        after = before.replace('int value', 'unsigned int value')
        rows.append((f'dev{index}', 'scalar_shift', 'dev', before, after))
    # Different expression shapes, types, parameter names and shift counts.
    for index, (expression, parameters) in enumerate([
        ('(amount >> 5) + 9', 'int amount'),
        ('(input >> 11) ^ 3', 'int input'),
        ('(bits >> 17) & 1023', 'int bits'),
        ('(datum >> distance)', 'int datum, unsigned int distance'),
        ('(number >> 9) - 13', 'int number'),
        ('(word >> 13) | 1', 'int word'),
    ]):
        before = f'int syn_eval_{index}({parameters}) {{ return {expression}; }}'
        after = before.replace('(' + parameters, '(' + 'unsigned ' + parameters, 1)
        rows.append((f'eval{index}', 'composed_shift', 'eval', before, after))
    # Already exact controls must remain covered; an unrelated residual remains in
    # the denominator even if the new tool has no mechanism for it.
    for index in range(3):
        source = f'int syn_control_{index}(int signed_value) {{ return signed_value >> {index + 2}; }}'
        rows.append((f'control{index}', 'retention_control', 'eval', source, source))
    for index in range(3):
        before = f'int syn_other_{index}(int value) {{ return value + {index + 2}; }}'
        after = f'int syn_other_{index}(int value) {{ return value * {index + 5}; }}'
        rows.append((f'other{index}', 'unrelated_arithmetic', 'eval', before, after))
    return rows


def lora_proposer(adapter, url, author_receipt):
    """The 7B student (tools.lora_serve: base, or a trained adapter by name) as tool author. No JSON-schema
    constraint exists on this server, so the reply's first {...} object is parsed; a malformed reply is a failed
    proposal like any other."""
    from tools.lora_serve.client import InferenceClient
    client = InferenceClient(url, timeout=600.0)
    author_round = 0

    def propose(messages):
        nonlocal author_round
        start = time.monotonic()
        r = client.chat(messages, model=None if adapter == 'base' else adapter, temperature=.4, seed=author_round,
                        max_tokens=3000)
        text = r.text
        cost = {'seconds': time.monotonic() - start,
                'tokens': (r.receipt.get('prompt_tokens') or 0) + (r.receipt.get('completion_tokens') or 0),
                'model': {'lora_serve': url, 'adapter': adapter}}
        write_json(author_receipt.with_name(f'author-{author_round}.json'), {'response': text, 'cost': cost})
        author_round += 1
        first, last = text.find('{'), text.rfind('}')
        return (text[first:last + 1] if 0 <= first < last else text), cost
    return propose


def local_proposer(model, endpoint, author_receipt, think='low'):
    info = inspect_model(endpoint, model)
    author_round = 0
    schema = {'type': 'object', 'additionalProperties': False,
              'required': ['name', 'rationale', 'code'],
              'properties': {k: {'type': 'string'} for k in ('name', 'rationale', 'code')}}
    # Reasoning ON by default: the 2026-10-03 14B runs had it off (think=False) and repeated an identical tool.
    think_value = {'false': False, 'true': True}.get(think, think)

    def propose(messages):
        nonlocal author_round
        start = time.monotonic()
        response = request(info['endpoint'], '/api/chat', {
            'model': model, 'messages': messages, 'stream': False, 'think': think_value,
            'format': schema, 'keep_alive': '5m',
            'options': {'num_ctx': 16384, 'num_predict': 8000, 'temperature': .4 + .1 * author_round,
                        'seed': 20261004 + author_round, 'num_thread': 4}}, timeout=900)
        cost = {'seconds': time.monotonic() - start,
                'tokens': response.get('prompt_eval_count', 0) + response.get('eval_count', 0),
                'prompt_tokens': response.get('prompt_eval_count'),
                'output_tokens': response.get('eval_count'), 'model': info}
        write_json(author_receipt.with_name(f'author-{author_round}.json'),
                   {'model': info, 'response': response, 'cost': cost})
        author_round += 1
        # No repair after seeing evaluation. Malformed author output is a failed run.
        return response['message']['content'], cost
    return propose


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compiler', type=Path,
                        default=Path('/home/grant/decomp/sbk1/tools/ido-recomp/linux'))
    parser.add_argument('--model', default='gpt-oss:20b',
                        help='Ollama model; or lora:<adapter|base> for the 7B student on tools.lora_serve')
    parser.add_argument('--endpoint', default='auto')
    parser.add_argument('--lora-url', default='http://127.0.0.1:8101')
    parser.add_argument('--think', default='low', help="Ollama 'think': low|medium|high|true|false")
    parser.add_argument('--author-calls', type=int, default=6)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if str(out).startswith('/mnt/'):
        parser.error('compile artifacts must be on the WSL filesystem')
    out.mkdir(parents=True, exist_ok=False)
    specimens = synthetic_cases()
    write_json(out / 'preregistration.json', {
        'scope': 'exposed synthetic integration smoke; repeated templates, no fresh held-out or game-transfer claim',
        'splits': [{'id': r[0], 'family': r[1], 'split': r[2]} for r in specimens],
        'compiles_per_case': 8, 'seconds_per_case': 30,
        'baseline': 'solver.rewrites.propose; root plus bounded one-step candidates',
        'author_model': args.model, 'author_calls': args.author_calls, 'think': args.think,
        'note': 'shape-disjoint synthetic templates; not independent broad task-family transfer'})
    maker = Oracle(args.compiler, [])
    cases, construction = [], []
    for identity, family, split, source, target_source in specimens:
        target = out / 'targets' / (identity + '.o')
        result = maker.compile(target_source, target)
        construction.append({'id': identity, 'receipt': result})
        write_json(out / 'target-construction.json', construction)
        if not result['compiled']:
            raise RuntimeError(f'target construction failed for {identity}: {result}')
        cases.append(dict(id=identity, family=family, split=split, source=source, target=str(target)))
    write_json(out / 'panel.json', cases)
    oracle = Oracle(args.compiler, cases)
    proposer = (lora_proposer(args.model[5:], args.lora_url, out / 'author.json') if args.model.startswith('lora:')
                else local_proposer(args.model, args.endpoint, out / 'author.json', think=args.think))
    result = run_experiment(cases, proposer, oracle, out / 'experiment', limits=Limits(author_calls=args.author_calls))
    print(json.dumps({k: result[k] for k in ('scope', 'development', 'evaluation', 'decision', 'all_compiles')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
