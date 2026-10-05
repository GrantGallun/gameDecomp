"""Bounded local compiler research with persistent, mechanically checked observations."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import sys
import time
import uuid

from tools.synthetic_corpus import FAMILIES, generate

ROOT = Path(__file__).resolve().parents[1]
METRICS = ['instructions', 'branches', 'backward_branches', 'calls', 'or_ops', 'andi_ops',
           'fpu_ops', 'narrow_stack_loads', 'stack_spills', 'saved_register_count', 'jump_table']
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['title', 'rationale', 'before', 'after', 'metric', 'relation'],
          'properties': {**{k: {'type': 'string'} for k in ('title', 'rationale', 'before', 'after')},
                         'metric': {'type': 'string', 'enum': METRICS},
                         'relation': {'type': 'string', 'enum': ['greater', 'less', 'equal']}}}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temp.replace(path)


def append_json(path, value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


class Halt(Exception):
    pass


class Budget:
    def __init__(self, *, minutes, max_calls, max_compiles, stop, run_id, heartbeat=lambda: None):
        for name, value, ceiling in [('minutes', minutes, 120), ('max_calls', max_calls, 48),
                                     ('max_compiles', max_compiles, 288)]:
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f'{name} must be an integer from 1 to {ceiling}')
        self.started = time.monotonic()
        self.deadline = self.started + minutes * 60
        self.limits = {'calls': max_calls, 'compiles': max_compiles}
        self.used = {'calls': 0, 'compiles': 0}
        self.stop, self.run_id, self.heartbeat = stop, run_id, heartbeat

    def check(self):
        if (read_json(self.stop, {}) or {}).get('run_id') == self.run_id:
            raise Halt('Stopped by user')
        if time.monotonic() >= self.deadline:
            raise Halt('Run time budget reached')

    def reserve(self, kind):
        self.check()
        if self.used[kind] >= self.limits[kind]:
            raise Halt(('Model call' if kind == 'calls' else 'Compiler') + ' budget reached')
        self.used[kind] += 1
        self.heartbeat()

    def execute(self, command, *, timeout, input_text=None):
        self.check()
        # Model instructions never supply a command. Each call is one fixed tool.
        proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                start_new_session=os.name != 'nt',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        end = time.monotonic() + timeout
        sent = False
        try:
            while True:
                self.check()
                if time.monotonic() >= end:
                    raise TimeoutError(f'Tool exceeded {timeout}s')
                self.heartbeat()
                try:
                    stdout, stderr = proc.communicate(input_text if not sent else None, timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    sent = True
            if proc.returncode:
                raise RuntimeError((stdout or stderr or f'Tool exited {proc.returncode}')[-2000:])
            return stdout
        finally:
            if os.name != 'nt':
                # A failed wrapper may have exited while its compiler descendants
                # still hold this group. Reap the group even after the leader exits.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif proc.poll() is None:
                proc.kill()
            if proc.poll() is None:
                proc.communicate(timeout=5)


def validate_hypothesis(value):
    if not isinstance(value, dict) or set(value) != set(SCHEMA['required']):
        raise ValueError('Expected exactly title, rationale, before, after, metric and relation')
    if value['metric'] not in METRICS or value['relation'] not in ('greater', 'less', 'equal'):
        raise ValueError('Unknown measurement or relation')
    for key, limit in [('title', 160), ('rationale', 1200), ('before', 6000), ('after', 6000)]:
        text = value[key]
        if not isinstance(text, str) or not 1 <= len(text) <= limit:
            raise ValueError(f'{key} exceeds its length limit or is empty')
    for key in ('before', 'after'):
        source = value[key]
        if '{{K}}' not in source:
            raise ValueError(f'{key} must contain the literal placeholder {{{{K}}}}; do not substitute a number')
        # Deliberately small C subset: no directives, strings, escape continuations,
        # trigraphs/digraphs, comments or assembly. The source is compiled, never run.
        if (any(c in source for c in '#"\'\\?') or '%:' in source or '/*' in source or '//' in source
                or re.search(r'\b(?:asm|__asm__|__asm|GLOBAL_ASM|INCLUDE_ASM)\b', source)
                 or not re.search(r'\bsyn_probe\s*\(', source)
                or not source.isascii()):
            raise ValueError('Use plain C89 syn_probe, {{K}}, and no directives, comments, strings or asm')
    if value['before'] == value['after']:
        raise ValueError('The two probes must differ')
    return value


def measurement(row, metric):
    feat = row['features']
    return len(set(feat['saved_registers']) - {'ra'}) if metric == 'saved_register_count' else int(feat[metric])


def verdict(proposal, rows):
    if any(not side.get('compiled') for row in rows for side in (row['before'], row['after'])):
        return 'compile_failed'
    for row in rows:
        a, b = (measurement(row[side], proposal['metric']) for side in ('before', 'after'))
        if not {'greater': b > a, 'less': b < a, 'equal': b == a}[proposal['relation']]:
            return 'counterexample'
    if (len(rows) != 3 or [r['phase'] for r in rows] != ['discovery', 'confirmation', 'confirmation']
            or len({r['k'] for r in rows}) != 3):
        return 'incomplete'
    return 'synthetic_confirmed'


def compact_record(row):
    return {'id': row['id'], 'proposal': row['proposal'], 'status': row['status'], 'measurements': [
        {k: v for k, v in r.items() if k in ('phase', 'k')} | {
            side: {'compiled': r[side]['compiled'], 'features': r[side].get('features'),
                   'stderr': r[side].get('stderr', '')[-500:]}
            for side in ('before', 'after')} for r in row['measurements']]}


class Research:
    def __init__(self, state, view, stop, *, repo, model='qwen2.5-coder:14b', endpoint='auto',
                 minutes=30, max_calls=12, run_id=None, tool=None, cluster=None):
        self.state, self.view, self.stop, self.repo = map(Path, (state, view, stop, repo))
        self.run_id = run_id or uuid.uuid4().hex
        if not re.fullmatch('[a-zA-Z0-9-]{1,64}', self.run_id):
            raise ValueError('Invalid run identity')
        self.run_dir = self.state/'runs'/self.run_id
        self.tool_override = tool
        self.model, self.endpoint = model, endpoint
        self.cluster = cluster
        self.budget = Budget(minutes=minutes, max_calls=max_calls, max_compiles=max_calls*6,
                             stop=self.stop, run_id=self.run_id, heartbeat=self.publish)
        self.last_publish = 0
        self.data = {'run_id': self.run_id, 'status': 'starting', 'phase': 'Checking local tools',
                     'model': model, 'minutes': minutes, 'max_calls': max_calls,
                     'max_compiles': max_calls*6, 'started_at': time.time(), 'experiments': 0,
                     'confirmed': 0, 'counterexamples': 0, 'errors': 0, 'recent': [],
                     'api_spend_usd': 0, 'notebook': str(self.state/'notebook.jsonl'),
                     'run_path': str(self.run_dir), 'scope': 'synthetic compiler observations'}

    def publish(self, force=False):
        if not force and time.monotonic() - self.last_publish < 1:
            return
        self.data.update(self.budget.used, updated_at=time.time(),
                         elapsed_seconds=round(time.monotonic()-self.budget.started, 1))
        atomic_json(self.view, self.data)
        self.last_publish = time.monotonic()

    def event(self, kind, **fields):
        append_json(self.run_dir/'events.jsonl', {'event': kind, 'at': time.time(), **fields})

    def tool(self, op, payload, timeout=60):
        if self.tool_override:
            return self.tool_override(op, payload, timeout=timeout)
        value = json.loads(self.budget.execute([sys.executable, '-m', 'eval.research_tools', op],
                           timeout=timeout, input_text=json.dumps(payload)))
        if value.get('error'):
            raise RuntimeError(value['error'])
        return value

    def memory(self, identity):
        recent, seen = deque(maxlen=6), set()
        notebook = self.state/'notebook.jsonl'
        if notebook.exists():
            for line in notebook.open(encoding='utf-8'):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get('recipe_id') == identity:
                    seen.add(row['pair_id'])
                    recent.append(compact_record(row))
        return recent, seen

    def prompt(self, memory, round_no):
        # FAILURES CHOOSE THE TOPIC WHEN A CLUSTER IS SUPPLIED. Without this the focus rotates through
        # the declared FAMILIES, which is research by schedule: useful for exercising the mechanism and
        # useless for the question this experiment asks, which is whether a REAL failure can be turned
        # into a finding that transfers. The cluster comes from `eval.research_demand`, whose features
        # are computed deterministically from stored attempts -- the model is told what is failing, it
        # is not asked to guess what matters.
        if self.cluster:
            focus = (f"real stored failure cluster: {self.cluster.get('error_class')} / "
                     f"{self.cluster.get('residual_kind')} residual / "
                     f"{self.cluster.get('size_bucket')} functions "
                     f"({self.cluster.get('attempts')} attempts)")
            example = (self.cluster.get('focus_example') or
                       'u32 syn_probe(u32 x) { return x + {{K}}; }')
        else:
            family = list(FAMILIES)[round_no % len(FAMILIES)]
            _, example = generate(family, 100 + round_no)
            focus = FAMILIES[family].capability
        system = ('You research IDO MIPS1 -O2 code generation for matching decompilation. '
                  'Propose one small falsifiable comparison of two C89 source templates. '
                  'Only JSON with title, rationale, before, after, metric, relation. '
                  'Each template defines one function named syn_probe, uses literal {{K}} '
                  'as a positive integer parameter (2..31), and must compile independently. '
                  'Use provided typedefs s8,u8,s16,u16,s32,u32,f32,f64. No includes, directives, '
                  'comments, strings, asm, execution, shell, or tool calls. External function '
                  'declarations are allowed. Declarations must precede statements (C89). '
                  'Avoid undefined behavior. Source may be semantically different: this is a '
                  'code-generation experiment, not a proven equivalence rewrite. Make a specific '
                  'mechanical prediction: after metric is greater/less/equal to before. '
                  'Metrics: ' + ', '.join(METRICS) + '. saved_register_count excludes ra; '
                  'stack_spills counts non-saved-register sw to sp, including argument slots. '
                  'One discovery and two unseen confirmation values will be compiled. '
                  'Do not repeat a recorded pair. Use counterexamples to refine your hypothesis. '
                  'Do not claim real-game improvement or universal proof from finite probes. '
                  'Write each C template on ONE LINE. Do not emit backslash-n sequences. '
                  'Both before and after MUST literally contain {{K}}. Do not replace it with '
                  'a number and do not just copy the unparameterized generator example.')
        context, remaining = [], 15000
        for entry in reversed(memory):
            # Limit context even when a notebook contains many large historical probes.
            size = len(json.dumps(entry))
            if size <= remaining:
                context.insert(0, entry)
                remaining -= size
        return [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({
            'suggested_focus': focus,
            'failure_cluster': self.cluster or 'none supplied; research the suggested focus',
            'generator_example': example.replace('#include "common.h"', ''),
            'format_example': {'title': 'Mask versus add', 'rationale': 'Compare immediate AND instructions',
                'before': 'u32 syn_probe(u32 x) { return x + {{K}}; }',
                'after': 'u32 syn_probe(u32 x) { return x & {{K}}; }',
                'metric': 'andi_ops', 'relation': 'greater'},
            'notebook': context, 'last_error': self.data.get('last_error'),
            'instruction': 'Choose a useful hypothesis and predict a measured effect. Return the JSON.'})}]

    def run(self):
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.publish(True)
        self.event('run_started', config=self.data)
        try:
            info = self.tool('inspect', {'endpoint': self.endpoint, 'model': self.model}, 30)
            resolved = self.tool('recipe', {'repo': str(self.repo)}, 60)
            self.data.update(model_digest=info['digest'], endpoint=info['endpoint'],
                             recipe_id=resolved['identity'], status='running')
            atomic_json(self.run_dir/'recipe.json', resolved)
            memory, seen = self.memory(resolved['identity'])
            consecutive_errors = 0
            while True:
                self.budget.reserve('calls')
                call = self.budget.used['calls']
                self.data['phase'] = f'Local model: proposal {call}'
                self.publish(True)
                payload = {**info, 'model': self.model, 'format': SCHEMA,
                           'messages': self.prompt(memory, call-1)}
                atomic_json(self.run_dir/f'call-{call:03}-request.json', payload)
                self.event('call_started', call=call)
                try:
                    response = self.tool('chat', payload, 180)
                    atomic_json(self.run_dir/f'call-{call:03}-response.json', response)
                    self.event('call_finished', call=call, eval_count=response.get('eval_count'))
                    proposal = validate_hypothesis(json.loads(response['message']['content']))
                    self.data.pop('last_error', None)
                    pair_id = hashlib.sha256((proposal['before']+'\n'+proposal['after']).encode()).hexdigest()
                    if pair_id in seen:
                        raise ValueError('Repeated source pair; use a different experiment')
                    seen.add(pair_id)
                    self.experiment(proposal, pair_id, resolved, memory)
                    consecutive_errors = 0
                except Halt as exc:
                    self.event('call_interrupted', call=call, error=str(exc))
                    raise
                except (ValueError, KeyError, TypeError, RuntimeError, OSError, TimeoutError) as exc:
                    self.data['errors'] += 1
                    self.data['last_error'] = str(exc)[-1500:]
                    self.event('call_failed', call=call, error=str(exc))
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        raise Halt('Stopped after three consecutive errors')
        except Halt as exc:
            self.data.update(status='stopped' if 'user' in str(exc) else 'finished', phase=str(exc))
        except Exception as exc:
            self.data.update(status='failed', phase='Research worker failed', last_error=str(exc)[-1500:])
            self.event('run_failed', error=str(exc))
        finally:
            self.data['finished_at'] = time.time()
            self.publish(True)
            atomic_json(self.run_dir/'receipt.json', self.data)
            self.event('run_finished', status=self.data['status'], used=self.budget.used)
        return self.data

    def experiment(self, proposal, pair_id, resolved, memory):
        experiment_id = f'{self.run_id}-{self.budget.used["calls"]:03}'
        row = {'id': experiment_id, 'pair_id': pair_id, 'recipe_id': resolved['identity'],
               'proposal': proposal, 'status': 'incomplete', 'measurements': [],
               'model_digest': self.data['model_digest'], 'game_transfer': 'untested'}
        self.event('experiment_started', **row)
        self.data['current_title'] = proposal['title']
        # Freeze the prediction BEFORE independently drawing confirmation inputs.
        ks = [3, *random.SystemRandom().sample([k for k in range(2, 32) if k != 3], 2)]
        halt = None
        try:
            for n, k in enumerate(ks):
                pair = {'phase': 'discovery' if n == 0 else 'confirmation', 'k': k}
                for side in ('before', 'after'):
                    self.budget.reserve('compiles')
                    self.data['phase'] = f'{pair["phase"].title()}: K={k}, {side}'
                    self.publish(True)
                    source = proposal[side].replace('{{K}}', str(k))
                    work = self.run_dir/experiment_id/f'{n}-{side}'
                    self.event('compile_started', experiment=experiment_id, phase=pair['phase'],
                               k=k, side=side, source=source, work=str(work))
                    try:
                        outcome = self.tool('compile', {'recipe': resolved, 'repo': str(self.repo),
                                            'source': source, 'work': str(work)}, 60)
                    except Halt:
                        self.event('compile_interrupted', experiment=experiment_id, k=k, side=side)
                        raise
                    except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
                        outcome = {'compiled': False, 'stderr': str(exc)}
                    self.event('compile_finished', experiment=experiment_id, k=k, side=side, **outcome)
                    pair[side] = outcome
                row['measurements'].append(pair)
                if verdict(proposal, row['measurements']) in ('compile_failed', 'counterexample'):
                    break  # Spend confirmation compiles only on surviving hypotheses.
            row['status'] = verdict(proposal, row['measurements'])
        except Halt as exc:
            halt = exc
        finally:
            # Keep failures and interruptions, not just successes. These are observations,
            # never active solver rules or training examples labelled byte-exact repair.
            row['finished_at'] = time.time()
            append_json(self.state/'notebook.jsonl', row)
            atomic_json(self.run_dir/f'{experiment_id}.json', row)
            compact = compact_record(row)
            memory.append(compact)
            self.data['recent'] = [compact, *self.data['recent']][:6]
            self.data['experiments'] += 1
            self.data['confirmed'] += int(row['status'] == 'synthetic_confirmed')
            self.data['counterexamples'] += int(row['status'] == 'counterexample')
            self.data['errors'] += int(row['status'] == 'compile_failed')
            self.event('experiment_finished', id=experiment_id, status=row['status'])
            self.publish(True)
        if halt:
            raise halt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--state', type=Path, default=Path.home()/'decomp/local-research')
    ap.add_argument('--repo', type=Path, default=Path.home()/'decomp/sbk1')
    ap.add_argument('--view', type=Path, required=True)
    ap.add_argument('--stop', type=Path, required=True)
    ap.add_argument('--run-id')
    ap.add_argument('--minutes', type=int, default=30)
    ap.add_argument('--max-calls', type=int, default=12)
    ap.add_argument('--model', default='qwen2.5-coder:14b')
    args = ap.parse_args()
    if sys.platform != 'linux':
        ap.error('Run the worker in WSL Linux')
    if any(str(p.resolve()).startswith('/mnt/') for p in (args.state, args.repo)):
        ap.error('Compiler repo and research state must be on the WSL filesystem')
    args.state.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (args.state/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            ap.error('A local research worker is already running')
        lab = Research(**vars(args))
        # Graceful SIGTERM preserves receipts and kills the active child group.
        def stopping(signum, frame):
            atomic_json(lab.stop, {'run_id': lab.run_id})
        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
        result = lab.run()
        print(json.dumps({k: result[k] for k in ('status', 'phase', 'calls', 'compiles')}))
        return int(result['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
