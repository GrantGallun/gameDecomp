import hashlib
import importlib
import importlib.util
import json

from solver import modelrepair, workspace


SOURCE = '''void f(Player *p) {
    s32 temp;
    temp = p->velocity;
    consume(temp);
    temp = p->timer;
    if (temp < 45) {
        p->timer = temp + 1;
    }
}
'''
DIFF = '@@ -1,2 +1,2 @@\n-lh v0,772(s0)\n+lh v1,772(s0)\n-move a0,v0\n+move a0,v1\n'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def attribution(source=SOURCE):
    return {'status': 'verified', 'source_sha256': sha(source), 'diff_sha256': sha(DIFF),
            'instructions': [{'normalized_line': 1, 'candidate_line': 5, 'section': '.text',
                              'address': 0, 'bytes': '86020304'}]}


def packet(source=SOURCE, **kwargs):
    assert importlib.util.find_spec('solver.repair_diagnostic'), 'focused diagnostic packet is missing'
    return importlib.import_module('solver.repair_diagnostic').build(source, 'f', DIFF, **kwargs)


def test_packet_links_diff_to_local_and_includes_earlier_role_outside_diff():
    report = packet(attribution=attribution())
    assert report['source_sha256'] == sha(SOURCE)
    assert report['direct_attribution_status'] == 'verified'
    local = next(row for row in report['locals'] if row['name'] == 'temp')
    assert local['type'] == 's32'
    assert {3, 4, 5, 6, 7} <= {row['line'] for row in local['occurrences']}
    assert sum(r['role'] == 'assignment' for r in local['occurrences']) == 2
    assert any(r['kind'] == 'call' and r['line'] == 4 for r in local['barriers'])
    assert not report['instruction_ownership_proven']


def test_stale_attribution_is_not_reported_as_direct_evidence():
    report = packet('\n' + SOURCE, attribution=attribution())
    assert report['direct_attribution_status'] == 'unavailable-or-stale'
    assert all(site['evidence'] != 'direct-compiler-line' for site in report['sites'])


def test_packet_bounds_payload_and_marks_incomplete_use_inventory():
    source = SOURCE.replace('consume(temp);', 'consume(temp);\n' * 100)
    report = packet(source, max_occurrences=6)
    local = next(row for row in report['locals'] if row['name'] == 'temp')
    assert local['omitted_occurrences'] > 0
    assert len(local['occurrences']) <= 6
    assert len(json.dumps(report)) < 18000


def test_packet_ignores_members_comments_and_other_functions_as_local_uses():
    source = SOURCE.replace('consume(temp);', 'consume(temp); /* temp */\n    p->temp = 1;')
    source += '\nvoid g(void) { int temp; temp = 4; }'
    local = next(row for row in packet(source)['locals'] if row['name'] == 'temp')
    assert len(local['occurrences']) == 5


def test_truncation_keeps_attributed_later_role_and_some_earlier_uses():
    source = SOURCE.replace('consume(temp);', 'consume(temp);\n' * 30)
    direct = attribution(source)
    direct['instructions'][0]['candidate_line'] = source[:source.index('temp = p->timer')].count('\n') + 1
    local = next(row for row in packet(source, attribution=direct, max_occurrences=6)['locals'] if row['name'] == 'temp')
    assert any('velocity' in row['excerpt'] for row in local['occurrences'])
    assert any('temp < 45' in row['excerpt'] for row in local['occurrences'])
    assert any('p->timer = temp + 1' in row['excerpt'] for row in local['occurrences'])


def test_only_matching_parent_and_diff_history_is_presented_as_measured():
    history = [{'parent_source_sha256': sha(SOURCE), 'parent_diff_sha256': sha(DIFF),
                'label': 'trial', 'compiled': True, 'exact': False, 'gradient': [0, 2, 2]},
               {'parent_source_sha256': 'stale', 'parent_diff_sha256': sha(DIFF),
                'label': 'unrelated', 'exact': True}]
    report = packet(history=history)
    assert [row['label'] for row in report['recent_observations']] == ['trial']


def test_existing_model_prompt_contains_packet_and_can_ablate_it():
    attempt = workspace.Attempt(True, 92.0, False, DIFF, '', '', source_attribution=attribution())
    prompt = modelrepair.build_prompt('f:\n jr ra\n nop', SOURCE, attempt)
    assert 'ASSIGNMENT-SCOPED DIAGNOSTIC PACKET' in prompt
    assert 'p->velocity' in prompt and 'preserve conversions' in prompt
    assert 'predicted assembly effect' in prompt
    old = modelrepair.build_prompt('f:\n jr ra\n nop', SOURCE, attempt, focused_diagnostics=False)
    assert 'ASSIGNMENT-SCOPED DIAGNOSTIC PACKET' not in old


def test_model_search_delivers_previous_measured_sibling_to_next_prompt(monkeypatch, tmp_path):
    prompts = []
    class Provider:
        provider_id = 'test'
        def generate(self, request):
            prompts.append(request.prompt)
            value = 45 + len(prompts)
            return json.dumps({'kind': 'expression', 'hypothesis': f'trial {value}',
                'edits': [{'old': 'temp < 45', 'new': f'temp < {value}'}]}), {'done_reason': 'stop'}
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    monkeypatch.setattr(workspace, 'score', lambda *a, **k: workspace.Attempt(True, 90, False, DIFF, '', ''))
    modelrepair.search(tmp_path, 'f', SOURCE, tmp_path, model='test', endpoint='unused',
        base_attempt=workspace.Attempt(True, 92, False, DIFF, '', ''), draws=2, max_depth=1,
        max_calls=2, provider=Provider())
    assert len(prompts) == 2
    assert '"recent_observations":[]' in prompts[0]
    assert '"weighted_score_before":92' in prompts[1]
    assert '"weighted_score_after":90' in prompts[1]
    assert 'trial 46' in prompts[1]
