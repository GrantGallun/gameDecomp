"""Apply only the reviewed fresh-name hunks to a separate staged frozen tree."""
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[3]


def replace(text, before, after, count=1):
    if text.count(before) != count:
        raise ValueError(f'Frozen patch precondition: expected {count} occurrences of {before!r}')
    return text.replace(before, after)


def apply_frozen(stage_dir):
    stage = Path(stage_dir).resolve()
    live = (PROJECT/'eval/results/resume-pipeline-20260908/code').resolve()
    if stage == live or not stage.is_relative_to(PROJECT/'eval/results'):
        raise ValueError('Only a separate staged tree inside eval/results may be amended')
    updates = {}
    path = stage/'solver/workspace.py'
    text = path.read_text()
    anchor = 'EXACT_RE = re.compile(r"^Verified exact match:\\s*(\\w+)", re.MULTILINE)'
    updates[path] = replace(text, anchor, anchor+'\n# Set only by the controller-verified fast runtime, restored when it uninstalls.\n# An artifact-name ledger may use this capability; it never replaces score gates.\n_verified_build_cache_pin = None')
    path = stage/'solver/repair.py'
    text = path.read_text()
    text = replace(text, 'run_id: str = "repair"):', 'run_id: str = "repair", baseline_name: str | None = None):')
    text = replace(text, '    base = workspace.score(ws, repo, name, src)',
        '    # Retain the fresh root artifact address; score reruns every acceptance gate.\n'
        '    base = workspace.score(ws, repo, baseline_name or name, src)')
    updates[path] = text
    path = stage/'eval/agentrepair.py'
    text = path.read_text()
    text = replace(text, '    root_tag = f"{function}_agentrepair_root_{time.time_ns()}"\n    root = workspace.score(\n        ws, repo, root_tag, source, conn=conn, func=function, iteration=0,',
        '    from solver.fresh_compile import Names\n    compiled_names = Names(repo, ws)\n'
        '    root_tag, root = compiled_names.score(\n        f"{function}_agentrepair_root_{time.time_ns()}", source, conn=conn, func=function, iteration=0,')
    text = replace(text, 'att = workspace.score(ws,repo,tag,adapted,conn=conn,func=function,', 'tag, att = compiled_names.score(tag,adapted,conn=conn,func=function,')
    text = replace(text, 'att=workspace.score(ws,repo,tag,candidate,conn=conn,func=function,', 'tag, att=compiled_names.score(tag,candidate,conn=conn,func=function,', 2)
    text = replace(text, 'run_id=run_id, verbose=verbose)', 'run_id=run_id, verbose=verbose, baseline_name=compiled_names.prior(source))')
    text = replace(text, 'verified = workspace.score(ws, repo, tag, candidate, conn=conn, func=function,', 'tag, verified = compiled_names.score(tag, candidate, conn=conn, func=function,')
    text = replace(text, 'att = workspace.score(ws, repo, tag, retained_source, conn=conn, func=function,', 'tag, att = compiled_names.score(tag, retained_source, conn=conn, func=function,')
    updates[path] = text
    # Validate all exact preconditions before any staged writes.
    for path, text in updates.items():
        compile(text, str(path), 'exec')
    for path, text in updates.items():
        path.write_text(text)
    for relative in ('solver/fresh_compile.py', 'tests/test_fresh_compile.py'):
        destination = stage/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT/relative, destination)
    return [str(p.relative_to(stage)) for p in updates] + ['solver/fresh_compile.py', 'tests/test_fresh_compile.py']


if __name__ == '__main__':
    import sys
    print(apply_frozen(sys.argv[1]))
