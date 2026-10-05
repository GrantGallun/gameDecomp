"""Campaign localization must use the current candidate, and reach repair prompts."""
import hashlib
from pathlib import Path

from solver import modelrepair, workspace


SOURCE = 'int f(int x) {\n    int y;\n    y = x + 1;\n    return y;\n}\n'
LISTING = ['addiu v0,a0,1', 'jr ra']
TARGET = ['addiu v0,a0,2', 'jr ra']
DIFF = '- addiu v0,a0,2\n+ addiu v0,a0,1'


def attribution(source=SOURCE, diff=DIFF):
    return {'status': 'verified', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'diff_sha256': hashlib.sha256(diff.encode()).hexdigest(),
            'instructions': [{'normalized_line': 1, 'candidate_line': 3},
                             {'normalized_line': 2, 'candidate_line': 4}]}


def compile_candidate(source, label):
    if '__gamedecomp_attr_probe =' in source:
        return ['sw v0,%lo(__gamedecomp_attr_probe)(v1)', *LISTING]
    if 'y = x + 1;' not in source:
        return ['jr ra']
    if 'short y;' in source:
        return ['sll v0,a0,16', 'jr ra']
    return LISTING


def test_current_original_lines_are_confirmed_by_intervention_and_shown_to_model():
    from solver import compiler_localization as loc
    packet = loc.analyze(SOURCE, 'f', TARGET, LISTING, DIFF, attribution(), compile_candidate)
    assert packet['direct'] == [3]
    assert packet['regions'] == [(3, 3)]
    attempt = workspace.Attempt(True, 90, False, DIFF, '', '')
    prompt = modelrepair.build_prompt('', SOURCE, attempt, function='f', localization=packet)
    assert 'LOCALIZATION' in prompt and 'line(s) 3' in prompt
    assert SOURCE in prompt


def test_stale_source_or_diff_declines_without_interventions():
    from solver import compiler_localization as loc
    def forbidden(*args):
        raise AssertionError('stale evidence must not trigger compiler probes')
    for evidence in (attribution(SOURCE + ' '), attribution(diff='different')):
        packet = loc.analyze(SOURCE, 'f', TARGET, LISTING, DIFF, evidence, forbidden)
        assert packet['status'] == 'unavailable'
        assert not packet['direct']


def test_missing_rows_show_all_tied_insertion_sites_and_rejected_probes():
    from solver import compiler_localization as loc
    target = ['sw a0,0(a1)', *LISTING]
    def compile_probe(source, label):
        if 'probe-insert' in label and '    int y;' in source.split('__gamedecomp_attr_probe =')[0]:
            return compile_candidate(source, label)
        if 'probe-insert' in label:
            return None
        return compile_candidate(source, label)
    packet = loc.analyze(SOURCE, 'f', target, LISTING, DIFF, attribution(), compile_probe)
    assert len(packet['missing']) > 1
    assert 'tied' in loc.render(packet, SOURCE, DIFF)
    assert packet['skipped']['probe-rejected-by-compiler'] >= 1


def test_nonlocal_residual_is_not_presented_as_a_single_line_fix():
    from solver import compiler_localization as loc
    packet = loc.analyze(SOURCE, 'f', ['addiu v1,a0,1', 'jr ra'], LISTING,
                         DIFF, attribution(), compile_candidate)
    assert packet['nonlocal'] == 'operand-names'
    assert 'not one source line' in loc.render(packet, SOURCE, DIFF)


def test_rejected_offsets_on_same_line_are_not_reported_as_measured_insertion_sites():
    from solver import compiler_localization as loc
    source = 'void f(int x){ x++; x--; }'
    evidence = attribution(source)
    evidence['instructions'] = [{'normalized_line': 1, 'candidate_line': 1},
                                {'normalized_line': 2, 'candidate_line': 1}]
    def compile_probe(code, label):
        if label.startswith('probe-insert:'):
            return compile_candidate(code, label) if label == 'probe-insert:14' else None
        return LISTING
    packet = loc.analyze(source, 'f', ['sw a0,0(a1)', *LISTING], LISTING,
                         DIFF, evidence, compile_probe)
    assert [s['offset'] for s in packet['insertion_sites']] == [14]


def test_same_length_stale_normalized_dump_is_refused_before_probing(tmp_path, monkeypatch):
    from solver import compiler_localization as loc
    obj = tmp_path / 'parent.o'
    obj.write_bytes(b'current compiled object')
    (tmp_path / 'parent_object_dump_normalized.s').write_text('unrelated opcode\njr ra\n')
    (tmp_path / 'target_object_dump_normalized.s').write_text('\n'.join(TARGET))
    dump = tmp_path / 'parent.source-lines.dump'
    dump.write_text('captured current disassembly')
    normalizer = tmp_path / 'objdump.py'
    normalizer.write_text("def process_objdump_lines(raw):\n    return ['addiu v0,a0,1', 'jr ra']\n")
    evidence = attribution()
    evidence.update(candidate_object_sha256=hashlib.sha256(obj.read_bytes()).hexdigest(),
                    line_dump_sha256=hashlib.sha256(dump.read_bytes()).hexdigest(),
                    normalizer_sha256=hashlib.sha256(normalizer.read_bytes()).hexdigest())
    parent = modelrepair.CandidateState(SOURCE, workspace.Attempt(True, 90, False, DIFF, '', '',
        receipt_id=1, source_attribution=evidence), obj)
    monkeypatch.setattr(workspace, 'score', lambda *args, **kwargs:
        workspace.Attempt(False, 0, False, '', 'compiler rejection', '', receipt_id=2))
    packet, children = loc.measure(tmp_path, tmp_path, 'f', parent, conn=object(), run_id='r', run_config={})
    assert packet['status'] == 'unavailable'
    assert 'normalized' in packet['reason']
    assert children == []


def test_localized_profile_remains_available_after_ordinary_repairs_are_spent():
    from eval import completion_campaign as campaign
    from solver import repair_queue as queue
    node = {'status': 'pending', 'source': 'f.c', 'source_sha256': 'source',
            'residual': {'compiled': True, 'frontend': {'passed': True}}, 'jobs': [],
            'semantic_validation': {'status': 'observed_equivalent',
                                    'counts': {'failed': 0, 'passed': 1}}}
    key = queue.evidence_key(node)
    node['jobs'] = [{'profile': p['name'], 'evidence_key': key, 'source_sha256': 'source'}
                    for p in campaign.PROFILES if not p.get('compiler_localization')]
    # Ordinary model visits survive; the new option is additional.
    profiles = []
    for _ in range(20):
        profile = queue.next_profile(node, 2, campaign.PROFILES)
        if profile is None:
            break
        profiles.append(profile)
        node['jobs'].append({'profile': profile['name'], 'evidence_key': key, 'source_sha256': 'source'})
    assert any(p.get('compiler_localization') is True for p in profiles)

