from pathlib import Path

from eval import intake_probe, intake_runners


SOURCE = '''void *Fdistort(void *arg0, unsigned char *arg1) {
    arg0->unk2C = 0.25f;
    return arg1 + 1;
}
'''
ASM = 'glabel Fdistort\nswc1 $f4, 0x2c($a0)\njr $ra\naddiu $v0, $a1, 1\n'


def test_void_action_fires_on_the_motivating_float_store(tmp_path, monkeypatch):
    from solver import frontend_diagnostics
    target = tmp_path / 'target.s'
    target.write_text(ASM)
    calls = []

    def analyse(source, **kwargs):
        calls.append(kwargs)
        line = source.splitlines()[1]
        return {'status': 'rejected', 'diagnostics':
                f"candidate.c:2:{line.index('->')+1}: error: member reference base type 'void' is not a structure or union\n 2 | {line}\n"}

    monkeypatch.setattr(frontend_diagnostics, 'analyse', analyse)
    label = 'eval.intake_runners.void_members'
    assert label in intake_runners.RUNNERS
    result = intake_runners.RUNNERS[label](dict(candidate=SOURCE, function='Fdistort',
        repo=str(tmp_path), target='build/src/f.o', target_asm_path=str(target)), {})
    assert result['changed']
    assert '(*(f32 *)((unsigned char *)arg0 + 0x2C)) = 0.25f' in result['source']
    assert calls[0]['full_diagnostics'] is True
    assert result['detail']['plans'][0]['witnesses'][0]['opcode'] == 'swc1'


def test_void_action_names_missing_binary_evidence(tmp_path):
    label = 'eval.intake_runners.void_members'
    assert label in intake_runners.RUNNERS
    result = intake_runners.RUNNERS[label](dict(candidate=SOURCE, function='Fdistort',
        repo=str(tmp_path), target='build/src/f.o'), {})
    assert not result['changed']
    assert 'target_asm_path' in result['reason']


def test_intake_runs_void_views_then_frontend_cast_repair():
    sequence = intake_probe.SEQUENCE
    assert 'eval.intake_runners.void_members' in sequence
    assert 'eval.intake_runners.frontend_casts' in sequence
    assert sequence.index('eval.intake_runners.void_members') > sequence.index('eval.intake_runners.widen_pointer_declarations')
    assert sequence.index('eval.intake_runners.frontend_casts') > sequence.index('eval.intake_runners.void_members')


def test_frontend_cast_action_delegates_to_fixer_and_reports_missing_workspace(tmp_path, monkeypatch):
    from solver import frontend_diagnostics, frontend_fixits
    label = 'eval.intake_runners.frontend_casts'
    assert label in intake_runners.RUNNERS
    context = dict(candidate=SOURCE, function='Fdistort', repo=str(tmp_path), target='build/src/f.o')
    missing = intake_runners.RUNNERS[label](context, {})
    assert not missing['changed'] and 'workspace' in missing['reason']
    monkeypatch.setattr(frontend_diagnostics, 'recipe', lambda *args: {'command': ['clang']})
    calls = []
    def propose(repo, source, command, workdir, rounds=4):
        calls.append((repo, source, command, workdir, rounds))
        return source.replace('0.25f', '((float) (0.25f))'), [{'round': 0, 'fixes': ['conversion']}]
    monkeypatch.setattr(frontend_fixits, 'propose', propose)
    result = intake_runners.RUNNERS[label]({**context, 'workspace': str(tmp_path)}, {})
    assert result['changed']
    assert calls == [(tmp_path, SOURCE, ['clang'], tmp_path, 4)]


def test_frontend_cast_action_repairs_pointer_store_with_real_proposer(tmp_path, monkeypatch):
    from solver import frontend_diagnostics, frontend_fixits
    source = 'void addRenderCallback(int *slot, void *value) {\n    *slot = value;\n}\n'
    fixed = source.replace('*slot = value;', '*slot = ((int) (value));')
    diagnostic = ("candidate.c:2:13:{2:5-2:10}{2:13-2:18}: error: incompatible pointer to "
                  "integer conversion assigning to 'int' from 'void *' [-Werror,-Wint-conversion]\n")
    monkeypatch.setattr(frontend_diagnostics, 'recipe', lambda *args: {'command': ['clang']})
    checked = []

    def run_frontend(repo, candidate, command, workdir):
        checked.append(candidate)
        assert candidate in (source, fixed)
        return (0, '') if candidate == fixed else (1, diagnostic)

    monkeypatch.setattr(frontend_fixits, 'run_frontend', run_frontend)
    result = intake_runners.frontend_casts(dict(candidate=source, repo=str(tmp_path),
        target='build/src/f.o', workspace=str(tmp_path)), {})
    assert result['changed'] and result['source'] == fixed
    assert checked == [source, fixed]
    assert result['exact'] is False
    assert result['detail']['plans'][0]['fixes']


