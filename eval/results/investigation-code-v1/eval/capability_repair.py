"""Bounded engineering experiments in private copies of the solver.

Tasks specify a trusted reproduction and existing regression tests. The local
model proposes edits only to explicitly scoped implementation modules. A repair
is eligible for a new frozen campaign only after the reproduction and tests
pass. Nothing edits the running campaign or installs a candidate automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from eval.agentrepair import _atomic_json
from solver import llm, modelrepair


PROTECTED = {'solver/byte_certificate.py', 'solver/function_boundary.py',
             'solver/frontend_check.py', 'solver/workspace.py',
             'eval/integration_gate.py', 'eval/completion_campaign.py',
             'eval/capability_repair.py'}


def file_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or path.suffix != '.py':
        raise ValueError('repair path must be a relative Python module')
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('repair path escapes private workspace')
    return target


def validate_task(task, project):
    if not task.get('issue_key') or not task.get('evidence'):
        raise ValueError('engineering task needs issue identity and reproduction evidence')
    modules = task.get('modules', [])
    if not 1 <= len(modules) <= 3:
        raise ValueError('engineering task needs one to three implementation modules')
    for name in modules:
        if name in PROTECTED or Path(name).parts[0] not in {'solver', 'oracle'}:
            raise ValueError('verifiers, controllers and tests cannot be edited by engineering worker')
        if not file_path(project, name).is_file():
            raise ValueError('unknown implementation module')
    # Reproductions are trusted pre-existing tests, not executable model text.
    for key in ('reproduce', 'regression', 'transfer'):
        selectors = task.get(key, [])
        if not selectors or len(selectors) > 64:
            raise ValueError('task needs bounded reproduction, regression and transfer tests')
        for selector in selectors:
            name = selector.split('::', 1)[0]
            if not name.startswith('tests/test_') or not file_path(project, name).is_file():
                raise ValueError('test selector must identify a pre-existing project test')
            if any(c in selector for c in ('\n', '\r', '\x00')):
                raise ValueError('invalid test selector')
    if set(task['reproduce']) & set(task['transfer']):
        raise ValueError('transfer checks must differ from the motivating reproduction')


def copy_project(project, output):
    output.mkdir(parents=True, exist_ok=False)
    for name in ('solver', 'eval', 'tests', 'oracle', 'kb', 'miner', 'patterns'):
        if (project / name).is_dir():
            shutil.copytree(project / name, output / name,
                ignore=shutil.ignore_patterns('__pycache__', 'results', '.pytest_cache', '*.sqlite', '*.db'))
    if (project / 'pytest.ini').exists():
        shutil.copy2(project / 'pytest.ini', output / 'pytest.ini')


def check(root, selectors, timeout, tag):
    command = [sys.executable, '-m', 'pytest', '-q', *selectors,
               '--basetemp', str(root / ('.pytest-' + tag)), '-o', 'cache_dir=' + str(root / '.pytest_cache')]
    start = time.monotonic()
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout)
        text = result.stdout + result.stderr
        # Pytest exit 0 with all checks skipped is not validation.
        import re
        passed = bool(re.search(r'\b[1-9][0-9]* passed\b', text))
        return {'command': command, 'returncode': result.returncode, 'passed': result.returncode == 0 and passed,
                'output': text[-16000:], 'seconds': round(time.monotonic() - start, 3)}
    except subprocess.TimeoutExpired:
        return {'command': command, 'passed': False, 'status': 'timeout'}


def apply(root, proposal, allowed):
    edits = proposal.get('edits')
    if not isinstance(edits, list) or not 1 <= len(edits) <= 8:
        raise ValueError('engineering proposal needs one to eight edits')
    staged = {}
    changed = 0
    for edit in edits:
        name, old, new = edit.get('path'), edit.get('old'), edit.get('new')
        if name not in allowed or not isinstance(old, str) or not isinstance(new, str) or not old or old == new:
            raise ValueError('invalid or out-of-scope engineering edit')
        original = staged.get(name, file_path(root, name).read_text())
        if original.count(old) != 1:
            raise ValueError('engineering edit does not uniquely identify its parent')
        staged[name] = original.replace(old, new, 1)
        changed += len(old) + len(new)
    if changed > 20000:
        raise ValueError('engineering patch too large')
    for name, source in staged.items():
        compile(source, name, 'exec')
    for name, source in staged.items():
        file_path(root, name).write_text(source)
    return {name: hashlib.sha256(source.encode()).hexdigest() for name, source in staged.items()}


def run(project, task, output, *, model='gpt-oss:20b', endpoint=None, calls=2, timeout=180, provider=None):
    validate_task(task, project)
    if calls < 0 or calls > 8:
        raise ValueError('engineering calls must be between zero and eight')
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'code'
    copy_project(project, root)
    original = {name: file_path(root, name).read_text() for name in task['modules']}
    report = {'kind': 'isolated-capability-repair', 'task': task,
              'implementation_hashes': {name: hashlib.sha256(code.encode()).hexdigest() for name, code in original.items()},
              'attempts': [], 'status': 'reproduction_pending', 'deployed': False}
    report['baseline'] = check(root, task['reproduce'], timeout, 'baseline')
    if report['baseline']['passed'] or report['baseline'].get('returncode') != 1:
        report['status'] = 'not_reproduced' if report['baseline']['passed'] else 'reproduction_unavailable'
        _atomic_json(output / 'report.json', report)
        return report
    provider = provider or modelrepair.OllamaProvider()
    for index in range(calls):
        prompt = ('Repair this tool capability in an isolated solver copy. Do not weaken validation, '
                  'skip unsupported cases as successes, read reference C, or change tests. '
                  'Return JSON {"hypothesis":"cause and predicted effect","edits":'
                  '[{"path":"allowed module","old":"unique old substring","new":"replacement"}]}.\n'
                  + json.dumps({'task': task, 'baseline': report['baseline'], 'previous': report['attempts'][-1:],
                                'modules': original}, sort_keys=True))
        # Use the same provider abstraction/accounting as source repair.
        request = modelrepair.GenerationRequest(model=model, endpoint=endpoint or llm.host(), prompt=prompt,
            timeout=timeout, think='low', num_thread=12, temperature=0.2,
            num_predict=6000, seed=20260910 + index, cache_dir=None, cache_namespace='capability-repair-v1')
        row = {'index': index, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        try:
            raw, meta = provider.generate(request)
            row.update(raw_response=raw, generation=meta)
            from solver.toolagent import _object
            proposal = _object(raw)
            if not isinstance(proposal, dict):
                raise ValueError('no engineering JSON proposal')
            for name, source in original.items():
                file_path(root, name).write_text(source)
            row['changed'] = apply(root, proposal, set(task['modules']))
            row['reproduction'] = check(root, task['reproduce'], timeout, f'{index}-reproduce')
            if row['reproduction']['passed']:
                row['regression'] = check(root, task['regression'], timeout, f'{index}-regression')
                row['transfer'] = check(root, task['transfer'], timeout, f'{index}-transfer')
                if row['regression']['passed'] and row['transfer']['passed']:
                    report['status'] = 'validated_candidate_requires_frozen_fork'
            row['hypothesis'] = proposal.get('hypothesis')
        except (ValueError, OSError, RuntimeError, SyntaxError) as exc:
            row.update(status='rejected', error=str(exc))
        report['attempts'].append(row)
        _atomic_json(output / 'report.json', report)
        if report['status'] == 'validated_candidate_requires_frozen_fork':
            break
    if report['status'] == 'reproduction_pending':
        report['status'] = 'budget_exhausted_unsolved'
    _atomic_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--task', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--calls', type=int, default=2)
    parser.add_argument('--endpoint')
    args = parser.parse_args()
    run(args.project.resolve(), json.loads(args.task.read_text()), args.out.resolve(),
        calls=args.calls, endpoint=args.endpoint)


if __name__ == '__main__':
    main()
