import copy
import os
import shutil
import subprocess

import pytest

from solver import partial_reconstruction as p


ASM = '''
beq a0,zero,other
nop
addiu v0,zero,1
b done
nop
other:
addiu v0,zero,2
done:
jr ra
nop
'''
SOURCE = 'typedef int s32;\ns32 f(s32 x) { if (x) { return 1; } return 2; }\nint g(void) { return 9; }'


def initial():
    return p.create(SOURCE, 'f', ASM)


def split(manifest=None):
    manifest = manifest or initial()
    pending = [b['id'] for b in manifest['blocks'] if 5 in b['instructions']]
    implemented = [b['id'] for b in manifest['blocks'] if b['id'] not in pending]
    return p.apply(manifest, {'manifest_sha256': manifest['manifest_sha256'], 'hole_id': 0,
        'replacement': 'if (x) { return 1; } else { __gd_unfinished(1); }',
        'implemented_blocks': implemented, 'child_holes': [{'id': 1, 'blocks': pending}]})


def test_preserves_signature_declarations_other_functions_and_lexed_braces():
    source = '/* { fake } */\nint f(int x) { const char *s = "}"; return x; }\nint g(void) {return 8;}'
    result = p.create(source, 'f', ASM)
    assert result['source'] == p.DECLARATION + '/* { fake } */\nint f(int x) {\n__gd_unfinished(0);\nreturn 0;\n}\nint g(void) {return 8;}'
    assert result['original_source'] == source
    assert p.validate(result) is result


def test_one_branch_implemented_other_dummy_then_fill_keeps_first_branch():
    parent = initial()
    child = split(parent)
    assert child['original_source'] == parent['original_source']
    assert len(child['holes']) == 1
    assert child['parent_manifest_sha256'] == parent['manifest_sha256']
    final = p.apply(child, {'manifest_sha256': child['manifest_sha256'], 'hole_id': 1,
        'replacement': 'return 2;', 'implemented_blocks': child['holes'][0]['blocks']})
    assert 'if (x) { return 1; } else { return 2; }' in p.source_for_compile(final)
    assert not final['holes']
    assert final['completion_requires_full_gates'] is True


def test_wrong_predicate_bypassing_marker_is_unfinished_even_if_values_equal():
    child = split()
    raw = {'passed': True, 'return': 0}
    result = p.classify_row(child, raw, [0, 1, 5, 6, 7], [])
    assert result['status'] == 'unfinished'
    assert result['raw'] == raw
    assert not result['eligible_for_behavior_credit']


def test_marker_poisons_shared_tail_and_keeps_raw_failures():
    child = split()
    result = p.classify_row(child, {'passed': False, 'mismatch': 'tail write'}, [0, 1, 2, 3, 4, 6, 7], [1])
    assert result['status'] == 'unfinished'
    assert result['raw']['mismatch'] == 'tail write'
    assert p.classify_row(child, {'passed': True}, [0, 1, 2, 3, 4, 6, 7], [])['status'] == 'observed'


def test_loop_and_incomplete_trace_never_credit_pending_iterations():
    child = split()
    assert p.classify_row(child, {}, [0, 1, 2, 3, 4, 0, 1, 5], [])['status'] == 'unfinished'
    assert p.classify_row(child, {}, [], [], trace_complete=False)['status'] == 'unfinished'
    assert p.classify_row(child, {}, [10000], [])['status'] == 'unfinished'


