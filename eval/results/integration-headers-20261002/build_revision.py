"""Derive scoped deployment scripts from the previously verified four-file amendment."""
from pathlib import Path
import ast
import hashlib
import json
import re
import shutil

HERE = Path(__file__).resolve().parent
RUN = HERE.parent / 'resume-pipeline-20260908'
PREVIOUS = RUN / 'revisions/20261001-preparer-names'
NEW = RUN / 'revisions/20261002-integration-headers'


def main():
    proof = json.loads((HERE / 'proof.json').read_text())
    assert proof['whole_rom_verified'] and proof['combined_union_count'] == proof['original_union_count'] + 1
    rel = 'eval/prepare_integration.py'
    reviewed = HERE / 'reviewed' / rel
    assert hashlib.sha256(reviewed.read_bytes()).hexdigest() == proof['new_sha256']
    assert hashlib.sha256((RUN / 'code' / rel).read_bytes()).hexdigest() == proof['old_sha256']
    NEW.mkdir(parents=True, exist_ok=True)
    (NEW / 'reviewed/eval').mkdir(parents=True, exist_ok=True)
    shutil.copy2(reviewed, NEW / 'reviewed' / rel)
    for filename in ('stage.py', 'verify_stage.py', 'apply_amendment.py'):
        source = (PREVIOUS / filename).read_text()
        source = source.replace('20261001-preparer-names', '20261002-integration-headers')
        source = source.replace('preparer-names-stage-20261001', 'integration-headers-stage-20261002')
        source = source.replace('preparer-names', 'integration-headers')
        source = re.sub(r'^""".*?"""', '"""Scoped active-header object-conflict amendment; preserves all retained nodes.\n'
                        'Use stage.py, verify_stage.py, then apply_amendment.py --apply after a drained pause.\n"""',
                        source, count=1, flags=re.S)
        if filename == 'stage.py':
            source = re.sub(r'CHANGED = .*?\nNEW: set = set\(\)',
                            "CHANGED = ('eval/prepare_integration.py',)\nNEW: set = set()", source, count=1, flags=re.S)
            source = re.sub(r'REVIEWED = \{.*?\n\}',
                            'REVIEWED = ' + repr({rel: (proof['old_sha256'], proof['new_sha256'])}),
                            source, count=1, flags=re.S)
            source = re.sub(r'TESTS = .*?\n# Test-only',
                            "TESTS = ('tests/test_integration_header_conflicts.py', 'tests/test_integration_declarations.py', "
                            "'tests/test_prepare_integration.py')\n# Test-only", source, count=1, flags=re.S)
        if filename == 'apply_amendment.py':
            start = source.index('def fires_ok()')
            end = source.index('\n\ndef lock(', start)
            source = source[:start] + '''def fires_ok() -> bool:
    proof_path = HERE.parents[2] / 'integration-headers-20261002/proof.json'
    try:
        proof = json.loads(proof_path.read_bytes())
        receipt = json.loads(Path(proof['receipt']).read_bytes())
        manifest_path = Path(proof['manifest'])
        return (proof['whole_rom_verified'] and proof['status'] == 'rom_exact'
            and proof['candidate'] == 'checkMainMenuSecretCode'
            and proof['candidate_source_unchanged']
            and proof['original_union_count'] >= 34
            and proof['combined_union_count'] == proof['original_union_count'] + 1
            and proof['new_sha256'] == sha(HERE / 'reviewed/eval/prepare_integration.py')
            and receipt['whole_rom_verified'] and receipt['status'] == 'rom_exact'
            and receipt['manifest_sha256'] == sha(manifest_path))
    except (OSError, ValueError, KeyError):
        return False
''' + source[end:]
            source = source.replace('fires evidence missing or failing: preparer batch rom_exact, relocation_names variants, or field_names win',
                                    'fresh header-conflict ROM-union proof missing or failing')
            source = source.replace('eval/results/hidden-object-20260930/RESULTS.md',
                                    'eval/results/integration-headers-20261002/proof.json')
            source = source.replace('eval/results/resume-pipeline-20260908/revisions/20261002-integration-headers/README.md',
                                    'eval/results/integration-headers-20261002/protocol.json')
            source = re.sub(r"'limits': \('Machinery only\..*?No node, '\s*'receipt or ledger row is changed by this install\.'\)",
                            "'limits': ('Machinery only: active destination headers are probed without reference bodies. "
                            "Conflicting extern objects retain candidate-owned typed accesses; equal or unknown objects stay unchanged. "
                            "Unavailable header context declines the new rewrite while retaining the previous preparation path and recording the gap. Certificates and whole-ROM union remain required. "
                            "No candidate, attempt or node is imported by this install.')", source, count=1, flags=re.S)
        ast.parse(source)
        (NEW / filename).write_text(source)
    print(str(NEW))


if __name__ == '__main__':
    main()
