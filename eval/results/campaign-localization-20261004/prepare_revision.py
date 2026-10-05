"""Reuse the drained, rollback-capable amendment protocol with an explicit six-file roster."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REVISIONS = ROOT / 'eval/results/resume-pipeline-20260908/revisions'
HERE = REVISIONS / '20261004-compiler-localization'
TEMPLATE = REVISIONS / '20261002-integration-headers'
hashes = json.loads((HERE / 'payload-hashes.json').read_text())


def assignment(text, name, value):
    lines = text.splitlines(keepends=True)
    for node in ast.parse(text).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ''.join(lines[:node.lineno - 1]) + name + ' = ' + repr(value) + '\n' + ''.join(lines[node.end_lineno:])
    raise RuntimeError('missing assignment: ' + name)


stage = (TEMPLATE / 'stage.py').read_text()
stage = stage.replace("KIND = 'integration-headers'", "KIND = 'compiler-localization'")
values = {'CHANGED': tuple(hashes), 'NEW': {rel for rel, pair in hashes.items() if pair[0] is None},
          'REVIEWED': {rel: tuple(pair) for rel, pair in hashes.items()},
          'TESTS': ('tests/test_compiler_localization.py', 'tests/test_line_map.py'),
          'TEST_FIXTURES': ('tests/test_completion_campaign.py',),
          'FROZEN_TESTS': ('tests/test_modelrepair.py', 'tests/test_repair_queue.py',
                           'tests/test_completion_campaign.py', 'tests/test_campaign_fast.py'),
          'NEW_DRIFT_IMPORTS': {('solver/compiler_localization.py', 'solver/source_attribution.py'):
                               'Frozen instructions_of/sha/parse_dump API verified by staged tests and compiler fire.',
                               ('solver/compiler_localization.py', 'solver/regalloc_mutations.py'):
                               'Use only frozen lexer/type proposals; measured by actual candidate compiler.',
                               ('solver/compiler_localization.py', 'solver/workspace.py'):
                               'Frozen score/repair_complete oracle retained; staged compiler proof logs private probes.',
                               ('solver/compiler_localization.py', 'solver/code_shapes.py'):
                               'Existing masked balanced body API exercised on raw production source.',
                               ('solver/compiler_localization.py', 'solver/edit_locality.py'):
                               'Only existing edited_lines spans are consumed, verified in staged tests.',
                               ('solver/line_map.py', 'tools/context_closure.py'):
                               'Canonical teaching helpers are not called by the campaign adapter; import is deferred.'}}
for name, value in values.items():
    stage = assignment(stage, name, value)
stage = stage.replace('shutil.copy2(MAIN / rel, target)',
    "shutil.copy2((HERE / 'reviewed-tests' / rel) if (HERE / 'reviewed-tests' / rel).is_file() else MAIN / rel, target)")
(HERE / 'stage.py').write_text(stage)
verify = (TEMPLATE / 'verify_stage.py').read_text().replace('integration-headers-stage-20261002', 'compiler-localization-stage-20261004')
verify = assignment(verify, 'FIXTURES', ('tools/context_closure.py',))
(HERE / 'verify_stage.py').write_text(verify)
apply = (TEMPLATE / 'apply_amendment.py').read_text().replace('20261002-integration-headers', '20261004-compiler-localization')
apply = apply.replace('unapplied-integration-headers-stage', 'unapplied-compiler-localization-stage')
a, b = apply.index('def fires_ok()'), apply.index('\n\ndef lock(')
apply = apply[:a] + '''def fires_ok() -> bool:
    proof_path = HERE.parents[2] / 'campaign-localization-20261004/fire-staged.json'
    try:
        proof = json.loads(proof_path.read_bytes())
        manifest = json.loads((HERE / 'stage.json').read_bytes())
        return (proof.get('manifest_sha256') == sha(HERE / 'stage.json')
            and proof['root_compiled'] and proof['attribution_status'] == 'verified'
            and proof['packet']['status'] == 'measured' and proof['probe_count'] > 0
            and proof['prompt_has_localization'] and proof['all_probes_parented']
            and proof.get('search_prompt_has_localization') is True
            and proof['payload_hashes'] == {rel: row['new_sha256'] for rel, row in manifest['changed'].items()})
    except (OSError, ValueError, KeyError):
        return False
''' + apply[b:]
apply = apply.replace("'eval/results/integration-headers-20261002/protocol.json',\n                             'eval/results/integration-headers-20261002/proof.json'",
                      "'eval/results/campaign-localization-20261004/fire-staged.json'")
old = "'limits': ('Machinery only:"
a = apply.index(old)
b = apply.index("')}", a) + 3
apply = apply[:a] + "'limits': ('Candidate-only compiler attribution; additional byte-lane profile; ordinary repairs and object certificate authority retained. Original source coordinates and measured insertion ties are preserved. All probes are logged; diagnostic stores cannot become repair children. This installation imports no candidate, attempt or node and does not establish a new match.')}" + apply[b:]
apply = apply.replace('fresh header-conflict ROM-union proof missing or failing', 'fresh staged localization integration proof missing or failing')
(HERE / 'apply_amendment.py').write_text(apply)
# Change only the frozen test's legacy profile expectation, rather than copy unrelated main tests.
test = (HERE.parents[1] / 'code/tests/test_completion_campaign.py').read_text()
anchor = "if not p.get('type_transaction') and p['name'] != 'regalloc_search'"
if test.count(anchor) != 1:
    raise RuntimeError('legacy test expectation differs')
test = test.replace(anchor, "if not p.get('type_transaction') and not p.get('compiler_localization') and p['name'] != 'regalloc_search'")
out = HERE / 'reviewed-tests/tests/test_completion_campaign.py'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(test)
for file in ('stage.py', 'verify_stage.py', 'apply_amendment.py'):
    ast.parse((HERE / file).read_text())
print('Prepared scoped stage/test/apply scripts.')
