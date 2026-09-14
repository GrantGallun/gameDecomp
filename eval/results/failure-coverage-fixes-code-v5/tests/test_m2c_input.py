from solver.m2c_input import normalize_o32_registers
from solver import workspace, m2c_input
from types import SimpleNamespace


def test_no_andor_is_opt_in_and_preserves_oracle_input(tmp_path, monkeypatch):
    target = tmp_path / 'target.s'
    original = 'glabel f\nadd.s $fv0, $fa0, $ft0\nendlabel f\n'
    target.write_text(original)
    calls = []

    def run(command, **kwargs):
        from pathlib import Path
        calls.append((command, Path(command[-1]).read_text()))
        return SimpleNamespace(returncode=0, stdout='void f(void) {}', stderr='')

    monkeypatch.setattr(m2c_input.subprocess, 'run', run)
    _, baseline = m2c_input.draft(tmp_path, target)
    _, variant = m2c_input.draft(tmp_path, target, no_andor=True)
    assert '--no-andor' not in calls[0][0]
    assert calls[1][0].count('--no-andor') == 1
    assert calls[0][1] == calls[1][1]
    assert 'add.s $f0, $f12, $f4' in calls[1][1]
    assert baseline['input_sha256'] == variant['input_sha256']
    assert baseline['no_andor'] is False and variant['no_andor'] is True
    assert target.read_text() == original


def test_mixed_o32_aliases_become_numeric_without_changing_other_text():
    original = ('glabel fa0\n.set fv0, $f0\n'
                '/* A00 800A0000 ABCD */ swc1 $fa0, 0($sp) # $fa0 comment\n'
                'add.s $fv0, $f12, $ft0f\nendlabel fa0\n')
    normalized, changes = normalize_o32_registers(original)
    assert 'swc1 $f12, 0($sp) # $fa0 comment' in normalized
    assert 'add.s $f0, $f12, $f5' in normalized
    assert 'glabel fa0\n.set fv0, $f0\n' in normalized
    assert changes == ['$fa0=$f12', '$ft0f=$f5', '$fv0=$f0']
    assert normalize_o32_registers(normalized) == (normalized, [])


def test_wavefront_missing_draft_uses_same_assembly_only_adapter(tmp_path, monkeypatch):
    executable = tmp_path / '.venv/bin/m2c'
    executable.parent.mkdir(parents=True)
    executable.touch()
    ws = tmp_path / 'nonmatchings/f'
    ws.mkdir(parents=True)
    (ws / 'target.s').write_text('glabel f\n')
    monkeypatch.setattr(m2c_input, 'draft', lambda repo, target: (
        SimpleNamespace(returncode=0, stdout='void f(void) {}'), {}))
    assert workspace.m2c_draft(ws) == '#include "common.h"\n\nvoid f(void) {}'
    assert not (ws / 'base.c').exists()
