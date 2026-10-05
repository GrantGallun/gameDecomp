from eval import intake_probe, intake_runners
from solver import frontend_diagnostics


def test_existing_wide_argument_owner_is_reached_from_intake(tmp_path, monkeypatch):
    (tmp_path / 'include').mkdir()
    (tmp_path / 'include/api.h').write_text('int root(u64 value);\n')
    source = '#include "api.h"\nint f(int hi, int lo) {\n    return root(hi, lo);\n}\n'
    assembly = tmp_path / 'target.s'
    assembly.write_text('jal root\nnop\n')
    obj = bytearray(52)
    obj[:6] = b'\x7fELF\x01\x02'
    obj[18:20] = (8).to_bytes(2, 'big')
    obj[36:40] = (0x1000).to_bytes(4, 'big')
    (tmp_path / 'target.o').write_bytes(obj)
    seen = []
    def observe(candidate, **kw):
        assert candidate == source
        seen.append(kw)
        return {'status': 'rejected', 'diagnostics':
            'candidate.c:3:21: error: too many arguments to function call, expected single argument, have 2\n'
            ' 3 |     return root(hi, lo);\n'}
    monkeypatch.setattr(frontend_diagnostics, 'analyse', observe)
    label = 'eval.intake_runners.frontend_abi'
    assert label in intake_probe.SEQUENCE
    result = intake_runners.RUNNERS[label](dict(candidate=source, function='f',
        repo=str(tmp_path), target='build/f.o', workspace=str(tmp_path),
        target_asm_path=str(assembly)), {})
    assert result['changed']
    assert '((u64)(u32)(hi) << 32)' in result['source']
    assert seen[0]['full_diagnostics']
    assert result['detail']['plans'][0]['kind'] == 'o32-u64-argument-pack'

    (tmp_path / 'include/api.h').write_text('int root(int value);\n')
    label = 'eval.intake_runners.call_arity'
    assert label in intake_probe.SEQUENCE
    result = intake_runners.RUNNERS[label](dict(candidate=source, function='f',
        repo=str(tmp_path), target='build/f.o', workspace=str(tmp_path),
        target_asm_path=str(assembly)), {})
    assert result['changed']
    assert '((void)(lo), root(hi))' in result['source']


def test_abi_owner_names_missing_inputs():
    label = 'eval.intake_runners.frontend_abi'
    assert label in intake_runners.RUNNERS
    result = intake_runners.RUNNERS[label]({'candidate': 'void f(void) {}'}, {})
    assert not result['changed']
    assert 'workspace' in result['reason']