def test_ido_cursor_action_fires_after_frontend_passes(tmp_path, monkeypatch):
    from solver import frontend_diagnostics
    monkeypatch.setattr(frontend_diagnostics, 'analyse', lambda *args, **kwargs: {'status': 'passed'})
    source = 'void *Fvibdown(void *arg0, void *arg1) {\n    return arg1 + 3;\n}\n'
    label = 'eval.intake_runners.ido_byte_cursors'
    assert label in intake_runners.RUNNERS
    assert intake_probe.SEQUENCE.index(label) > intake_probe.SEQUENCE.index('eval.intake_runners.frontend_casts')
    result = intake_runners.RUNNERS[label](dict(candidate=source, function='Fvibdown',
        repo=str(tmp_path), target='build/src/f.o', initial_verdict={'compiled': False}), {})
    assert result['changed'] and 'return ((u8 *)arg1) + 3;' in result['source']
    assert result['detail']['plans']['arithmetic'] == ['arg1']
    assert result['exact'] is False


def test_ido_cursor_action_preserves_compiled_and_frontend_rejected_sources(tmp_path, monkeypatch):
    from solver import frontend_diagnostics
    label = 'eval.intake_runners.ido_byte_cursors'
    assert label in intake_runners.RUNNERS
    calls = []
    def analyse(*args, **kwargs):
        calls.append(args)
        return {'status': 'rejected'}
    monkeypatch.setattr(frontend_diagnostics, 'analyse', analyse)
    context = dict(candidate=SOURCE, function='Fdistort', repo=str(tmp_path), target='build/src/f.o')
    assert not intake_runners.RUNNERS[label]({**context, 'initial_verdict': {'compiled': True}}, {})['changed']
    assert calls == []
    assert not intake_runners.RUNNERS[label]({**context, 'initial_verdict': {'compiled': False}}, {})['changed']
    assert len(calls) == 1


def test_ido_cursor_action_never_lowers_pseudo_fields(tmp_path, monkeypatch):
    from solver import frontend_diagnostics
    # Even an inconsistent frontend result cannot admit the older helper's inferred field widths.
    monkeypatch.setattr(frontend_diagnostics, 'analyse', lambda *args, **kwargs: {'status': 'passed'})
    label = 'eval.intake_runners.ido_byte_cursors'
    assert label in intake_runners.RUNNERS
    result = intake_runners.RUNNERS[label](dict(candidate=SOURCE, function='Fdistort',
        repo=str(tmp_path), target='build/src/f.o', initial_verdict={'compiled': False}), {})
    assert not result['changed']
    assert 'field' in result['reason']


def test_sequential_intake_passes_the_adopted_compile_verdict(tmp_path, monkeypatch):
    from eval import tool_agent_run
    from eval.tool_agent import Context
    from solver import frontend_diagnostics
    label = 'eval.intake_runners.ido_byte_cursors'
    source = 'void *f(void *p) {\n    return p + 1;\n}\n'
    context = Context(function='f', candidate=source, repo=str(tmp_path), target='build/src/f.o',
        initial_verdict={'compiled': False, 'exact': False, 'score': 0, 'stderr': 'bad operand'},
        compile_fn=lambda child: {'compiled': True, 'exact': False, 'score': 20, 'stderr': ''})
    monkeypatch.setattr(tool_agent_run, 'build_context', lambda *args, **kwargs: (context, None))
    monkeypatch.setattr(intake_probe, 'candidates', lambda *args, **kwargs: ['f'])
    monkeypatch.setattr(intake_probe, '_placeholder_widths', lambda *args: ({}, {}))
    monkeypatch.setattr(intake_probe, '_diagnostic_chain', lambda *args: {'clang': 'passed', 'errors': 0, 'classes': []})
    monkeypatch.setattr(frontend_diagnostics, 'analyse', lambda *args, **kwargs: {'status': 'passed'})
    monkeypatch.setattr(intake_probe, 'SEQUENCE', ('repair', label))
    monkeypatch.setitem(intake_runners.RUNNERS, 'repair', lambda ctx, params:
        {'changed': True, 'source': ctx['candidate'] + '/* repaired */\n'})
    observed = []
    def observe(ctx, params):
        result = intake_runners.ido_byte_cursors(ctx, params)
        observed.append((ctx['candidate'], ctx['initial_verdict']['compiled'], result['changed']))
        return result
    monkeypatch.setitem(intake_runners.RUNNERS, label, observe)
    code = intake_probe.main(['--kb', str(tmp_path / 'kb.sqlite'), '--repo', str(tmp_path),
        '--split', str(tmp_path / 'no-split.json'), '--out', str(tmp_path / 'receipt.json'), '--want', '1'])
    assert code == 0
    assert observed[0] == (source, False, True)  # independent baseline arm
    assert observed[1] == (source + '/* repaired */\n', True, False)  # sequential arm
