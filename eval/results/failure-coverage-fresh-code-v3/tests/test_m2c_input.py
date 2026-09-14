from solver.m2c_input import normalize_o32_registers
from solver import workspace, m2c_input
from types import SimpleNamespace
import pytest


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


def test_structured_pointer_context_and_valid_syntax_are_opt_in(tmp_path, monkeypatch):
    from pathlib import Path
    target=tmp_path/'target.s'
    target.write_text('glabel f\nendlabel f\n')
    calls=[]
    def run(command, **kwargs):
        calls.append((command,Path(command[-1]).read_text()))
        return SimpleNamespace(returncode=0,stdout='extern u16 *slots;',stderr='')
    monkeypatch.setattr(m2c_input.subprocess,'run',run)
    _,meta=m2c_input.draft(tmp_path,target,valid_syntax=True,pointer_globals=(('u16','slots'),))
    assert calls[0][1]=='extern u16 *slots;\n'
    assert '--valid-syntax' in calls[1][0]
    assert meta['pointer_globals']==[('u16','slots')]
    assert meta['valid_syntax'] and meta['oracle_target_unchanged']


@pytest.mark.parametrize('declarations',[
    (('struct GameRecord','slots'),), (('u16','x; void f(void) {}'),),
    (('u16','slots'),('u8','slots'))])
def test_pointer_context_rejects_arbitrary_c_and_duplicates(tmp_path,declarations):
    with pytest.raises(ValueError):
        m2c_input.draft(tmp_path,tmp_path/'absent.s',pointer_globals=declarations)


def test_fixed_word_prototype_context_is_structured_and_recorded(tmp_path,monkeypatch):
    from pathlib import Path
    target=tmp_path/'target.s'
    target.write_text('glabel f\njr ra\nnop')
    seen=[]
    def run(command,**kw):
        seen.append(Path(command[-1]).read_text())
        return SimpleNamespace(returncode=0,stdout='void f(void) {}',stderr='')
    monkeypatch.setattr(m2c_input.subprocess,'run',run)
    prototypes=(('s32','sound',('s16','s16')),)
    _,meta=m2c_input.draft(tmp_path,target,function_prototypes=prototypes)
    assert seen[0]=='extern s32 sound(s16, s16);\n'
    assert meta['function_prototypes']==list(prototypes)


@pytest.mark.parametrize('prototypes',[
    (('s32','x; bad()',()),), (('s64','x',()),), (('s32','x',('void',)),),
    (('void','x',('u32',)*5),), (('void','x',()),('s32','x',()))])
def test_prototype_context_rejects_arbitrary_or_unsupported_declarations(tmp_path,prototypes):
    with pytest.raises(ValueError,match='prototype'):
        m2c_input.draft(tmp_path,tmp_path/'missing.s',function_prototypes=prototypes)