@pytest.mark.skipif(os.name == 'nt', reason='compiler smoke runs in the Linux/WSL test environment')
def test_both_initial_skeleton_and_split_are_real_compilable_c(tmp_path):
    cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if not cc:
        pytest.skip('C compiler unavailable')
    for manifest in (initial(), split()):
        path = tmp_path / 'partial.c'
        path.write_text(p.source_for_compile(manifest), encoding='utf-8')
        completed = subprocess.run([cc, '-std=c89', '-fsyntax-only', str(path)], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize('replacement', [
    '#define __gd_unfinished(x)\n', 'asm("nop");', '__asm__("nop");',
    '} int forged(void) { return 3; } {', 'goto end;', '\\u005f_gd_unfinished(1);',
])
def test_rejects_escape_preprocessor_and_marker_hiding(replacement):
    manifest = initial()
    with pytest.raises(p.InvalidReconstruction):
        p.apply(manifest, {'manifest_sha256': manifest['manifest_sha256'], 'hole_id': 0,
            'replacement': replacement, 'implemented_blocks': manifest['holes'][0]['blocks']})


def test_rejects_missing_duplicated_forged_holes_and_dropped_blocks():
    manifest = initial()
    base = {'manifest_sha256': manifest['manifest_sha256'], 'hole_id': 0,
            'replacement': '__gd_unfinished(1);', 'child_holes': [{'id': 1, 'blocks': manifest['holes'][0]['blocks']}]}
    for changes in [{'replacement': ''}, {'replacement': '__gd_unfinished(1); __gd_unfinished(1);'},
                    {'replacement': '__gd_unfinished(2);'}, {'child_holes': []},
                    {'implemented_blocks': [manifest['holes'][0]['blocks'][0]]}]:
        with pytest.raises(p.InvalidReconstruction):
            p.apply(manifest, dict(base, **changes))


def test_manifest_tamper_and_stale_proposal():
    parent = initial()
    tampered = copy.deepcopy(parent)
    tampered['source'] = tampered['source'].replace('return 0;', 'return 1;')
    with pytest.raises(p.InvalidReconstruction, match='hash'):
        p.validate(tampered)
    child = split(parent)
    with pytest.raises(p.InvalidReconstruction, match='stale'):
        p.apply(child, {'manifest_sha256': parent['manifest_sha256']})
    tampered = copy.deepcopy(child)
    tampered['source'] = tampered['source'].replace('s32 f(', 's32 forged(')
    tampered['source_sha256'] = p._sha(tampered['source'])
    with pytest.raises(p.InvalidReconstruction):
        p.validate(p._seal(tampered))


@pytest.mark.parametrize('source', ['struct Thing f(void) { struct Thing t; return t; }',
    'Mystery f(void) { return x; }', 'int f(int x, ...) { return x; }',
    'int f(int x) {\n#if ENABLED\nreturn x;\n#endif\n}'])
def test_declines_unsupported_signatures_and_directives(source):
    with pytest.raises(p.InvalidReconstruction):
        p.create(source, 'f', ASM)


@pytest.mark.parametrize('source, expected', [('void f(void) {}', 'return;'),
    ('struct Thing *f(void) {return 0;}', 'return 0;'), ('float f(int x) {return x;}', 'return 0;')])
def test_supported_return_placeholders(source, expected):
    assert expected in p.create(source, 'f', ASM)['source']


@pytest.mark.parametrize('changes', [
    {'hole_id': False}, {'replacement': ['return;']}, {'replacement': ' ' * 12001},
    {'implemented_blocks': '0123'}, {'implemented_blocks': [True]},
    {'child_holes': '1'}, {'child_holes': [{'id': 1, 'blocks': [0]}] * 17},
    {'child_holes': [{'id': True, 'blocks': [0]}]},
    {'child_holes': [{'id': 1, 'blocks': '0'}]},
    {'child_holes': [{'id': 1, 'blocks': [False]}]},
    {'child_holes': [{'id': 1, 'blocks': [], 'extra': 1}]},
    {'hypothesis': []}, {'hypothesis': 'x' * 601}, {'extra': 'ignored?'},
])
def test_strict_bounded_proposals(changes):
    parent = initial()
    proposal = {'manifest_sha256': parent['manifest_sha256'], 'hole_id': 0,
                'replacement': 'return 2;', 'implemented_blocks': parent['holes'][0]['blocks']}
    proposal.update(changes)
    with pytest.raises(p.InvalidReconstruction):
        p.apply(parent, proposal)
