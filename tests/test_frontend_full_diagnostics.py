from types import SimpleNamespace

from solver import frontend_diagnostics as fd


def test_repair_can_request_full_real_diagnostics_without_changing_default(tmp_path, monkeypatch):
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'textconv.py').write_text('')
    (tools / 'charmap.txt').write_text('')
    diagnostic = "candidate.c:2:6: error: member reference base type 'void' is not a structure or union\n 2 |     a->unk4 = 7;\n" + ' ' * 17000
    monkeypatch.setattr(fd, 'recipe', lambda *args: {'command': ['clang']})
    def run(command, **kwargs):
        if command[0] == 'python3':
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=1, stdout='', stderr=diagnostic)
    monkeypatch.setattr(fd.subprocess, 'run', run)
    source = 'void f(void *a) {\n    a->unk4 = 7;\n}\n'
    default = fd.analyse(source, repo=tmp_path, target='build/src/f.o')
    complete = fd.analyse(source, repo=tmp_path, target='build/src/f.o', full_diagnostics=True)
    assert default['diagnostics_truncated']
    assert len(default['diagnostics']) == 16000
    assert complete['diagnostics'] == diagnostic
    assert not complete['diagnostics_truncated']
    assert complete['errors'] == default['errors']
