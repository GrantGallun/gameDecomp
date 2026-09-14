import copy
import hashlib

from solver import stack_object_repair as repair


SOURCE = '''void f(void) {
    s16 sp20;
    s32 sp34;
    init(&sp20, 1);
    sp34 = 7;
    (*(s32 *)((u8 *)(&sp20) + 0x14)) = 7;
    consume((Object *) &sp20);
}
'''
ASM = '''f:
move t0, a0
addiu sp, sp, -0x40
addiu a0, sp, 0x20
jal init
nop
sw v0, 0x34(sp)
addiu a0, sp, 0x20
jal consume
nop
addiu sp, sp, 0x40
jr ra
nop
'''


def measurement(source=SOURCE):
    return {'kind': 'target-compiler-header-layouts',
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'layouts': {'Object': [
            {'member': 'rotation', 'offset': 0, 'width': 18, 'owner_size': 24,
             'array': True, 'canonical': 's16[9]'},
            {'member': 'value', 'offset': 20, 'width': 4, 'owner_size': 24, 'spelling': 's32'}]}}


def test_candidate_requires_three_evidence_sources():
    r = repair.propose(SOURCE, 'f', ASM, measurement())
    assert r['changes'], r
    assert 'Object sp20;' in r['source']
    assert 'init(sp20.rotation, 1)' in r['source']
    assert 'sp20.value = 7;' in r['source']
    assert 's32 sp34;' not in r['source']
    assert r['changes'][0]['target_object_base'] == -32
    assert r['assembly_sha256'] == hashlib.sha256(ASM.encode()).hexdigest()


def test_declines_missing_or_ambiguous_binary():
    for asm in [ASM.replace('sw v0, 0x34(sp)', 'nop'),
                ASM.replace('addiu a0, sp, 0x20', 'addiu a0, sp, 0x24'),
                ASM.replace('jal consume', 'jal other'),
                ASM.replace('sw v0, 0x34(sp)', 'addiu sp, sp, -0x10')]:
        r = repair.propose(SOURCE, 'f', asm, measurement())
        assert not r['changes'] and r['declines']
        assert r['source'] == SOURCE


def test_declines_stale_and_ambiguous_layout():
    packets = [{}, measurement(SOURCE+' ')]
    overlap = measurement()
    overlap['layouts']['Object'].append(copy.deepcopy(overlap['layouts']['Object'][1]))
    packets.append(overlap)
    small = measurement()
    for field in small['layouts']['Object']:
        field['owner_size'] = 20
    packets.append(small)
    converted = measurement()
    converted['layouts']['Object'][1]['spelling'] = 'f32'
    packets.append(converted)
    for packet in packets:
        r = repair.propose(SOURCE, 'f', ASM, packet)
        assert not r['changes'] and r['declines']


def test_declines_unclosed_local_uses():
    for source in [SOURCE.replace('sp34 = 7;', 'escape(&sp34);'),
                   SOURCE.replace('sp34 = 7;', 'sp34 += 7;'),
                   SOURCE.replace('sp34 = 7;', 'sp34 = 7; read(sp34);'),
                   SOURCE.replace('s32 sp34;', 'volatile s32 sp34;'),
                   SOURCE.replace('init(&sp20, 1);', 'sp20 = 7;')]:
        r = repair.propose(source, 'f', ASM, measurement(source))
        assert not r['changes'] and r['declines'], r


def test_does_not_edit_other_function_or_comments():
    source = SOURCE+'void other(void) { s32 sp34; sp34 = 7; }\n/* sp34 */\n'
    r = repair.propose(source, 'f', ASM, measurement(source))
    assert r['changes']
    assert r['source'].endswith('void other(void) { s32 sp34; sp34 = 7; }\n/* sp34 */\n')


def test_unknown_overlapping_local_cannot_hide_beside_valid_one():
    source = SOURCE.replace('s32 sp34;', 's32 sp34;\n    volatile s16 sp32;')
    r = repair.propose(source, 'f', ASM, measurement(source))
    assert not r['changes'] and r['declines']


def test_normalization_wiring_and_lineage(monkeypatch, tmp_path):
    from solver import modelrepair, type_constraints, workspace, stack_buffers
    import json
    (tmp_path/'.compiler-target.json').write_text(json.dumps({'target': 'build/f.o'}))
    monkeypatch.setattr(type_constraints, 'measure', lambda *a: measurement())
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: ASM)
    monkeypatch.setattr(stack_buffers, 'candidates', lambda *a: ([], {}))
    monkeypatch.setattr(stack_buffers, 'byte_subfields', lambda *a: ([], {}))
    root = workspace.Attempt(True, 60, False, '', '', '', 7, frontend={'passed': True})
    child = workspace.Attempt(True, 89, False, '', '', '', 8, frontend={'passed': True})
    seen = []
    def score(*args, **kwargs):
        seen.append((args[3], kwargs))
        return child
    monkeypatch.setattr(workspace, 'score', score)
    r = modelrepair.search(tmp_path, 'f', SOURCE, tmp_path, model='test', endpoint='none',
        base_attempt=root, resilient=True, max_calls=0)
    assert r.best_attempt is child and r.calls_attempted == 0
    assert len(seen) == 1
    assert seen[0][1]['parent_attempt_id'] == 7
    assert seen[0][1]['action'] == 'measured-stack-object'
    assert seen[0][1]['extra']['stack_object_hypotheses']['changes']
